"""synapse0g command line.

  synapse0g models                       prices and capabilities as the registry sees them
  synapse0g bench --agent research --model zg-main --calib 30 --test 50
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from synapse0g import upstream
from synapse0g.registry import ROOT, load_env, load_registry


def _pct(x: float | None) -> str:
    return "n/a" if x is None else f"{x:+.1%}"


def _ci_text(v: dict) -> str:
    if "cost_ci" not in v:  # reports written before the paired rule
        return f"Cost {v['cost_gain']:+.1%} saved (noise {v['cost_band']:.1%}); time {v['latency_gain']:+.1%} saved."
    c, t = v["cost_ci"], v["latency_ci"]
    return (
        f"Cost saved {v['cost_gain']:+.1%} (95% interval {c[0]:+.1%} to {c[1]:+.1%}); "
        f"time saved {v['latency_gain']:+.1%} ({t[0]:+.1%} to {t[1]:+.1%}, against {v['latency_reference']}); "
        f"tokens saved {v['tokens_gain']:+.1%}."
    )


def _markdown(doc: dict) -> str:
    s = doc["search"]
    lines = [
        f"# {doc['agent']} agent on {doc['model']} ({doc['provider']})",
        "",
        f"Run {doc['started_at']}. Agent: {doc.get('agent_impl', 'LangGraph `create_react_agent`, unchanged')}. Tasks: {doc['dataset']}.",
        f"Tuning tasks: {doc['calib_n']} (`calib`). Reported tasks: {doc['test_n']} (`test`, never seen by the search).",
        f"Prices: {json.dumps(doc['prices']['models'][doc['model']])}.",
        "",
        "## Held-out result (the numbers to quote)",
        "",
    ]
    ev = doc["evaluation"]
    rows = [("measure", "unchanged run 1", "unchanged run 2", "optimized", "change vs mean of unchanged")]
    b1, b2, opt = ev["baseline_1"], ev["baseline_2"], ev.get("optimized")

    def row(name, key, fmt):
        if key not in b1:  # a report written before this measure existed
            return
        base = (b1[key] + b2[key]) / 2 if b1[key] is not None and b2[key] is not None else None
        o = opt[key] if opt else None
        change = None if (o is None or not base) else (o - base) / base
        rows.append((name, fmt(b1[key]), fmt(b2[key]), fmt(o) if opt else "-", _pct(change) if opt else "-"))

    def money(v):
        return "n/a" if v is None else f"${v:.5f}"

    row("tasks passed", "passed", str)
    row("cost, total (0G billed when given)", "cost_usd", money)
    row("cost per task", "cost_per_task_usd", money)
    row("cost per passed task", "cost_per_pass_usd", money)
    row("input tokens", "tokens_in", lambda v: f"{v:,}")
    row("output tokens", "tokens_out", lambda v: f"{v:,}")
    row("of which hidden reasoning", "reasoning_tokens", lambda v: f"{v:,}")
    row("model calls", "model_calls", str)
    row("mean time per task", "mean_wall_s", lambda v: f"{v:.2f} s")
    row("median time per task", "median_wall_s", lambda v: f"{v:.2f} s")
    row("mean time, rate-limit waits removed", "mean_wall_excl_waits_s", lambda v: f"{v:.2f} s")
    row("rate-limit retries", "retries", str)
    row("crashed runs", "crashed", str)
    lines += ["| " + " | ".join(r) + " |" for r in rows[:1]]
    lines += ["|" + "---|" * len(rows[0])]
    lines += ["| " + " | ".join(r) + " |" for r in rows[1:]]
    lines.append("")
    v = ev.get("verdict")
    if v is None:
        lines.append("**No beneficial change found.** The search approved nothing, so the agent ships unchanged.")
    else:
        lines.append(
            f"**Firewall on held-out tasks: {'APPROVE' if v['approve'] else 'BLOCK'}.** "
            f"Broke {len(v['broken'])}, fixed {len(v['fixed'])}, unstable in the unchanged runs {len(v['unstable'])}. "
            + _ci_text(v)
        )
        if v["reasons"]:
            lines.append("Reasons: " + "; ".join(v["reasons"]))
        if v["broken"]:
            lines.append("Broken tasks: " + ", ".join(v["broken"]))
    lines += ["", "## What the search tried (tuning tasks only)", ""]
    lines.append("| step | technique | why it was considered | predicted saving | measured saving | passes (base 1, base 2, try) | verdict |")
    lines.append("|---|---|---|---|---|---|---|")
    for t in s["trials"]:
        c, vd = t["candidate"], t["verdict"]
        verdict = "APPROVE" if vd["approve"] else "BLOCK: " + "; ".join(vd["reasons"])
        lines.append(
            f"| {c['step']} | {c['technique']} | {c['why']} | ${c['predicted_usd']:.5f} | ${t['measured_usd']:.5f} "
            f"| {tuple(vd['passes'])} | {verdict} |"
        )
    blocked = [t for t in s["trials"] if not t["verdict"]["approve"] and t["verdict"]["cost_gain"] > 0]
    if blocked:
        lines += ["", "Blocked trade-offs. The firewall refused these; a developer could still choose one knowingly:", ""]
        for t in blocked:
            vd = t["verdict"]
            base = sum(vd["passes"][:2]) / 2
            lines.append(
                f"- {t['candidate']['technique']}: {vd['cost_gain']:.0%} cheaper, {vd['latency_gain']:+.0%} time saved, "
                f"passes {vd['passes'][2]} vs {base:g} unchanged (mean), {len(vd['broken'])} stable task(s) broken."
            )
    if s["skipped"]:
        lines += ["", "Not tried, because the predicted saving was under the threshold:", ""]
        for c in s["skipped"]:
            lines.append(f"- {c['technique']} on {c['step']}: predicted ${c['predicted_usd']:.6f}. {c['why']}.")
    lines += ["", "Final policy: " + ("; ".join(s["policy_described"]) or "unchanged"), ""]
    lines += [
        "## Spend",
        "",
        f"This whole run (search and held-out measurement) cost ${doc['spend']['spent_usd']:.4f} over "
        f"{doc['spend']['calls']} paid calls. Cap: ${doc['spend']['cap_usd']:.2f}.",
        "",
        "Steps: " + "; ".join(f"{k} = {v}" for k, v in doc["steps"].items()),
        "",
        "Every call's request id, tokens and billed amount is in the JSON file next to this one.",
    ]
    return "\n".join(lines) + "\n"


def bench(args) -> int:
    from synapse0g.agents.langgraph_agents import AGENTS
    from synapse0g.proxy import Engine, Proxy
    from synapse0g.tasks import load_gsm8k, load_hotpot, pick
    from synapse0g.tune import evaluate, search

    load_env()
    if args.cap is not None:
        upstream.BUDGET.cap_usd = args.cap
    registry = load_registry()
    model = registry.model(args.model)
    if args.agent == "supplier":
        from synapse0g.supplier_bench import run

        doc = run(args, registry)
        g = doc["graph_stage"]["verdict"]
        print(f"graph changes ({', '.join(doc['compile']['applied'])}): {'APPROVE' if g['approve'] else 'BLOCK'}")
        print(_markdown(doc).split("## What the search tried")[0])
        hp = doc["evaluation"]["hand_parallel"]
        print(f"hand-parallel LangGraph: {hp['passed']}/{hp['tasks']} passed, {hp['median_wall_s']:.2f} s median, "
              f"${hp['cost_usd']:.5f}")
        return 0
    if args.agent == "retail":
        from synapse0g.agents.retail import LABELS, load_retail, make_retail_agent

        engine = Engine(registry, labels=LABELS)
        calib, test = load_retail("train")[: args.calib], load_retail("test")[: args.test]
        agent = make_retail_agent(engine, user_model=args.user_model)
        dataset = f"tau-bench retail (Sierra, MIT): tuning on its train split, reporting on its test split; simulated customer on {args.user_model}"
    else:
        all_tasks = load_hotpot() if args.agent == "research" else load_gsm8k()
        calib, test = pick(all_tasks, "calib", args.calib), pick(all_tasks, "test", args.test)
        agent = AGENTS[args.agent]
        engine = Engine(registry)
        dataset = "HotpotQA distractor validation" if args.agent == "research" else "GSM8K test"
    started = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    print(f"{args.agent} on {model.id} ({model.tag}, {model.provider}): {len(calib)} tuning, {len(test)} held-out tasks")
    with Proxy(engine) as px:
        result = search(engine, px.base_url, agent, model.id, calib, workers=args.workers,
                        min_gain=args.min_gain, max_trials=args.max_trials)
        print("  policy:", "; ".join(result.policy.describe()) or "unchanged")
        b1, b2, opt, verdict = evaluate(engine, px.base_url, agent, model.id, test, result.policy, workers=args.workers)
    doc = {
        "agent": args.agent,
        "agent_impl": "tau-bench's reference ToolCallingAgent loop on the OpenAI SDK" if args.agent == "retail" else "LangGraph `create_react_agent`, unchanged",
        "dataset": dataset,
        "model": model.id,
        "provider": model.provider,
        "started_at": started,
        "calib_n": len(calib),
        "test_n": len(test),
        "prices": registry.describe(),
        "steps": {k: v for b in result.batches for r in b.runs.values() for rec in r.records for k, v in [(rec["step"], rec["step_label"])]},
        "search": {
            "baseline": [result.baseline[0].summary(), result.baseline[1].summary()],
            "trials": [
                {"candidate": t.candidate.__dict__, "verdict": t.verdict.as_dict(), "measured_usd": t.measured_usd, "summary": t.summary}
                for t in result.trials
            ],
            "skipped": [c.__dict__ for c in result.skipped],
            "policy": result.policy.as_dict(),
            "policy_described": result.policy.describe(),
        },
        "evaluation": {
            "baseline_1": b1.summary(),
            "baseline_2": b2.summary(),
            "optimized": opt.summary() if opt else None,
            "verdict": verdict.as_dict() if verdict else None,
        },
        "spend": {"spent_usd": upstream.BUDGET.spent_usd, "calls": upstream.BUDGET.calls, "cap_usd": upstream.BUDGET.cap_usd},
        "runs": {
            b.label: {tid: {"passed": r.passed, "crashed": r.crashed, "error": r.error, "answer": r.answer[-300:],
                            "wall_s": r.wall_s, "records": r.records} for tid, r in b.runs.items()}
            for b in [*result.batches, b1, b2, *([opt] if opt else [])]
        },
    }
    out = Path(args.out or ROOT / "reports")
    out.mkdir(parents=True, exist_ok=True)
    stem = f"{args.agent}_{model.id}_{started[:16].replace(':', '')}"
    (out / f"{stem}.json").write_text(json.dumps(doc, indent=1, default=str), encoding="utf-8")
    (out / f"{stem}.md").write_text(_markdown(doc), encoding="utf-8")
    print(f"  report: {out / (stem + '.md')}")
    print(_markdown(doc).split("## What the search tried")[0])
    return 0


def models(_args) -> int:
    load_env()
    print(json.dumps(load_registry().describe(), indent=1))
    return 0


def workflow(args) -> int:
    """The graph engine: compile a bundled workflow and show what the passes do, or run its bench."""
    if args.action == "bench":
        from synapse0g.graph.bench.suite import main as bench_main

        return bench_main(["--workflow", args.name, *args.rest])
    from synapse0g.showcase import _protections, compile_workflow

    c = compile_workflow(args.name)
    g = c["before"]
    print(f"{args.name}: {g['id']}, {len(g['nodes'])} steps")
    for n in g["nodes"]:
        print(f"  {n['id']:>4}  {n['kind']:<9} {n['effect']:<12} {n['target']}" + (f"  [{n['region']}]" if n.get("region") else ""))
    print("passes:")
    for p in c["passes"]:
        if p["applied"]:
            for t in p["transformations"]:
                print(f"  {p['pass_name']:<13} {t['description']}")
        else:
            print(f"  {p['pass_name']:<13} not applied: {(p['findings'][0]['message'] if p['findings'] else (p.get('estimate') or {}).get('note'))}")
    for node, reasons in _protections(c["passes"], g).items():
        print(f"  protected {node}: {reasons[0]}")
    return 0


def ui(args) -> int:
    from synapse0g.server import serve

    serve(args.port)
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="synapse0g")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("models").set_defaults(fn=models)
    w = sub.add_parser("workflow", help="the graph engine: show a bundled workflow, or bench it on real tasks")
    w.add_argument("action", choices=["show", "bench"])
    w.add_argument("name", nargs="?", default="research_fanout",
                   choices=["research_fanout", "code_review", "rag_qa", "math_tools", "shop_ops"])
    w.add_argument("rest", nargs=argparse.REMAINDER, help="for bench: the bench driver's own options, e.g. --split calib --n 10")
    w.set_defaults(fn=workflow)
    u = sub.add_parser("ui", help="serve the dashboard and its data")
    u.add_argument("--port", type=int, default=8000)
    u.set_defaults(fn=ui)
    b = sub.add_parser("bench")
    b.add_argument("--agent", choices=["research", "math", "retail", "supplier"], required=True)
    b.add_argument("--user-model", default="zg-main", help="retail only: the simulated customer's model (never optimized)")
    b.add_argument("--model", required=True)
    b.add_argument("--calib", type=int, default=30)
    b.add_argument("--test", type=int, default=50)
    b.add_argument("--workers", type=int, default=8)
    b.add_argument("--min-gain", type=float, default=0.03)
    b.add_argument("--max-trials", type=int, default=6)
    b.add_argument("--cap", type=float, default=None, help="spend cap in USD for this run")
    b.add_argument("--out", default=None)
    b.set_defaults(fn=bench)
    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
