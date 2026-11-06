"""The benchmark driver: run a workflow's graph over tasks on the real runtime.

    py -3 -m bench.suite --workflow research_fanout --split train --n 10

Every execution runs in BENCH mode, so external nodes are stubbed and irreversible ones
refused. Crashes are counted separately and never enter latency or accuracy (CLAUDE.md
rule 10). The projected provider calls are printed before anything starts, and a run over
SYNAPSE_PROVIDER_CALL_BUDGET is refused (docs/BENCHMARKS.md §9).

`optimized` is the graph after the default pass pipeline (`passes.base.run_pipeline`), which
is application, not selection. Baselines are in bench/baselines.py; every row but
`cache_only_warm` starts cold.

Every run ends in a firewall verdict (CLAUDE.md rule 4). The noise band comes from running
the unmodified graph twice on `train` or `calib`. `test` never measures its own: it reuses
the band from `calib`, and refuses one that is missing, thin, or from another graph or
model (docs/CORRECTNESS.md section 6).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from statistics import median, quantiles
from typing import Any

from synapse0g.graph.bench.baselines import fresh_cache, marked_only_cache
from synapse0g.graph.bench.corpus.review_tasks import FILES_PER_TASK
from synapse0g.graph.bench.tasks import Task, load_tasks
from synapse0g.graph.bench.workflows import _llm, code_review, math_tools, rag_qa, research_fanout, shop_ops
from synapse0g.graph.core.sir import Graph, NoiseBand, Profile
from synapse0g.graph.correctness.firewall import VerificationReport, verdict_line, verify_pairs
from synapse0g.graph.passes.base import run_pipeline
from synapse0g.graph.profile.collect import Execution as Measured
from synapse0g.graph.profile.collect import NodeMeasurement, measure_noise
from synapse0g.graph.profile.ledger import charge, describe, saved_per_run, spending
from synapse0g.graph.profile.ledger import report as ledger_report
from synapse0g.graph.runtime.cache import Cache
from synapse0g.graph.runtime.executor import Execution, ExecutionMode, NodeRun
from synapse0g.graph.runtime.providers import model_config, waits
from synapse0g.graph.runtime.scheduler import run_graph_sync

REPORT_DIR = Path(__file__).parent / "reports"
GRAPHS = {"research_fanout": research_fanout, "rag_qa": rag_qa, "math_tools": math_tools,
          "code_review": code_review, "shop_ops": shop_ops}
# the most model calls one execution can make, and the runs that can reach a provider
MAX_CALLS = {"research_fanout": research_fanout.K + 2, "rag_qa": rag_qa.MAX_HOPS + 2,
             "math_tools": math_tools.MAX_CALCS + 2, "shop_ops": shop_ops.MAX_ACTIONS + 1,
             "code_review": FILES_PER_TASK + 1}
#: Workflows whose `test` split has been run once already (1 Nov 2026). Running it again is a
#: second look at the same tasks, and its report must say so.
TEST_ALREADY_USED = frozenset({"research_fanout", "rag_qa", "code_review"})
RUNS_WITH_CALLS = 3  # sequential, optimized, cache_only_cold; warm reuses cold
BAND_FORMULA = "abs(a-b)/max(a,b), p95"  # a band saved under another formula must not decide `test`
MIN_NOISE_SAMPLES = 8  # fewer pairs than this and a p95 is one outlier


def charged(run: NodeRun) -> bool:
    """A real provider call. Nothing else can owe a number: a tool has no tokens, and a cache
    hit's counts are the original call's."""
    return run.calls_made > 0


def spent(execution: Execution, field: str) -> float | None:
    """What was actually spent at a provider. None if any real call did not report."""
    values = [getattr(r, field) for r in execution.node_runs if charged(r)]
    return None if any(v is None for v in values) else sum(values)


def measured(execution: Execution, task: Task) -> Measured:
    """The firewall's view of a run. Only a real call can be unknown: anything else spent
    zero, and one None would erase the whole run's total. Stubbed side effects are the
    observable actions, compared by content hash because one node can serve many tools, so
    a change to what they would have done is a structural break, never model noise. Every
    run handed to the firewall starts from an empty cache, which is what `cold` means here;
    `Execution.cold` only says that a cache was attached."""
    nodes = [NodeMeasurement(node_id=r.instance, latency_ms=r.latency_ms, cached=r.cached, provider=r.provider,
                             tokens_in=r.tokens_in if charged(r) else 0, tokens_out=r.tokens_out if charged(r) else 0,
                             cost_usd=r.cost_usd if charged(r) else 0.0, model_id=r.model_id, error=r.error,
                             started_ms=r.started_ms, worker=None if r.worker is None else str(r.worker))
             for r in execution.node_runs]
    actions = [{"target": r.target, "effect": r.effect, "node_hash": r.node_hash}
               for r in execution.node_runs if r.stubbed]
    run = Measured(task_id=task.task_id, graph_id=execution.graph_id, split=task.split, cold=True,
                   wall_ms=execution.wall_ms, nodes=nodes, output=execution.outputs.get("result"),
                   error=execution.error, actions=actions)
    charge(run)
    return run


def noise_band(first: list[dict], second: list[dict]) -> NoiseBand | None:
    """The calibrated spread of the unmodified graph, over the pairs where neither run crashed."""
    pairs = [(a["measured"], b["measured"]) for a, b in zip(first, second, strict=True)
             if not a["measured"].crashed and not b["measured"].crashed]
    if not pairs:
        return None
    return measure_noise([a for a, _ in pairs], [b for _, b in pairs])


def save_band(path: Path, band: NoiseBand, graph_id: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    saved = {"band": band.model_dump(mode="json"), "graph_id": graph_id, "model": _llm.MODEL_ID,
             "formula": BAND_FORMULA}
    path.write_text(json.dumps(saved), encoding="utf-8")


def load_band(path: Path, graph_id: str) -> NoiseBand:
    """The band `test` will rest on. `test` is touched once, so a band that does not describe
    this graph on this model stops the run instead of quietly deciding it."""
    if not path.exists():
        raise ValueError(f"`test` reuses the calib noise band and {path} is missing. Run --split calib first.")
    saved = json.loads(path.read_text(encoding="utf-8"))
    for field, expected in (("graph_id", graph_id), ("model", _llm.MODEL_ID), ("formula", BAND_FORMULA)):
        if saved.get(field) != expected:
            raise ValueError(f"{path} has {field} {saved.get(field)!r}, this run is {expected!r}. Run --split calib.")
    band = NoiseBand.model_validate(saved.get("band"))
    if band.samples < MIN_NOISE_SAMPLES:
        raise ValueError(f"{path} has {band.samples} samples, under {MIN_NOISE_SAMPLES}. Run --split calib with more.")
    return band


def verdict(tasks: list[Task], rows: dict[str, list[dict]], noise: NoiseBand | None, split: str,
            against: str = "optimized") -> VerificationReport:
    by_id = {t.task_id: t for t in tasks}
    pairs = [(a["measured"], b["measured"]) for a, b in zip(rows["sequential"], rows[against], strict=True)]
    return verify_pairs(pairs, noise=noise, relation="checker", split=split,
                        checker=lambda task_id, output: by_id[task_id].check(output).success,
                        calibration_split=noise.split if noise is not None and split == "test" else None)


def repeat_flips(rows: dict[str, list[dict]]) -> dict[str, int] | None:
    """How many answers changed verdict when the unmodified graph simply ran again.

    The fair yardstick for a BLOCK on a real model: if repeating changes as much as the
    optimization did, those breaks may be noise. It is reported beside the verdict and never
    relaxes it, because the firewall stays strict unless the team decides otherwise.
    """
    if "sequential_again" not in rows:
        return None
    pairs = [(a, b) for a, b in zip(rows["sequential"], rows["sequential_again"], strict=True)
             if not a["crashed"] and not b["crashed"]]
    return {"broke": sum(a["passed"] and not b["passed"] for a, b in pairs),
            "fixed": sum(b["passed"] and not a["passed"] for a, b in pairs), "tasks": len(pairs)}


def waited(rows: dict[str, list[dict]]) -> dict[str, dict[str, float]]:
    """Back-off per mode. If `sequential` waited more than `optimized`, part of the speedup is
    a rate limit and not the pipeline, so this is printed beside the numbers."""
    return {name: {"retries": sum(r.get("retries", 0) for r in mode), "slept_s": sum(r.get("slept_s", 0) for r in mode),
                   "runs_hit": sum(1 for r in mode if r.get("retries"))} for name, mode in rows.items()}


def without_measured(rows: dict[str, list[dict]]) -> dict[str, list[dict]]:
    return {name: [{k: v for k, v in r.items() if k != "measured"} for r in mode] for name, mode in rows.items()}


def _stored_audits() -> dict:
    """Server audits from the same store `synapse audit` writes to. None stored: an empty dict."""
    from synapse0g.graph.core.store import open_store
    from synapse0g.graph.correctness.audit import stored

    try:
        return stored(open_store(os.environ.get("SYNAPSE_STORE") or os.environ.get("SYNAPSE_DATA") or "data"))
    except Exception as exc:  # noqa: BLE001 - said, not swallowed: the run continues without audits
        print(f"could not read stored server audits ({type(exc).__name__}: {exc}); none applied", file=sys.stderr)
        return {}


def run_task(graph: Graph, task: Task, concurrency: int | None = None, cache: Cache | None = None) -> dict[str, Any]:
    """One execution, as a row, with the provider back-off that landed inside its wall time.
    Tasks run one after another, so the difference in the counters is this run's alone."""
    before = waits()
    row = _run_task(graph, task, concurrency, cache)
    after = waits()
    return {**row, "retries": after["retries"] - before["retries"], "slept_s": after["slept_s"] - before["slept_s"]}


def _run_task(graph: Graph, task: Task, concurrency: int | None, cache: Cache | None) -> dict[str, Any]:
    """Any crash stays a crashed row with its error (CLAUDE.md rule 10), so one bad task
    cannot cost a whole run its verdict."""
    try:
        execution = run_graph_sync(graph, task_input=task.input, task_id=task.task_id, split=task.split,
                                   mode=ExecutionMode.BENCH, concurrency=concurrency, cache=cache)
        if execution.crashed:
            print(f"  CRASHED {task.task_id}: {execution.error}", file=sys.stderr)
            return {"task_id": task.task_id, "crashed": True, "error": execution.error,
                    "measured": measured(execution, task)}
        answer = execution.outputs["result"]
        return {"task_id": task.task_id, "crashed": False, "wall_ms": execution.wall_ms, "answer": answer,
                "passed": task.check(answer).success, "tokens_in": spent(execution, "tokens_in"),
                "tokens_out": spent(execution, "tokens_out"), "cost_usd": spent(execution, "cost_usd"),
                "provider_calls": execution.provider_calls, "cache_hits": execution.cache_hits,
                "measured": measured(execution, task)}
    except Exception as exc:  # noqa: BLE001 - recorded as a crashed run, never swallowed
        error = f"{type(exc).__name__}: {exc}"
        print(f"  CRASHED {task.task_id}: {error}", file=sys.stderr)
        return {"task_id": task.task_id, "crashed": True, "error": error,
                "measured": Measured(task_id=task.task_id, graph_id=graph.id, split=task.split, cold=True,
                                     error=error)}


def optimize(graph: Graph, names: list[str] | None = None, audits: dict | None = None) -> tuple[Graph, list[str]]:
    """The given passes or the default pipeline, and the names of those that changed something.

    `audits` are stored server audits (`synapse audit`). `parallelize` keeps a model's calls one at a
    time when its audit says answers change under concurrent requests.
    """
    from synapse0g.graph.correctness.audit import attach

    optimized, results = run_pipeline(attach(graph, audits), Profile.empty(graph.id), names)
    return optimized, [r.pass_name for r in results if r.applied]


def compare(graph: Graph, optimized: Graph, tasks: list[Task], *, repeat: bool = True) -> dict[str, list[dict]]:
    """Each task on both graphs. The order rotates, because a later run of a task benefits
    from the provider's prompt cache: sequential goes first, then last, so neither the
    comparison nor the noise repeat is always the warm side.

    Each task first runs once, unscored, on the original graph. A task's first run gives
    different answers from later ones, on whichever graph happens to go first, so without
    this a break can come from run order alone. Passes that change prompts, such as
    `context_trim`, still have a cold first prompt on their optimized side."""
    graphs = {"sequential": graph, "optimized": optimized}
    if repeat:
        graphs["sequential_again"] = graph
    names = list(graphs)
    rows: dict[str, list[dict]] = {name: [] for name in names}
    for i, task in enumerate(tasks):
        run_task(graph, task)  # the warm-up: never scored, never timed
        for name in (names if i % 2 == 0 else names[1:] + names[:1]):
            rows[name].append(run_task(graphs[name], task, cache=marked_only_cache() if name == "optimized" else None))
        print(f"[{i + 1}/{len(tasks)}] {task.task_id}", flush=True)
    cache = fresh_cache()
    rows["cache_only_cold"] = [run_task(graph, task, 1, cache) for task in tasks]
    rows["cache_only_warm"] = [run_task(graph, task, 1, cache) for task in tasks]
    return rows


def fmt(value: Any, spec: str = "") -> str:
    """None prints as a dash, never as zero (docs/BENCHMARKS.md §8)."""
    return "—" if value is None else format(value, spec)


def summarize(rows: list[dict]) -> dict[str, Any]:
    done = [r for r in rows if not r["crashed"]]
    out: dict[str, Any] = {"n": len(done), "crashed": len(rows) - len(done)}
    if not done:
        return out
    walls = [r["wall_ms"] / 1000 for r in done]
    tokens = [r["tokens_in"] for r in done]
    out.update(wall_p50_s=median(walls), wall_p95_s=quantiles(walls, n=20)[-1] if len(walls) >= 2 else None,
               tokens_in_p50=None if any(t is None for t in tokens) else median(tokens),
               calls_p50=median(r["provider_calls"] for r in done), passed=sum(r["passed"] for r in done))
    return out


def table(rows: dict[str, list[dict]]) -> str:
    lines = [f"{'mode':<18}{'n':>4}{'crashed':>9}{'wall p50':>10}{'wall p95':>10}{'tokens in':>11}"
             f"{'calls':>7}{'passed':>8}"]
    for name, mode_rows in rows.items():
        s = summarize(mode_rows)
        lines.append(f"{name:<18}{s['n']:>4}{s['crashed']:>9}{fmt(s.get('wall_p50_s'), '.2f'):>10}"
                     f"{fmt(s.get('wall_p95_s'), '.2f'):>10}{fmt(s.get('tokens_in_p50'), '.0f'):>11}"
                     f"{fmt(s.get('calls_p50'), '.0f'):>7}{fmt(s.get('passed')):>8}")
    for name, mode_rows in rows.items():
        paired = [(a, b) for a, b in zip(rows["sequential"], mode_rows, strict=True)
                  if not a["crashed"] and not b["crashed"]]
        if name != "sequential" and paired:
            lines.append(f"{name} vs sequential: {median(a['wall_ms'] / b['wall_ms'] for a, b in paired):.2f}x p50 "
                         f"over {len(paired)} paired tasks")
    return "\n".join(lines)


def projected_calls(workflow: str, n_tasks: int, *, repeat: bool = True) -> int:
    """The most calls a run can make: its timed runs, the noise repeat, a warm-up per task, and
    the one that loads the model."""
    return MAX_CALLS[workflow] * (n_tasks * (RUNS_WITH_CALLS + repeat + 1) + bool(n_tasks))


def over_budget(calls: int) -> bool:
    # An older .env.example left a comment after the "=", and dotenv reads that as the value.
    budget = os.environ.get("SYNAPSE_PROVIDER_CALL_BUDGET", "").split("#")[0].strip()
    return bool(budget) and calls > int(budget)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Run a workflow over tasks on the real runtime, cold cache.")
    ap.add_argument("--workflow", default="research_fanout", choices=sorted(GRAPHS))
    ap.add_argument("--split", default="train", choices=["train", "calib", "test"])
    ap.add_argument("--n", type=int, default=None)
    ap.add_argument("--passes", help="comma-separated passes, instead of the default pipeline")
    ap.add_argument("--models-serial", action="store_true",
                    help="run tools concurrently but model calls one at a time, for a server whose answers "
                         "change under concurrent requests (synapse audit)")
    ap.add_argument("--notify", action="store_true", help="send Slack, email and WhatsApp alerts about the verdict")
    ap.add_argument("--final", action="store_true", help="required for `test`, which is touched once")
    ap.add_argument("--label", default="",
                    help="names this run in its report, e.g. 'second run'; required on a test split already used")
    args = ap.parse_args(argv)
    if args.split == "test" and not args.final:
        ap.error("the test split is touched once, for the final numbers: pass --final to mean it")
    if args.split == "test" and args.workflow in TEST_ALREADY_USED and not args.label:
        ap.error(f"{args.workflow}'s test split was already run once (1 Nov). Another run is a second look at "
                 "the same tasks: pass --label 'second run' so its report can never pass as the first")

    graph = GRAPHS[args.workflow].graph()
    noise_path = REPORT_DIR / "noise" / f"{args.workflow}.json"
    noise = None
    if args.split == "test":
        try:
            noise = load_band(noise_path, graph.id)
        except ValueError as exc:
            print(f"refusing: {exc}", file=sys.stderr)
            return 2

    tasks = load_tasks(args.workflow, split=args.split, n=args.n)
    calls = projected_calls(args.workflow, len(tasks), repeat=noise is None)
    print(f"{args.workflow} / split {args.split} / {len(tasks)} tasks / projected provider calls {calls}", flush=True)
    if over_budget(calls):
        print(f"refusing: {calls} projected calls is over SYNAPSE_PROVIDER_CALL_BUDGET", file=sys.stderr)
        return 2

    audits = _stored_audits()
    optimized, applied = optimize(graph, args.passes.split(",") if args.passes else None, audits)
    held = sorted({r["model_id"] for r in audits.values() if not r.get("safe_to_parallelize_model_calls", True)}
                  & {n.target for n in graph.nodes if n.kind == "model"})
    if held:
        print(f"server audit: {', '.join(held)} changed answers under concurrent requests, so its calls run "
              "one at a time. Tools still overlap.", flush=True)
    if args.models_serial:
        provider = model_config(_llm.MODEL_ID).provider
        optimized = optimized.copy_with(meta={**optimized.meta, "per_provider": {provider: 1}})
    with spending() as spent:
        if tasks:
            run_task(graph, tasks[0], 1)  # loads the model, or the first timed row pays for that
        rows = compare(graph, optimized, tasks, repeat=noise is None)
    ledger = ledger_report(spent, saved_per_run([r["measured"] for r in rows["sequential"]],
                                                [r["measured"] for r in rows["optimized"]]))
    if noise is None:
        noise = noise_band(rows["sequential"], rows["sequential_again"])
        if noise is not None and args.split == "calib":
            save_band(noise_path, noise, graph.id)
    report = verdict(tasks, rows, noise, args.split)
    flips = repeat_flips(rows)
    from synapse0g.graph.correctness.net_rule import net_verdict

    proposed = net_verdict(report, flips)
    tolerant = None
    if flips and report.verdict.decision == "BLOCK":
        # The answer if we allowed the breaks a plain repeat produces. Shown, never the verdict.
        pairs = [(a["measured"], b["measured"]) for a, b in zip(rows["sequential"], rows["optimized"], strict=True)]
        by_id = {t.task_id: t for t in tasks}
        tolerant = verify_pairs(pairs, noise=noise, relation="checker", split=args.split,
                                checker=lambda task_id, output: by_id[task_id].check(output).success,
                                calibration_split=None if noise is None or noise.split != "calib" else "calib",
                                max_stochastic_breaks=flips["broke"]).verdict.decision
    rows = without_measured(rows)
    print(describe(ledger))
    pipeline = " -> ".join(applied) or "nothing applied"
    print(f"\n{args.workflow} / split {args.split} / relation checker\n"
          f"pipeline: {pipeline}\n{table(rows)}\n"
          f"firewall: {verdict_line(report.verdict)}")
    backed_off = waited(rows)
    for name, wait in backed_off.items():
        if wait["retries"]:
            print(f"back-off in {name}: {wait['retries']} retries, {wait['slept_s']:.1f} s asleep, "
                  f"{wait['runs_hit']} of {len(rows[name])} runs")
    if flips:
        print(f"unmodified graph, run again: {flips['broke']} broke, {flips['fixed']} fixed, of {flips['tasks']} tasks")
    if tolerant:
        print(f"if breaks at that level were allowed, the verdict would be {tolerant}. The verdict above stands.")
    print(f"proposed rule {proposed.rule} (not in force): {proposed.decision} (relation {proposed.relation}, "
          f"split {proposed.split}, {proposed.condition}), {'; '.join(proposed.reasons)}. "
          f"{proposed.fixed} fixed, {proposed.broken} broken, McNemar p = {proposed.p_mcnemar:.3f}. "
          "The verdict above stands.")
    for row in report.per_task:
        if row.status in ("broken", "crashed"):
            print(f"  {row.status} {row.task_id}: {row.error or ', '.join(row.invariant_failures) or 'checker'}")
    REPORT_DIR.mkdir(exist_ok=True)
    suffix = "_" + "".join(c if c.isalnum() else "_" for c in args.label.lower()).strip("_") if args.label else ""
    path = REPORT_DIR / f"{args.workflow}_{args.split}{suffix}.json"
    meta = {"workflow": args.workflow, "split": args.split, "relation": "checker", "model": _llm.MODEL_ID,
            "pipeline": pipeline, "warmup_per_task": True,
            "models_serial": args.models_serial, "models_serial_by_audit": held, "label": args.label or None}
    run = {"meta": meta, "rows": rows, "verdict": report.verdict.model_dump(mode="json"), "repeat_flips": flips,
           "waits": backed_off, "per_task": [r.model_dump(mode="json") for r in report.per_task],
           "errors": report.errors,
           "if_repeat_noise_allowed": tolerant, "proposed_net_verdict": proposed.model_dump(mode="json"),
           "ledger": ledger}
    path.write_text(json.dumps(run, indent=1, ensure_ascii=False, default=str), encoding="utf-8")
    print(f"-> {path}")
    if args.notify:
        from synapse0g.graph.alerts import from_verdict, send

        for name, state, detail in send(from_verdict(f"{args.workflow} on {_llm.MODEL_ID}", run["verdict"])):
            print(f"  alert {name}: {state}. {detail}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
