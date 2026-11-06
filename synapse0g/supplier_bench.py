"""The supplier-check study: one LangGraph app, compiled by synapse0g, measured on real 0G calls.

    synapse0g bench --agent supplier --model zg-main --calib 8 --test 20

1. Compile. `synapse0g.compile` traces the app once on a tuning request (real calls) and runs the
   graph passes. Offline from here on: the plan is fixed before any task is measured.
2. Graph verdict, on tuning tasks: the app as written, twice, against the compiled graph, task by
   task in one pool. The firewall decides whether the graph changes stand.
3. Call-level search, on tuning tasks, on top of whichever graph won: per-step changes (thinking
   off, a cheaper model, ...), each against an unchanged control, each through the firewall.
4. Held-out tasks, never seen by 2 or 3: as written twice, the final optimized app, and the same
   nodes wired in parallel by hand in LangGraph. The firewall's verdict on the final app is the
   result; the hand-wired run shows what the framework alone gets with expert wiring.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from synapse0g import upstream
from synapse0g.optimizer import Policy
from synapse0g.proxy import Engine, Proxy
from synapse0g.registry import ROOT
from synapse0g.tune import judge, run_batches, search


def _passes(app) -> list[dict]:
    out = []
    for r in app.passes:
        d = r.model_dump(mode="json")
        out.append({k: v for k, v in d.items() if k not in ("graph", "before", "after")})
    return out


def _runs(batches) -> dict:
    return {b.label: {tid: {"passed": r.passed, "crashed": r.crashed, "error": r.error, "answer": r.answer[-300:],
                            "wall_s": r.wall_s, "extra": r.extra, "records": r.records} for tid, r in b.runs.items()}
            for b in batches}


def run(args, registry) -> dict:
    import synapse0g
    from synapse0g.agents import supplier

    model = registry.model(args.model)
    calib, test = supplier.split("calib", args.calib), supplier.split("test", args.test)
    ex = supplier.example()
    started = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    print(f"supplier on {model.id}: {len(calib)} tuning, {len(test)} held-out tasks")
    engine = Engine(registry)
    log = []
    with Proxy(engine) as px:
        # 1. compile: the trace runs on a tuning request, inside its own session so its calls are counted
        sample = {"rfq": json.loads(calib[0].question)}
        trace = engine.open(session_id="compile-trace")
        from synapse0g.openai import routed

        t0 = time.perf_counter()
        with routed(px.base_url, trace.id):
            app = synapse0g.compile(ex.builder, sample=sample, effects=ex.EFFECTS, name="supplier_check")
        trace_records = [r.as_dict() for r in engine.close(trace.id).records]
        print(app.explain())
        print(f"  compiled in {time.perf_counter() - t0:.1f}s ({len(trace_records)} traced calls)")
        compiled = supplier.compiled_agent(app)

        # 2. graph verdict on tuning tasks
        empty = Policy.empty()
        g1, g2, gc = run_batches(engine, px.base_url, None, model.id, calib,
                                 [("calib as written 1", empty, supplier.as_written),
                                  ("calib as written 2", empty, supplier.as_written),
                                  ("calib compiled", empty, compiled)], args.workers)
        graph_verdict = judge(g1, g2, gc)
        for b in (g1, g2, gc):
            s = b.summary()
            print(f"  {b.label}: {s['passed']}/{s['tasks']} passed, ${s['cost_usd']:.5f}, {s['median_wall_s']:.1f}s median")
        print(f"  graph changes: {'APPROVE' if graph_verdict.approve else 'BLOCK'} "
              f"(time {graph_verdict.latency_gain:+.1%}, cost {graph_verdict.cost_gain:+.1%})"
              + ("" if graph_verdict.approve else f": {'; '.join(graph_verdict.reasons)}"))
        base_agent = compiled if graph_verdict.approve else supplier.as_written

        # 3. call-level search on top of the chosen graph
        result = search(engine, px.base_url, base_agent, model.id, calib, workers=args.workers,
                        min_gain=args.min_gain, max_trials=args.max_trials, log=lambda m: (print(m), log.append(m)))
        print("  call policy:", "; ".join(result.policy.describe()) or "unchanged")

        # 4. held-out
        t1, t2, final, hand = run_batches(engine, px.base_url, None, model.id, test,
                                          [("test as written 1", empty, supplier.as_written),
                                           ("test as written 2", empty, supplier.as_written),
                                           ("test optimized", result.policy, base_agent),
                                           ("test hand-parallel LangGraph", empty, supplier.hand_parallel)],
                                          args.workers)
        verdict = judge(t1, t2, final)
        hand_verdict = judge(t1, t2, hand)

    doc = {
        "agent": "supplier",
        "agent_impl": "examples/supplier_agent.py: a LangGraph StateGraph, six steps in a chain, unchanged but for "
                      "the synapse0g import and compile",
        "dataset": "synthetic supplier marketplace (synapse0g/agents/supplier_desk.py), 60 requests; right answers "
                   "computed by code from the purchasing policy",
        "model": model.id,
        "provider": model.provider,
        "started_at": started,
        "calib_n": len(calib),
        "test_n": len(test),
        "prices": registry.describe(),
        "compile": {"before": app.before.model_dump(mode="json"), "after": app.after.model_dump(mode="json"),
                    "passes": _passes(app), "applied": app.applied, "explain": app.explain(),
                    "trace_records": trace_records, "sample": sample["rfq"]["rfq_id"]},
        "graph_stage": {"summaries": [g1.summary(), g2.summary(), gc.summary()], "verdict": graph_verdict.as_dict()},
        "steps": {rec["step"]: rec["step_label"] for b in [*result.batches, t1] for r in b.runs.values() for rec in r.records},
        "search": {
            "baseline": [result.baseline[0].summary(), result.baseline[1].summary()],
            "trials": [{"candidate": t.candidate.__dict__, "verdict": t.verdict.as_dict(), "measured_usd": t.measured_usd,
                        "summary": t.summary} for t in result.trials],
            "skipped": [c.__dict__ for c in result.skipped],
            "policy": result.policy.as_dict(),
            "policy_described": result.policy.describe(),
            "log": log,
        },
        "evaluation": {
            "baseline_1": t1.summary(),
            "baseline_2": t2.summary(),
            "optimized": final.summary(),
            "verdict": verdict.as_dict(),
            "hand_parallel": hand.summary(),
            "hand_parallel_verdict": hand_verdict.as_dict(),
        },
        "spend": {"spent_usd": upstream.BUDGET.spent_usd, "calls": upstream.BUDGET.calls, "cap_usd": upstream.BUDGET.cap_usd},
        "runs": _runs([g1, g2, gc, *result.batches, t1, t2, final, hand]),
    }
    out = Path(args.out or ROOT / "reports")
    out.mkdir(parents=True, exist_ok=True)
    stem = f"supplier_{model.id}_{started[:16].replace(':', '')}"
    (out / f"{stem}.json").write_text(json.dumps(doc, indent=1, default=str), encoding="utf-8")
    print(f"  report: {out / (stem + '.json')}")
    return doc
