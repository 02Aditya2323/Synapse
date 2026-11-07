"""synapse0g as a paid agent: it takes optimization jobs, prices them, and pays for its own calls.

A job is one of three kinds:

- `run`: one request through the compiled supplier-check app, every model call paid from the
  agent's own 0G account (`agent-main`/`agent-lite`, the capped key) under a cap per job, two calls
  at a time, each reserved first (`synapse0g.spend`). The smallest paid unit of work.

- `inspect`: any agent's code, read and drawn as a graph with the passes' plan. Static: no model
  is called, so it costs nothing and runs in any budget mode.
- `study`: a full study of an agent that has a task set (`synapse0g.study`): compile, graph
  verdict, call-level search, held-out measurement. Every model call it makes is paid from the
  agent's own balance, under a hard cap per job.

Before every job the agent reads its balance and chooses how to work, so it slows down rather than
crashing when money runs low:

    full      the whole study, at its quoted size
    reduced   a smaller study (fewer tasks and trials); the report says it is smaller
    static    inspect only; study jobs wait in the queue, and their status says why
    paused    nothing that costs money runs; the queue is kept

The balance comes from a `Balance` source. `Ledger` is a local account of deposits and spend, used
until the agent's 0G compute account is connected; swapping the source changes nothing else.
Nothing here moves money: payment arrives later, through the launchpad.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
import traceback
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from synapse0g.registry import ROOT

DB = ROOT / "data" / "service.db"
MARGIN = 1.5  # price = estimated provider cost x margin
# thresholds as multiples of the job's estimated cost
FULL_AT, REDUCED_AT = 1.5, 0.5
RESERVE_USD = 0.05  # never spend the last of the balance: it keeps status and inspect jobs answering

# the agent's own account pays for these: the models it asks for, mapped to the same models on its key
PAY_FROM = {"zg-main": "agent-main", "zg-lite": "agent-lite"}
RUN_CAP_0G = float(os.environ.get("SYNAPSE0G_RUN_CAP_0G", "0.02"))
_PAID: dict = {}  # the supplier app compiled on the agent's account, kept for later jobs
PROGRESS: dict = {}  # job id -> live.LiveRun: a paid job's calls and steps as they happen


class Paused(RuntimeError):
    """The job stopped because the account or a cap refused a call: it waits, it did not fail."""


# study sizes per mode: tuning tasks, held-out tasks, call-level trials
SIZES = {"full": (8, 18, 6), "reduced": (4, 8, 2)}


class Balance(Protocol):
    def available_usd(self) -> float: ...
    def spend(self, usd: float, note: str) -> None: ...


class Ledger:
    """Deposits and spend, kept locally. The stand-in for the 0G compute account."""

    def __init__(self, db: Path = DB):
        self.db = db
        db.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as c:
            c.execute("create table if not exists ledger (at real, usd real, note text)")

    def _conn(self):
        return sqlite3.connect(self.db)

    def available_usd(self) -> float:
        with self._conn() as c:
            return float(c.execute("select coalesce(sum(usd), 0) from ledger").fetchone()[0])

    def deposit(self, usd: float, note: str) -> None:
        with self._conn() as c:
            c.execute("insert into ledger values (?, ?, ?)", (time.time(), usd, note))

    def spend(self, usd: float, note: str) -> None:
        with self._conn() as c:
            c.execute("insert into ledger values (?, ?, ?)", (time.time(), -usd, note))

    def entries(self, n: int = 50) -> list[dict]:
        with self._conn() as c:
            return [{"at": a, "usd": u, "note": t} for a, u, t in
                    c.execute("select at, usd, note from ledger order by at desc limit ?", (n,))]


class RouterBalance:
    """The agent's 0G Router account, the account its compute key spends from, read live. Its balance
    is in 0G; it is valued in USD at the rate the 0G catalog implies, so job quotes compare with it.
    Spend is not written here: the Router records every billed call itself."""

    def __init__(self, account, usd_per_0g: float):
        self.account, self.usd_per_0g = account, usd_per_0g

    def available_usd(self) -> float:
        return self.account.balance()["spendable_0g"] * self.usd_per_0g

    def spend(self, usd: float, note: str) -> None:
        pass  # already taken from the account by the Router, call by call


def inference_receipts(limit: int = 12) -> dict:
    """The agent's own 0G-billed model calls, from every study report: count, billed cost and the
    latest request ids. Only calls 0G billed are counted; the reports keep the search trials' calls in
    their spend totals but not as records, so this is a floor on what was spent, not the total."""
    calls, billed, latest, seen = 0, 0.0, [], set()
    for path in sorted((ROOT / "reports").glob("*_*.json")):
        doc = json.loads(path.read_text(encoding="utf-8"))
        for runs in doc.get("runs", {}).values():
            for t in runs.values():
                for r in t["records"]:
                    # a reused call is recorded again under its first request id: count it once
                    if r.get("status") != "ok" or r.get("billed_usd") is None or r.get("request_id") in seen:
                        continue
                    seen.add(r.get("request_id"))
                    calls += 1
                    billed += r.get("billed_usd") or 0.0
                    if r.get("request_id"):
                        latest.append({"request_id": r["request_id"], "billed_usd": r.get("billed_usd"),
                                       "model": r.get("model"), "report": path.name})
    return {"calls": calls, "billed_usd": round(billed, 6), "latest": latest[-limit:][::-1]}


# ---- studies a job can run: an agent with a task set and a checker ------------------------------
def _study_spec(agent: str, calib: int, test: int):
    if agent == "procurement":
        from synapse0g.agents.procurement import spec

        return spec(calib, test)
    raise ValueError(f"no task set for {agent!r}: study jobs run on agents with tasks; inspect works on any code")


STUDY_AGENTS = {"procurement": {"model_steps": 2, "per_item_steps": 1, "items": 3.5, "usd_per_call": 0.0006}}


def measured_cost_per_call(agent: str) -> float | None:
    """Mean billed cost per model call in the agent's latest study, if one has run."""
    for path in sorted((ROOT / "reports").glob(f"{agent}_*.json"), reverse=True):
        doc = json.loads(path.read_text(encoding="utf-8"))
        costs = [r.get("cost_usd") for runs in doc.get("runs", {}).values() for t in runs.values() for r in t["records"]
                 if r.get("status") == "ok" and r.get("cost_usd") is not None]
        if costs:
            return sum(costs) / len(costs)
    return None


def estimate(agent: str, size: str) -> dict:
    """Provider cost of a study before it runs: its runs x calls per run x cost per call, measured in
    the agent's last study where there is one."""
    a = STUDY_AGENTS[agent]
    calib, test, trials = SIZES[size]
    runs = calib * 3 + calib * (2 + 2 * trials + 1) + test * 4  # graph stage, search (with a recheck), held-out
    calls = runs * (a["model_steps"] + a["per_item_steps"] * a["items"])
    measured = measured_cost_per_call(agent)
    per_call = measured if measured is not None else a["usd_per_call"]
    usd = calls * per_call
    return {"size": size, "tuning_tasks": calib, "held_out_tasks": test, "trials": trials, "runs": runs,
            "calls": round(calls), "usd_per_call": round(per_call, 7),
            "per_call_source": "measured in the last study" if measured is not None else "assumed: no study has run yet",
            "provider_usd": round(usd, 4), "price_usd": round(usd * MARGIN, 4)}


def mode_for(available: float, job_cost: float) -> str:
    spendable = available - RESERVE_USD
    if spendable <= 0:
        return "paused"
    if spendable >= job_cost * FULL_AT:
        return "full"
    if spendable >= job_cost * REDUCED_AT:
        return "reduced"
    return "static"


DELIVERIES = "deliveries"


def deliver(job, app, policy, report_doc: dict | None, report: str | None, result: dict, *, ex=None, registry=None,
            inputs: dict | None = None) -> str:
    """The optimized agent, returned to the customer as code: `optimized_agent.py`, generated from the
    optimized SIR and the approved call policy (synapse0g.codegen), beside their original code, both graphs,
    the policy, what changed and why, and this run's evidence. A folder and a zip under data/deliveries/."""
    from synapse0g import codegen
    from synapse0g.agents import supplier

    ex = ex or supplier.example()
    ev = (report_doc or {}).get("evaluation", {})
    labels = (report_doc or {}).get("steps", {})
    refused = [{"step": t["candidate"]["step"], "label": labels.get(t["candidate"]["step"]), "technique": t["candidate"]["technique"],
                "reasons": t["verdict"]["reasons"]} for t in (report_doc or {}).get("search", {}).get("trials", [])
               if not t["verdict"]["approve"]]
    ui_after, ui_before = app.after.to_ui(), app.before.to_ui()
    nodes = [n["id"] for n in ui_after["nodes"]]
    reasoning = {"*": dict(registry.model(ex.MODEL).reasoning_off) if registry else {}}
    if registry:
        reasoning.update({m.id: dict(m.reasoning_off) for m in registry.models.values() if m.reasoning_off})
    module = Path(supplier.APP_PATH).stem
    code = codegen.generate_langgraph(
        module=module, state_class=ex.builder.state_schema.__name__, builder_attr="builder", ui_after=ui_after,
        policy=policy.as_dict(), step_labels=labels, reasoning_off=reasoning, refused=refused, verdict=ev.get("verdict"),
        job=job.id, report=report, sample_input=inputs or {}, functions=codegen.module_functions(ex, nodes))
    protected = [n["id"] for n in ui_after["nodes"] if n.get("effect") in ("external", "irreversible")]
    run = {k: v for k, v in result.items() if k != "package"}
    files = {
        "optimized_agent.py": code,
        f"{module}.py": Path(supplier.APP_PATH).read_text(encoding="utf-8"),
        "sir_before.json": json.dumps(ui_before, indent=1, default=str),
        "sir_after.json": json.dumps(ui_after, indent=1, default=str),
        "policy.json": json.dumps({"approved": policy.as_dict(), "described": policy.describe(), "refused": refused,
                                   "from_study": report, "held_out_verdict": ev.get("verdict")}, indent=1, default=str),
        "OPTIMIZATION.md": codegen.optimization_md(explain=app.explain(), policy_described=policy.describe(), refused=refused,
                                                   run=run, paid={"tx": job.spec.get("payment_tx"), "quote": job.spec.get("quote")}),
        "README.md": codegen.readme(job=job.id, module=module, applied=app.applied, protected=protected, verdict=ev.get("verdict")),
        "input.json": json.dumps(inputs or {}, indent=1),
        "run.json": json.dumps(run, indent=1, default=str),
    }
    zpath = codegen.write_pack(ROOT / "data" / DELIVERIES / f"synapse0g-optimized-{job.id}", files)
    result["optimized_code"] = code
    return str(zpath.relative_to(ROOT))


def deliver_custom(job, app, module, target: dict, path: Path, result: dict, sample: dict) -> str:
    """The customer's optimized agent as code, beside their original, both graphs and this run's evidence."""
    from synapse0g import codegen
    from synapse0g.optimizer import Policy

    ui_after, ui_before = app.after.to_ui(), app.before.to_ui()
    nodes = [n["id"] for n in ui_after["nodes"]]
    if target["kind"] == "langgraph":
        code = codegen.generate_langgraph(
            module=path.stem, state_class=target["target"].state_schema.__name__, builder_attr=target["name"], ui_after=ui_after,
            policy={}, step_labels={}, reasoning_off={}, refused=[], verdict=None, job=job.id, report=None, sample_input=sample,
            functions=codegen.module_functions(module, nodes))
    else:
        code = codegen.generate_function(module=path.stem, entry=target["name"], job=job.id)
    protected = [n["id"] for n in ui_after["nodes"] if n.get("effect") in ("external", "irreversible")]
    run = {k: v for k, v in result.items() if k != "package"}
    files = {
        "optimized_agent.py": code,
        f"{path.stem}.py": path.read_text(encoding="utf-8"),
        "sir_before.json": json.dumps(ui_before, indent=1, default=str),
        "sir_after.json": json.dumps(ui_after, indent=1, default=str),
        "OPTIMIZATION.md": codegen.optimization_md(explain=app.explain(), policy_described=Policy({}).describe(), refused=[],
                                                   run=run, paid={"tx": job.spec.get("payment_tx"), "quote": job.spec.get("quote")}),
        "README.md": codegen.readme(job=job.id, module=path.stem, applied=app.applied, protected=protected, verdict=None),
        "input.json": json.dumps(sample, indent=1, default=str),
        "run.json": json.dumps(run, indent=1, default=str),
    }
    zpath = codegen.write_pack(ROOT / "data" / DELIVERIES / f"synapse0g-optimized-{job.id}", files)
    result["optimized_code"] = code
    return str(zpath.relative_to(ROOT))


def peak_overlap(records) -> int:
    """The most calls in flight at one moment: from when each request left to when its reply came back."""
    edges = []
    for r in records:
        if r.status == "ok" and r.sent_at is not None:
            edges += [(r.sent_at, 1), (r.started_at + r.latency_s, -1)]
    peak = live = 0
    for _, d in sorted(edges, key=lambda e: (e[0], e[1])):
        live += d
        peak = max(peak, live)
    return peak


# ---- jobs --------------------------------------------------------------------------------------
@dataclass
class Job:
    id: str
    kind: str  # inspect | study
    spec: dict
    status: str = "queued"  # queued | running | done | failed | waiting
    created: float = 0.0
    quote: dict | None = None
    mode: str | None = None
    spent_usd: float = 0.0
    result: dict | None = None
    note: str = ""


class Queue:
    def __init__(self, db: Path = DB):
        self.db = db
        db.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(db) as c:
            c.execute("create table if not exists jobs (id text primary key, body text, created real)")
        self.lock = threading.Lock()

    def put(self, job: Job) -> None:
        with self.lock, sqlite3.connect(self.db) as c:
            c.execute("insert or replace into jobs values (?, ?, ?)", (job.id, json.dumps(job.__dict__, default=str), job.created))

    def get(self, job_id: str) -> Job | None:
        with sqlite3.connect(self.db) as c:
            row = c.execute("select body from jobs where id = ?", (job_id,)).fetchone()
        return Job(**json.loads(row[0])) if row else None

    def all(self) -> list[Job]:
        with sqlite3.connect(self.db) as c:
            return [Job(**json.loads(b)) for (b,) in c.execute("select body from jobs order by created")]


def submit(queue: Queue, kind: str, spec: dict) -> Job:
    if kind not in ("inspect", "study", "run"):
        raise ValueError("kind is inspect, study or run")
    if kind == "run":
        if spec.get("agent", "supplier") not in ("supplier", "custom"):
            raise ValueError("run jobs run the supplier-check app or the customer's own agent")
        job = Job(id="job-" + uuid.uuid4().hex[:10], kind=kind, spec={"agent": "supplier", **spec}, created=time.time())
        job.quote = {"cap_0g": float(spec.get("cap_0g", RUN_CAP_0G)), "note": "one supplier request, compiled, paid by the agent's account"}
        queue.put(job)
        return job
    if kind == "study" and spec.get("agent") not in STUDY_AGENTS:
        raise ValueError(f"study jobs need an agent with a task set: {', '.join(STUDY_AGENTS)}")
    if kind == "inspect" and not (spec.get("code") or spec.get("path")):
        raise ValueError("an inspect job needs code or a path")
    job = Job(id="job-" + uuid.uuid4().hex[:10], kind=kind, spec=spec, created=time.time())
    job.quote = estimate(spec["agent"], "full") if kind == "study" else {"price_usd": 0.0, "provider_usd": 0.0,
                                                                         "note": "static: no model calls"}
    queue.put(job)
    return job


class Agent:
    """Works through the queue, one job at a time, within its balance."""

    def __init__(self, queue: Queue, balance: Balance, registry=None, log=print, guard=None):
        self.queue, self.balance, self.log = queue, balance, log
        self.registry = registry
        self.guard = guard  # the agent's own account (synapse0g.spend.Guard); run jobs need it

    def status(self) -> dict:
        available = self.balance.available_usd()
        full = estimate("procurement", "full")["provider_usd"]
        return {"balance_usd": round(available, 6), "reserve_usd": RESERVE_USD, "mode": mode_for(available, full),
                "thresholds": {"full_at_usd": round(full * FULL_AT + RESERVE_USD, 4),
                               "reduced_at_usd": round(full * REDUCED_AT + RESERVE_USD, 4)},
                "queued": sum(1 for j in self.queue.all() if j.status in ("queued", "waiting"))}

    def step(self) -> Job | None:
        """Run the next job the budget allows. Returns it, or None when there is nothing to do."""
        for job in self.queue.all():
            if job.status not in ("queued", "waiting"):
                continue
            if job.kind == "inspect":
                return self._run(job, "static")
            if job.kind == "run":
                if self.guard is None:
                    job.status, job.note = "waiting", "the agent's 0G account is not connected (ZG_AGENT_KEY, ZG_MANAGEMENT_KEY)"
                    self.queue.put(job)
                    continue
                return self._run(job, "paid")
            available = self.balance.available_usd()
            mode = mode_for(available, job.quote["provider_usd"])
            if mode in ("full", "reduced"):
                return self._run(job, mode)
            job.status, job.mode = "waiting", mode
            job.note = (f"balance ${available:.4f} is too low for this study (needs about "
                        f"${job.quote['provider_usd'] * REDUCED_AT + RESERVE_USD:.4f} for a reduced one); waiting for funds")
            self.queue.put(job)
        return None

    def _run(self, job: Job, mode: str) -> Job:
        job.status, job.mode = "running", mode
        self.queue.put(job)
        try:
            if job.kind == "inspect":
                from synapse0g.mapping import inspect_code

                job.result = inspect_code(code=job.spec.get("code"), path=job.spec.get("path"), entry=job.spec.get("entry"))
            elif job.kind == "run":
                job.result = self._paid_custom(job) if job.spec.get("agent") == "custom" else self._paid_run(job)
            else:
                job.result, job.spent_usd = self._study(job, mode)
            job.status, job.note = "done", ""
        except Paused as e:  # low funds or a cap: the job waits for credit, nothing crashed
            job.status, job.note = "waiting", str(e)
        except Exception as e:  # recorded on the job, never swallowed
            job.status, job.note = "failed", f"{type(e).__name__}: {e}"
            traceback.print_exc()
        self.queue.put(job)
        return job

    def _paid_run(self, job: Job) -> dict:
        """One supplier request through the compiled app, paid from the agent's account under the job's cap."""
        import synapse0g
        from synapse0g import live
        from synapse0g.agents import supplier
        from synapse0g.graph.runtime.executor import ExecutionMode
        from synapse0g.openai import routed
        from synapse0g.proxy import Engine, Proxy
        from synapse0g.registry import load_registry
        from synapse0g.spend import NEURON
        from synapse0g.tasks import CHECKERS

        registry = self.registry or load_registry()
        engine = Engine(registry)
        ex = supplier.example()
        tasks = {t.id: t for t in supplier.load_supplier()}
        task = tasks[job.spec.get("task") or live.tasks()[0]["id"]]
        rfq = json.loads(task.question)
        policy, report_doc, report = live.policy_from_report()
        from synapse0g.study import _passes
        cap = int(float(job.spec.get("cap_0g", RUN_CAP_0G)) * NEURON)
        before = len(self.guard.refusals)
        records, compiled_now, execution = [], False, None
        run_records: list = []
        progress = PROGRESS[job.id] = live.LiveRun(job.id, task.id)
        progress.emit("task", **live._describe("supplier", task), cap_0g=cap / NEURON)
        t0 = time.perf_counter()
        try:
            with self.guard.for_job(job.id, cap), Proxy(engine) as px:
                if "app" not in _PAID:  # compiling traces the app with real calls: paid by the agent too, once
                    progress.emit("phase", phase="compile", text="Tracing your agent step by step and lifting it to SIR, "
                                                                 "paid by the agent's account (a trace kept from before is replayed free)")
                    # always the same sample, so the kept trace answers a later compile of the same app
                    sample = json.loads(supplier.split("test", 1)[0].question)
                    trace = engine.open(pay_from=PAY_FROM, replay_path=str(ROOT / "data" / "paid_trace.json"))
                    watch = live._Watch(progress, trace, "compile")
                    try:
                        with routed(px.base_url, trace.id):
                            _PAID["app"] = synapse0g.compile(ex.builder, sample={"rfq": sample}, effects=ex.EFFECTS,
                                                             name="supplier_check", cache_dir=ROOT / "data" / "paid_cache",
                                                             cache_scope="paid-agent")
                    finally:
                        watch.close()
                        records += engine.close(trace.id).records
                    compiled_now = True
                app = _PAID["app"]
                progress.emit("compiled", reused=not compiled_now, applied=app.applied, before=app.before.to_ui(),
                              after=app.after.to_ui(), passes=_passes(app), explain=app.explain(),
                              order=app.before.meta.get("declared_order"))
                live_policy = {k: {"label": (report_doc or {}).get("steps", {}).get(k), "techniques": sp.techniques()}
                               for k, sp in policy.steps.items()}
                progress.emit("policy", report=report, steps=live_policy,
                              held_out=(report_doc or {}).get("evaluation", {}).get("verdict"))
                progress.emit("phase", phase="run", text="Running the optimized agent on your request, two calls at a time, "
                                                         "paid by the agent's account")
                # a resumed job replays the calls its paused attempt already paid for, and continues from there
                session = engine.open(policy, pay_from=PAY_FROM, replay_path=str(ROOT / "data" / "resume" / f"{job.id}.json"))
                watch = live._Watch(progress, session, "run")
                try:
                    with routed(px.base_url, session.id):
                        execution = app.run({"rfq": rfq}, mode=ExecutionMode.BENCH, on_event=lambda e: progress.emit(
                            "node", event=e["type"], **{k: v for k, v in e.items() if k != "type"}))
                finally:
                    watch.close()
                    run_records = engine.close(session.id).records
        except Exception as e:
            refused = self.guard.refusals[before:]
            if refused:  # refused for funds or a cap: the job waits; nothing crashed
                progress.emit("paused", reason=refused[0][1])
                progress.done = True
                raise Paused(f"stopped for funds: {refused[0][1]}") from e
            progress.emit("failed", error=f"{type(e).__name__}: {e}"[:400])
            progress.done = True
            raise
        refused = self.guard.refusals[before:]
        if refused:
            progress.emit("paused", reason=refused[0][1])
            progress.done = True
            raise Paused(f"stopped for funds: {refused[0][1]}")
        out = (execution.outputs.get("result") if execution and not execution.crashed else None) or {}
        answer = out.get("order")
        calls = [{"step": r.step, "model": r.tag, "account": r.provider, "request_id": r.request_id, "billed_0g": r.billed_0g,
                  "latency_s": round(r.latency_s, 3), "started_at": r.started_at, "status": r.status, "error": r.error,
                  "hold": r.hold_id, "compile": i < len(records)}
                 for i, r in enumerate(records + run_records)]
        billed = [c["billed_0g"] for c in calls if c["status"] == "ok"]
        result = {"task": task.id, "rfq": rfq.get("rfq_id"), "report": report, "compiled_now": compiled_now,
                "wall_s": round(time.perf_counter() - t0, 3), "crashed": execution.error if execution and execution.crashed else None,
                "answer": answer, "passed": bool(answer) and CHECKERS["supplier"](task, json.dumps(answer)),
                "order_placed": False, "order_note": "place_order is irreversible: stubbed in a paid run, never executed",
                "cap_0g": cap / NEURON, "billed_0g": None if None in billed else round(sum(billed), 9),
                "peak_overlap": peak_overlap(run_records), "calls": calls}
        result["package"] = deliver(job, _PAID["app"], policy, report_doc, report, result, ex=ex, registry=registry,
                                    inputs={"rfq": rfq})
        progress.emit("result", **{k: v for k, v in result.items() if k != "calls"})
        progress.done = True
        return result

    def _paid_custom(self, job: Job) -> dict:
        """The customer's own agent: compiled on their sample input, run optimized, returned as code. Every call
        is paid from the agent's account under the job's cap, exactly as for the example agent."""
        import synapse0g
        from synapse0g import custom, live
        from synapse0g.graph.runtime.executor import ExecutionMode
        from synapse0g.openai import routed
        from synapse0g.proxy import Engine, Proxy
        from synapse0g.registry import load_registry
        from synapse0g.spend import NEURON
        from synapse0g.study import _passes

        registry = self.registry or load_registry()
        engine = Engine(registry)
        path = Path(job.spec["code_path"])
        sample = job.spec.get("sample") or {}
        cap = int(float(job.spec.get("cap_0g", RUN_CAP_0G)) * NEURON)
        before = len(self.guard.refusals)
        progress = PROGRESS[job.id] = live.LiveRun(job.id, "your agent")
        progress.emit("task", label=job.spec.get("name") or "your agent", summary=json.dumps(sample)[:300], cap_0g=cap / NEURON)
        records, run_records, execution, app = [], [], None, None
        t0 = time.perf_counter()
        try:
            module = custom.load(path)
            target = custom.find(module, job.spec.get("entry"))
            nodes = list(target["target"].nodes) if target["kind"] == "langgraph" else []
            effects, effect_source = custom.effects_of(module, path, nodes) if nodes else (getattr(module, "EFFECTS", {}) or {}, {})
            with self.guard.for_job(job.id, cap), Proxy(engine) as px:
                progress.emit("phase", phase="compile", text="Tracing your agent step by step and lifting it to SIR, "
                                                             "paid by the optimizer's account")
                trace = engine.open(pay_from=PAY_FROM)
                watch = live._Watch(progress, trace, "compile")
                try:
                    with routed(px.base_url, trace.id):
                        if target["kind"] == "langgraph":
                            app = synapse0g.compile(target["target"], sample=sample, effects=effects, name=target["name"],
                                                    cache_dir=ROOT / "data" / "paid_cache", cache_scope=f"customer-{job.id}")
                        else:
                            app = synapse0g.compile(target["target"], effects=effects, name=target["name"],
                                                    cache_dir=ROOT / "data" / "paid_cache", cache_scope=f"customer-{job.id}")
                finally:
                    watch.close()
                    records += engine.close(trace.id).records
                progress.emit("compiled", reused=False, applied=app.applied, before=app.before.to_ui(), after=app.after.to_ui(),
                              passes=_passes(app), explain=app.explain(), order=app.before.meta.get("declared_order"),
                              effects_from=effect_source)
                progress.emit("policy", report=None, steps={}, held_out=None)
                progress.emit("phase", phase="run", text="Running your optimized agent on your input, two calls at a time, "
                                                         "paid by the optimizer's account")
                session = engine.open(pay_from=PAY_FROM, replay_path=str(ROOT / "data" / "resume" / f"{job.id}.json"))
                watch = live._Watch(progress, session, "run")
                try:
                    with routed(px.base_url, session.id):
                        execution = app.run(dict(sample), mode=ExecutionMode.BENCH, on_event=lambda e: progress.emit(
                            "node", event=e["type"], **{k: v for k, v in e.items() if k != "type"}))
                finally:
                    watch.close()
                    run_records = engine.close(session.id).records
        except Exception as e:
            refused = self.guard.refusals[before:]
            progress.emit("paused" if refused else "failed", **({"reason": refused[0][1]} if refused else
                                                                {"error": f"{type(e).__name__}: {e}"[:400]}))
            progress.done = True
            if refused:
                raise Paused(f"stopped for funds: {refused[0][1]}") from e
            raise
        calls = [{"step": r.step, "model": r.tag, "account": r.provider, "request_id": r.request_id, "billed_0g": r.billed_0g,
                  "latency_s": round(r.latency_s, 3), "started_at": r.started_at, "status": r.status, "error": r.error,
                  "hold": r.hold_id, "compile": i < len(records)} for i, r in enumerate(records + run_records)]
        billed = [c["billed_0g"] for c in calls if c["status"] == "ok"]
        outputs = (execution.outputs if execution and not execution.crashed else {}) or {}
        result = {"task": "your agent", "rfq": job.spec.get("name") or "your agent", "report": None, "compiled_now": True,
                  "wall_s": round(time.perf_counter() - t0, 3), "crashed": execution.error if execution and execution.crashed else None,
                  "answer": outputs.get("result", outputs), "passed": bool(execution) and not execution.crashed,
                  "order_placed": False, "order_note": "irreversible and external steps are flagged and never executed in a paid run",
                  "cap_0g": cap / NEURON, "billed_0g": None if None in billed else round(sum(billed), 9),
                  "peak_overlap": peak_overlap(run_records), "calls": calls, "custom": True,
                  "flagged": [n["id"] for n in app.after.to_ui()["nodes"] if n.get("effect") in ("external", "irreversible")]}
        result["package"] = deliver_custom(job, app, module, target, path, result, sample)
        progress.emit("result", **{k: v for k, v in result.items() if k != "calls"})
        progress.done = True
        return result

    def _study(self, job: Job, mode: str) -> tuple[dict, float]:
        from synapse0g import study, upstream
        from synapse0g.registry import load_registry

        registry = self.registry or load_registry()
        calib, test, trials = SIZES[mode]
        quote = estimate(job.spec["agent"], mode)
        # the hard cap: what this job may spend, never more than the balance above the reserve
        cap = min(quote["provider_usd"] * 2, self.balance.available_usd() - RESERVE_USD)
        before, old_cap = upstream.BUDGET.spent_usd, upstream.BUDGET.cap_usd
        upstream.BUDGET.cap_usd = before + cap
        try:
            doc = study.run(_study_spec(job.spec["agent"], calib, test), registry, model_id=job.spec.get("model", "zg-main"),
                            workers=6, max_trials=trials, log=self.log)
        finally:
            spent = upstream.BUDGET.spent_usd - before
            upstream.BUDGET.cap_usd = old_cap
            self.balance.spend(spent, f"{job.id}: {job.spec['agent']} study ({mode})")
        v = doc["evaluation"]["verdict"]
        return {"mode": mode, "size": quote, "cap_usd": round(cap, 4), "verdict": "APPROVE" if v["approve"] else "BLOCK",
                "reasons": v["reasons"], "cost_saved": v["cost_gain"], "time_saved": v["latency_gain"],
                "report": f"reports/{doc['agent']}_{doc['model']}_{doc['started_at'][:16].replace(':', '')}.json"}, spent

    def run_forever(self, idle_s: float = 5.0) -> None:
        while True:
            if self.step() is None:
                time.sleep(idle_s)
