"""synapse0g as a paid agent: it takes optimization jobs, prices them, and pays for its own calls.

A job is one of two kinds:

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
    if kind not in ("inspect", "study"):
        raise ValueError("kind is inspect or study")
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

    def __init__(self, queue: Queue, balance: Balance, registry=None, log=print):
        self.queue, self.balance, self.log = queue, balance, log
        self.registry = registry

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
            else:
                job.result, job.spent_usd = self._study(job, mode)
            job.status = "done"
        except Exception as e:  # recorded on the job, never swallowed
            job.status, job.note = "failed", f"{type(e).__name__}: {e}"
            traceback.print_exc()
        self.queue.put(job)
        return job

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
