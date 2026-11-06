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


# The agents a live run can use. `graph`: compiled to SIR, run as written and compiled side by side.
# `loop`: a ReAct agent (one model step, repeated with tools): no graph to rewrite; only call-level
# changes apply, and only those its study's firewall approved.
AGENTS = {
    "supplier": {"label": "Supplier check", "kind": "graph", "framework": "LangGraph StateGraph, six steps",
                 "task_noun": "request"},
    "procurement": {"label": "Procurement quotations", "kind": "graph", "framework": "plain Python, eight steps, no framework",
                    "task_noun": "purchase request"},
    "research": {"label": "Research assistant", "kind": "loop", "framework": "LangGraph ReAct agent with a search tool (HotpotQA)",
                 "task_noun": "question"},
    "math": {"label": "Maths solver", "kind": "loop", "framework": "LangGraph ReAct agent with a calculator (GSM8K)",
             "task_noun": "problem"},
}


@dataclass
class LiveRun:
    id: str
    task_id: str
    agent: str = "supplier"
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


def _meta(agent: str) -> dict:
    m = AGENTS[agent]
    return {"agent_label": m["label"], "kind": m["kind"], "framework": m["framework"], "task_noun": m["task_noun"]}


def agents() -> list[dict]:
    return [{"id": k, **v} for k, v in AGENTS.items()]


def tasks(agent: str = "supplier") -> list[dict]:
    """Held-out tasks of the agent: its study never tuned on them."""
    if agent not in AGENTS:
        raise ValueError(f"no live agent {agent!r}: {', '.join(AGENTS)}")
    out = []
    for t in _task_set(agent):
        out.append({"id": t.id, **_describe(agent, t)})
    return out


def _task_set(agent: str):
    if agent == "supplier":
        from synapse0g.agents import supplier

        return supplier.split("test", 20)
    if agent == "procurement":
        from synapse0g.agents import procurement

        return procurement.split("test", 18)
    from synapse0g.tasks import load_gsm8k, load_hotpot, split_of

    every = load_hotpot() if agent == "research" else load_gsm8k()
    return [t for t in every if split_of(t.id) == "test"][:20]


def _describe(agent: str, t) -> dict:
    if agent == "supplier":
        rfq = json.loads(t.question)
        return {"rfq": rfq, "label": f"{rfq['rfq_id']}: {rfq['quantity']} x {rfq['item']}",
                "summary": f"{rfq['rfq_id']}: {rfq['quantity']} x {rfq['item']}, at most {rfq['max_unit_price']} each, "
                           f"within {rfq['needed_within_days']} days"}
    if agent == "procurement":
        from synapse0g.agents import procurement

        sid = json.loads(t.question)["scenario"]
        args = procurement.inputs(t)
        first = next((ln.strip() for ln in args["request"].splitlines() if ln.strip()), "")
        return {"label": f"{sid}: {first[:70]}",
                "summary": f"{sid}: {first[:140]} ({len(args['quotation_paths'])} supplier quotations)"}
    q = t.question.strip().replace("\n", " ")
    return {"label": q[:90] + ("…" if len(q) > 90 else ""), "summary": q[:400]}


def start(task_id: str | None = None, cold: bool = False, agent: str = "supplier") -> LiveRun:
    if agent not in AGENTS:
        raise ValueError(f"no live agent {agent!r}: {', '.join(AGENTS)}")
    with _START_LOCK:
        if any(not r.done for r in RUNS.values()):
            raise RuntimeError("a live run is already in progress: one at a time, so their timings do not share the provider")
        task_id = task_id or tasks(agent)[0]["id"]
        run = LiveRun("live-" + uuid.uuid4().hex[:8], task_id, agent)
        RUNS[run.id] = run
        threading.Thread(target=_run, args=(run, cold), daemon=True).start()
        return run


def _report(agent: str = "supplier"):
    paths = sorted((ROOT / "reports").glob(f"{agent}_zg-main_*.json"))
    return (json.loads(paths[-1].read_text(encoding="utf-8")), paths[-1].name) if paths else (None, None)


def policy_from_report(agent: str = "supplier"):
    """The call-level policy the agent's latest study found and its firewall approved, with that report."""
    from synapse0g.optimizer import Policy, StepPolicy

    report, name = _report(agent)
    steps = (report or {}).get("search", {}).get("policy", {})
    return Policy({k: StepPolicy(**v) for k, v in steps.items()}), report, name


def _call(rec, arm: str) -> dict:
    d = rec.as_dict()
    keep = ("step", "step_label", "model_id", "tag", "status", "latency_s", "tokens_in", "tokens_out", "reasoning_tokens",
            "cached_tokens", "billed_usd", "listed_cost_usd", "cost_usd", "request_id", "techniques", "started_at", "error",
            "billed_0g", "hold_id", "sent_at", "provider")
    return {"arm": arm, **{k: d.get(k) for k in keep}}


def _emit_policy(run: LiveRun, agent: str):
    """The approved call-level policy, every trial with its verdict, and the held-out verdict, from the report."""
    from synapse0g.optimizer import StepPolicy

    policy, report, name = policy_from_report(agent)
    steps = (report or {}).get("search", {}).get("policy", {})
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
    return policy


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
    if run.agent != "supplier":
        return _run_other(run, cold)
    import synapse0g
    from synapse0g.agents import supplier
    from synapse0g.graph.runtime.executor import ExecutionMode
    from synapse0g.openai import routed
    from synapse0g.optimizer import Policy
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
        run.emit("task", agent="supplier", **_meta("supplier"), **_describe("supplier", task), model=model.id, tag=model.tag, provider=model.provider,
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
            policy = _emit_policy(run, "supplier")

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
            a["short"], c["short"] = (a["answer"] or {}).get("supplier_id"), (c["answer"] or {}).get("supplier_id")
            run.emit("result", as_written=a, compiled=c, same_decision=a["short"] == c["short"])
    except Exception as e:  # the run says it failed and why
        traceback.print_exc()
        run.emit("failed", error=f"{type(e).__name__}: {e}")
    finally:
        run.done = True


def _short(agent: str, answer) -> str | None:
    """The answer in a few words, for comparing the two versions."""
    if answer is None:
        return None
    if agent == "procurement":
        dec = (answer if isinstance(answer, dict) else {}).get("decision") or {}
        return " · ".join(str(x) for x in (dec.get("status"), dec.get("supplier")) if x) or None
    text = str(answer).strip()
    line = next((ln for ln in reversed(text.splitlines()) if ln.lower().startswith("answer")), text.splitlines()[-1] if text else "")
    return line.split(":", 1)[-1].strip()[:80] or None


def _run_other(run: LiveRun, cold: bool) -> None:
    """A live run of the procurement agent (compiled graph) or a ReAct agent (call-level changes only)."""
    import synapse0g
    from synapse0g.graph.runtime.executor import ExecutionMode
    from synapse0g.openai import routed
    from synapse0g.optimizer import Policy
    from synapse0g.proxy import Engine, Proxy
    from synapse0g.registry import load_env, load_registry
    from synapse0g.study import _passes
    from synapse0g.tasks import CHECKERS

    agent = run.agent
    try:
        load_env()
        registry = load_registry()
        engine = Engine(registry)
        model = registry.model("zg-main")
        task = next(t for t in _task_set(agent) if t.id == run.task_id)
        check = CHECKERS[agent]
        run.emit("task", agent=agent, **_meta(agent), **_describe(agent, task), model=model.id, tag=model.tag, provider=model.provider,
                 held_out=True)
        cache_dir = CACHE_DIR / agent
        if cold and cache_dir.exists():
            shutil.rmtree(cache_dir)

        app = None
        if agent == "procurement":
            from synapse0g.agents import procurement

            ex = procurement.example()
            key = f"app:{agent}"
            if cold or key not in _COMPILED:
                run.emit("phase", phase="compile", text="Reading the function's steps and lifting them to SIR")
                t = time.perf_counter()
                compiled = synapse0g.compile(ex.handle_request, effects=ex.EFFECTS, name="procurement", cache_dir=cache_dir,
                                             cache_scope="live-demo")
                _COMPILED[key] = {"app": compiled, "seconds": round(time.perf_counter() - t, 2), "on": run.task_id, "at": time.time()}
                reused = False
            else:
                reused = True
                run.emit("phase", phase="compile", text=f"Using the graph compiled on {_COMPILED[key]['on']}: the same code, so the same SIR")
            c = _COMPILED[key]
            app = c["app"]
            run.emit("compiled", seconds=c["seconds"], reused=reused, compiled_on=c["on"], compiled_at=c["at"],
                     before=app.before.to_ui(), after=app.after.to_ui(), passes=_passes(app), applied=app.applied,
                     explain=app.explain(), order=app.before.meta.get("declared_order"))
        else:
            run.emit("phase", phase="compile", text="A ReAct agent is one model step repeated with tools: no graph to rewrite. "
                                                    "Only call-level changes apply, and only those its firewall approved.")

        policy = _emit_policy(run, agent)
        both = app is not None or bool(policy.steps)
        run.emit("phase", phase="race", text="Running both versions at the same moment, on the same task" if both else
                 "The study approved no change for this agent, so it ships unchanged: running it once, live")
        results: dict = {}

        def arm(name: str, arm_policy, body):
            session = engine.open(arm_policy)
            watch = _Watch(run, session, name)
            t = time.perf_counter()
            answer, error, extra = None, None, {}
            try:
                with routed(px.base_url, session.id):
                    answer, extra = body(session)
            except Exception as e:  # shown on the run, never hidden
                error = f"{type(e).__name__}: {e}"[:500]
            finally:
                watch.close()
                records = engine.close(session.id).records
            wall = time.perf_counter() - t
            text = answer if isinstance(answer, str) else json.dumps(answer or {}, default=str)
            summary = _summary(records, wall, answer, error is None and answer is not None and check(task, text), False)
            summary.update(error=error, short=_short(agent, answer), **extra)
            run.emit("arm_done", arm=name, summary=summary)
            results[name] = summary

        if agent == "procurement":
            from synapse0g.agents import procurement

            args = procurement.inputs(task)

            def as_written(session):
                return ex.handle_request(**args), {"filed": "not filed: PROCUREMENT_STAGING makes filing a no-op in a comparison"}

            def compiled_body(session):
                execution = app.run(args, mode=ExecutionMode.BENCH, on_event=lambda e: run.emit(
                    "node", arm="compiled", event=e["type"], **{k: v for k, v in e.items() if k != "type"}))
                if execution.crashed:
                    raise RuntimeError(execution.error or "crashed")
                return execution.outputs.get("result"), {"cache_hits": execution.cache_hits}

            arms = [("as_written", Policy.empty(), as_written), ("compiled", policy, compiled_body)]
        else:
            from synapse0g.agents.langgraph_agents import AGENTS as LOOPS

            def loop(session):
                res = LOOPS[agent](task, px.base_url, session.id, model.id)
                if res.error:
                    raise RuntimeError(res.error)
                return res.answer, {}

            arms = [("as_written", Policy.empty(), loop)] + ([("compiled", policy, loop)] if policy.steps else [])

        with Proxy(engine) as px:
            threads = [threading.Thread(target=arm, args=a) for a in arms]
            for th in threads:
                th.start()
            for th in threads:
                th.join()
        a, c = results.get("as_written"), results.get("compiled")
        if a is None or (len(arms) == 2 and c is None):
            raise RuntimeError("an arm did not finish: see the server log")
        run.emit("result", as_written=a, compiled=c, unchanged=len(arms) == 1,
                 same_decision=None if c is None else a["short"] == c["short"])
    except Exception as e:  # the run says it failed and why
        traceback.print_exc()
        run.emit("failed", error=f"{type(e).__name__}: {e}")
    finally:
        run.done = True
