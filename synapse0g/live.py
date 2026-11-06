"""One live run, start to finish, streamed to the dashboard as it happens.

For one request the supplier-check app is:

1. compiled: traced on that request, lifted to SIR, and the graph passes run. What each pass did,
   and what it refused and why, comes from the passes themselves;
2. given its call-level policy: the per-step changes the latest supplier study found and its
   firewall approved on tuning tasks (each with its verdict, including the refused ones);
3. run as written: the LangGraph app, step after step, on real 0G calls;
4. run compiled: the optimized graph with that policy, on real 0G calls, in test mode, so the
   irreversible step (placing the order) is stubbed and never executed;
5. compared: time, tokens, hidden reasoning, 0G's billed cost and whether the answer is right,
   for this one request. The firewall's verdict over held-out tasks is shown beside it, from its
   report, because one request cannot make a verdict.

Every number is measured on this run, except the held-out verdict, which is labelled as such.
"""

from __future__ import annotations

import json
import shutil
import threading
import time
import traceback
import uuid
from dataclasses import dataclass, field

from synapse0g.registry import ROOT

CACHE_DIR = ROOT / "data" / "live_cache"
RUNS: dict[str, LiveRun] = {}
_START_LOCK = threading.Lock()
_COMPILED: dict = {}  # the compiled app, kept for later runs: compiling traces the app with real calls


@dataclass
class LiveRun:
    id: str
    task_id: str
    events: list[dict] = field(default_factory=list)
    done: bool = False
    t0: float = field(default_factory=time.perf_counter)
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def emit(self, type_: str, **data) -> None:
        with self.lock:
            self.events.append({"type": type_, "t": round(time.perf_counter() - self.t0, 3), **data})

    def since(self, n: int) -> dict:
        with self.lock:
            return {"id": self.id, "task_id": self.task_id, "events": self.events[n:], "next": len(self.events), "done": self.done}


def tasks() -> list[dict]:
    """Held-out supplier requests: the study never tuned on them."""
    from synapse0g.agents import supplier

    out = []
    for t in supplier.split("test", 20):
        rfq = json.loads(t.question)
        out.append({"id": t.id, "rfq": rfq, "label": f"{rfq['rfq_id']}: {rfq['quantity']} x {rfq['item']}"})
    return out


def start(task_id: str | None = None, cold: bool = False) -> LiveRun:
    with _START_LOCK:
        if any(not r.done for r in RUNS.values()):
            raise RuntimeError("a live run is already in progress: one at a time, so their timings do not share the provider")
        task_id = task_id or tasks()[0]["id"]
        run = LiveRun("live-" + uuid.uuid4().hex[:8], task_id)
        RUNS[run.id] = run
        threading.Thread(target=_run, args=(run, cold), daemon=True).start()
        return run


def _report():
    paths = sorted((ROOT / "reports").glob("supplier_*.json"))
    return (json.loads(paths[-1].read_text(encoding="utf-8")), paths[-1].name) if paths else (None, None)


def _call(rec, arm: str) -> dict:
    d = rec.as_dict()
    keep = ("step", "step_label", "model_id", "tag", "status", "latency_s", "tokens_in", "tokens_out", "reasoning_tokens",
            "cached_tokens", "billed_usd", "listed_cost_usd", "cost_usd", "request_id", "techniques", "started_at", "error")
    return {"arm": arm, **{k: d.get(k) for k in keep}}


class _Watch:
    """Streams a session's calls as each one finishes."""

    def __init__(self, run: LiveRun, session, arm: str):
        self.run, self.session, self.arm, self.n = run, session, arm, 0
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()

    def _flush(self):
        records = list(self.session.records)
        for rec in records[self.n:]:
            self.run.emit("call", **_call(rec, self.arm))
        self.n = len(records)

    def _loop(self):
        while not self.stop.wait(0.2):
            self._flush()

    def close(self):
        self.stop.set()
        self.thread.join()
        self._flush()


def _summary(records, wall_s: float, answer: dict | None, passed: bool, order_placed: bool) -> dict:
    ok = [r for r in records if r.status in ("ok", "reused")]
    billed = [r.billed_usd for r in ok if r.billed_usd is not None]
    return {"wall_s": round(wall_s, 3), "calls": len(ok), "reused_calls": sum(r.status == "reused" for r in ok),
            "tokens_in": sum(r.tokens_in or 0 for r in ok), "tokens_out": sum(r.tokens_out or 0 for r in ok),
            "reasoning_tokens": sum(r.reasoning_tokens or 0 for r in ok),
            "billed_usd": round(sum(billed), 6) if len(billed) == len([r for r in ok if r.status == "ok"]) else None,
            "listed_usd": round(sum(r.listed_cost_usd or 0 for r in ok), 6),
            "errors": [r.error for r in records if r.status == "error"],
            "answer": answer, "passed": passed, "order_placed": order_placed}


def _run(run: LiveRun, cold: bool) -> None:
    import synapse0g
    from synapse0g.agents import supplier
    from synapse0g.graph.runtime.executor import ExecutionMode
    from synapse0g.openai import routed
    from synapse0g.optimizer import Policy, StepPolicy
    from synapse0g.proxy import Engine, Proxy
    from synapse0g.registry import load_env, load_registry
    from synapse0g.study import _passes
    from synapse0g.tasks import CHECKERS

    try:
        load_env()  # the dashboard's server does not read .env by itself; keys are never sent to the page
        registry = load_registry()
        engine = Engine(registry)
        ex = supplier.example()
        task = next(t for t in supplier.load_supplier() if t.id == run.task_id)
        rfq = json.loads(task.question)
        check = CHECKERS["supplier"]
        model = registry.model(ex.MODEL)
        run.emit("task", rfq=rfq, model=model.id, tag=model.tag, provider=model.provider,
                 steps=["load_policy", "get_quotes", "screen_reviews", "check_certificates", "decide", "place_order"],
                 effects=ex.EFFECTS, held_out=True)
        if cold and CACHE_DIR.exists():
            shutil.rmtree(CACHE_DIR)

        with Proxy(engine) as px:
            # 1. compile (once; a later run reuses it unless asked to start cold)
            if cold or "app" not in _COMPILED:
                run.emit("phase", phase="compile", text="Tracing the app on this request and lifting it to SIR")
                trace = engine.open()
                watch = _Watch(run, trace, "compile")
                t = time.perf_counter()
                try:
                    with routed(px.base_url, trace.id):
                        app = synapse0g.compile(ex.builder, sample={"rfq": rfq}, effects=ex.EFFECTS, name="supplier_check",
                                                cache_dir=CACHE_DIR, cache_scope="live-demo")
                finally:
                    watch.close()
                    engine.close(trace.id)
                _COMPILED.update(app=app, seconds=round(time.perf_counter() - t, 2), on=run.task_id, at=time.time())
                reused = False
            else:
                app, reused = _COMPILED["app"], True
                run.emit("phase", phase="compile", text=f"Using the graph compiled on {_COMPILED['on']}: the same app, so the same SIR")
            run.emit("compiled", seconds=_COMPILED["seconds"], reused=reused, compiled_on=_COMPILED["on"], compiled_at=_COMPILED["at"],
                     before=app.before.to_ui(), after=app.after.to_ui(), passes=_passes(app), applied=app.applied,
                     explain=app.explain(), order=app.before.meta.get("declared_order"))

            # 2. the call-level policy, as the study found and approved it
            report, name = _report()
            steps = (report or {}).get("search", {}).get("policy", {})
            policy = Policy({k: StepPolicy(**v) for k, v in steps.items()})
            labels = (report or {}).get("steps", {})
            trials = [{"step": tr["candidate"]["step"], "label": labels.get(tr["candidate"]["step"]),
                       "technique": tr["candidate"]["technique"], "why": tr["candidate"].get("why"),
                       "approve": tr["verdict"]["approve"], "reasons": tr["verdict"]["reasons"],
                       "cost_gain": tr["verdict"].get("cost_gain"), "latency_gain": tr["verdict"].get("latency_gain"),
                       "passes": tr["verdict"].get("passes")} for tr in (report or {}).get("search", {}).get("trials", [])]
            ev = (report or {}).get("evaluation", {})
            run.emit("policy", report=name, steps={k: {"label": labels.get(k), "techniques": StepPolicy(**v).techniques()}
                                                   for k, v in steps.items()}, trials=trials,
                     held_out={"verdict": ev.get("verdict"), "baseline": ev.get("baseline_1"), "optimized": ev.get("optimized"),
                               "tasks": (report or {}).get("test_n")})

            # 3. both at once, as the studies run them: the same request, the same moment, the same provider
            run.emit("phase", phase="race", text="Running the app as written and the compiled graph at the same moment, on the same request")
            results: dict = {}

            def as_written_arm():
                session = engine.open(Policy.empty())
                watch = _Watch(run, session, "as_written")
                t = time.perf_counter()
                state, error = None, None
                order = list(ex.builder.nodes)
                seen = [0]
                try:
                    with routed(px.base_url, session.id):
                        run.emit("step", arm="as_written", node=order[0], state="running")
                        state = {}
                        for update in ex.builder.compile().stream({"rfq": rfq}, stream_mode="updates"):
                            for node, value in update.items():
                                state.update(value or {})
                                # the app runs one step at a time, so the calls made since the last step are this step's
                                made = list(session.records)[seen[0]:]
                                seen[0] += len(made)
                                run.emit("step", arm="as_written", node=node, state="done", calls=sorted({r.step for r in made}))
                                nxt = order.index(node) + 1 if node in order else len(order)
                                if nxt < len(order):
                                    run.emit("step", arm="as_written", node=order[nxt], state="running")
                except Exception as e:  # shown on the run, never hidden
                    error = f"{type(e).__name__}: {e}"
                finally:
                    watch.close()
                    records = engine.close(session.id).records
                wall = time.perf_counter() - t
                answer = (state or {}).get("order")
                placed = bool((state or {}).get("receipt", {}).get("po_number"))
                a = _summary(records, wall, answer, error is None and check(task, json.dumps(answer or {})), placed)
                a["error"] = error
                a["receipt"] = (state or {}).get("receipt")
                run.emit("arm_done", arm="as_written", summary=a)
                results["as_written"] = a

            def compiled_arm():
                session = engine.open(policy)
                watch = _Watch(run, session, "compiled")
                t = time.perf_counter()
                execution, error = None, None
                try:
                    with routed(px.base_url, session.id):
                        execution = app.run({"rfq": rfq}, mode=ExecutionMode.BENCH,
                                            on_event=lambda e: run.emit("node", arm="compiled", event=e["type"],
                                                                     **{k: v for k, v in e.items() if k != "type"}))
                    if execution.crashed:
                        error = execution.error or "crashed"
                except Exception as e:
                    error = f"{type(e).__name__}: {e}"
                finally:
                    watch.close()
                    records = engine.close(session.id).records
                wall = time.perf_counter() - t
                out = (execution.outputs.get("result") if execution and not error else None) or {}
                answer = out.get("order")
                c = _summary(records, wall, answer, error is None and check(task, json.dumps(answer or {})), False)
                c["error"] = error
                c["cache_hits"] = execution.cache_hits if execution else 0
                run.emit("arm_done", arm="compiled", summary=c)
                results["compiled"] = c

            arms = [threading.Thread(target=f) for f in (as_written_arm, compiled_arm)]
            for th in arms:
                th.start()
            for th in arms:
                th.join()
            a, c = results.get("as_written"), results.get("compiled")
            if a is None or c is None:
                raise RuntimeError("an arm did not finish: see the server log")
            run.emit("result", as_written=a, compiled=c,
                     same_decision=(a["answer"] or {}).get("supplier_id") == (c["answer"] or {}).get("supplier_id"))
    except Exception as e:  # the run says it failed and why
        traceback.print_exc()
        run.emit("failed", error=f"{type(e).__name__}: {e}")
    finally:
        run.done = True
