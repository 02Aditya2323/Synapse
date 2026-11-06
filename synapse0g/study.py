"""One study: an agent as written against the same agent compiled by synapse0g, on real calls.

Every agent synapse0g optimizes goes through the same four stages, whoever asked for it:

1. Compile. `synapse0g.compile` reads the agent (tracing it if it is a LangGraph app, inside its
   own session so those calls are counted) and the graph passes run. The plan is fixed here,
   before any task is measured.
2. Graph verdict, on tuning tasks: the agent as written, twice, against the compiled graph, task
   by task in one pool. The firewall decides whether the graph changes stand.
3. Call-level search, on tuning tasks, on top of whichever version won: one change per step at a
   time (thinking off, a cheaper model, ...), each against an unchanged control, each through the
   firewall.
4. Held-out tasks, never seen above: as written twice, the final optimized agent, and, when the
   spec gives one, the same steps parallelized by hand. The firewall's verdict on the final agent
   is the result.

`max_trials` and the task counts are the levers a budget pulls: a smaller study costs less and
says less, and its report says how small it was.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from synapse0g import upstream
from synapse0g.optimizer import Policy
from synapse0g.proxy import Engine, Proxy
from synapse0g.registry import ROOT
from synapse0g.tasks import Task
from synapse0g.tune import judge, run_batches, search


@dataclass
class StudySpec:
    name: str  # the report's stem, e.g. "supplier" or "procurement"
    agent_impl: str
    dataset: str
    compile: Callable[[], Any]  # returns an App; called with calls routed to a counted session
    as_written: Callable  # agent(task, base_url, token, model) -> AgentResult
    compiled_agent: Callable[[Any], Callable]  # App -> agent
    calib: list[Task]
    test: list[Task]
    hand_parallel: Callable | None = None
    hand_label: str = "test hand-parallel"
    integration: list[str] = field(default_factory=list)
    extra: dict = field(default_factory=dict)


def _passes(app) -> list[dict]:
    return [{k: v for k, v in r.model_dump(mode="json").items() if k not in ("graph", "before", "after")} for r in app.passes]


def _runs(batches) -> dict:
    return {b.label: {tid: {"passed": r.passed, "crashed": r.crashed, "error": r.error, "answer": r.answer[-2000:],
                            "wall_s": r.wall_s, "extra": r.extra, "records": r.records} for tid, r in b.runs.items()}
            for b in batches}


def run(spec: StudySpec, registry, *, model_id: str, workers: int = 6, min_gain: float = 0.03, max_trials: int = 6,
        out: str | Path | None = None, log: Callable[[str], None] = print) -> dict:
    from synapse0g.openai import routed

    model = registry.model(model_id)
    calib, test = spec.calib, spec.test
    started = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    log(f"{spec.name} on {model.id}: {len(calib)} tuning, {len(test)} held-out tasks")
    engine = Engine(registry)
    search_log: list[str] = []
    with Proxy(engine) as px:
        # 1. compile
        trace = engine.open(session_id=f"compile-{spec.name}")
        t0 = time.perf_counter()
        with routed(px.base_url, trace.id):
            app = spec.compile()
        trace_records = [r.as_dict() for r in engine.close(trace.id).records]
        log(app.explain())
        log(f"  compiled in {time.perf_counter() - t0:.1f}s ({len(trace_records)} traced calls)")
        compiled = spec.compiled_agent(app)

        # 2. graph verdict on tuning tasks
        empty = Policy.empty()
        g1, g2, gc = run_batches(engine, px.base_url, None, model.id, calib,
                                 [("calib as written 1", empty, spec.as_written), ("calib as written 2", empty, spec.as_written),
                                  ("calib compiled", empty, compiled)], workers)
        graph_verdict = judge(g1, g2, gc)
        for b in (g1, g2, gc):
            s = b.summary()
            log(f"  {b.label}: {s['passed']}/{s['tasks']} passed, ${s['cost_usd']:.5f}, {s['median_wall_s']:.1f}s median")
        log(f"  graph changes: {'APPROVE' if graph_verdict.approve else 'BLOCK'} (time {graph_verdict.latency_gain:+.1%}, "
            f"cost {graph_verdict.cost_gain:+.1%})" + ("" if graph_verdict.approve else f": {'; '.join(graph_verdict.reasons)}"))
        base_agent = compiled if graph_verdict.approve else spec.as_written

        # 3. call-level search on top
        def note(m: str) -> None:
            log(m)
            search_log.append(m)

        result = search(engine, px.base_url, base_agent, model.id, calib, workers=workers, min_gain=min_gain,
                        max_trials=max_trials, log=note)
        log("  call policy: " + ("; ".join(result.policy.describe()) or "unchanged"))

        # 4. held-out
        arms = [("test as written 1", empty, spec.as_written), ("test as written 2", empty, spec.as_written),
                ("test optimized", result.policy, base_agent)]
        if spec.hand_parallel:
            arms.append((spec.hand_label, empty, spec.hand_parallel))
        held = run_batches(engine, px.base_url, None, model.id, test, arms, workers)
        t1, t2, final = held[:3]
        hand = held[3] if spec.hand_parallel else None
        verdict = judge(t1, t2, final)
        hand_verdict = judge(t1, t2, hand) if hand else None

    doc = {
        "agent": spec.name,
        "agent_impl": spec.agent_impl,
        "dataset": spec.dataset,
        "model": model.id,
        "provider": model.provider,
        "started_at": started,
        "calib_n": len(calib),
        "test_n": len(test),
        "prices": registry.describe(),
        "integration": spec.integration,
        "compile": {"before": app.before.model_dump(mode="json"), "after": app.after.model_dump(mode="json"),
                    "passes": _passes(app), "applied": app.applied, "explain": app.explain(), "trace_records": trace_records},
        "graph_stage": {"summaries": [g1.summary(), g2.summary(), gc.summary()], "verdict": graph_verdict.as_dict()},
        "steps": {rec["step"]: rec["step_label"] for b in [*result.batches, t1] for r in b.runs.values() for rec in r.records},
        "search": {
            "baseline": [result.baseline[0].summary(), result.baseline[1].summary()],
            "trials": [{"candidate": t.candidate.__dict__, "verdict": t.verdict.as_dict(), "measured_usd": t.measured_usd,
                        "summary": t.summary} for t in result.trials],
            "skipped": [c.__dict__ for c in result.skipped],
            "policy": result.policy.as_dict(),
            "policy_described": result.policy.describe(),
            "log": search_log,
        },
        "evaluation": {
            "baseline_1": t1.summary(), "baseline_2": t2.summary(), "optimized": final.summary(), "verdict": verdict.as_dict(),
            "hand_parallel": hand.summary() if hand else None, "hand_parallel_verdict": hand_verdict.as_dict() if hand_verdict else None,
            "hand_label": spec.hand_label if hand else None,
        },
        "spend": {"spent_usd": upstream.BUDGET.spent_usd, "calls": upstream.BUDGET.calls, "cap_usd": upstream.BUDGET.cap_usd},
        "runs": _runs([g1, g2, gc, *result.batches, *held]),
        **spec.extra,
    }
    out_dir = Path(out or ROOT / "reports")
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{spec.name}_{model.id}_{started[:16].replace(':', '')}"
    (out_dir / f"{stem}.json").write_text(json.dumps(doc, indent=1, default=str), encoding="utf-8")
    log(f"  report: {out_dir / (stem + '.json')}")
    return doc
