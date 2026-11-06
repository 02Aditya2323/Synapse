"""Run the firewall on the real scheduler: original against optimized, task by task.

`firewall.verify_pairs` decides from paired measurements. This module produces them from
`runtime/scheduler.py`, the runtime we ship, and returns the shape in docs/API_FOR_UI.md
section 4.

Only the demo workflows are verified here, on the fake provider. Every result says so and is
never reportable. Real corpora run through `synapse bench`, because a corpus on a real model
takes minutes and does not belong inside one HTTP request.
"""

from __future__ import annotations

import asyncio
import difflib
import tempfile
from pathlib import Path
from typing import Any

from synapse0g.graph.core.sir import Graph, NoiseBand, Profile
from synapse0g.graph.core.store import Store, StoreProtocol
from synapse0g.graph.correctness.firewall import verify_pairs
from synapse0g.graph.passes.base import run_pipeline
from synapse0g.graph.profile.collect import Execution as Measured
from synapse0g.graph.profile.collect import NodeMeasurement, execution_total
from synapse0g.graph.profile.ledger import add_to, charge, saved_per_run, spending
from synapse0g.graph.runtime.cache import Cache
from synapse0g.graph.runtime.executor import Execution, ExecutionMode
from synapse0g.graph.runtime.scheduler import run_graph

#: Demo runs are labelled `train`, so their verdict can never be reported as a result.
DEMO_SPLIT = "train"
NOTE = (
    "Demo verdict on a fake provider, over a small built-in task set. It shows the firewall working on the real "
    "scheduler. It is not a benchmark and is never reportable. Real corpora: `synapse bench`."
)


class NotVerifiable(ValueError):
    """This graph cannot be verified here, and the message says what to do instead."""


def measured(execution: Execution, task_id: str, split: str) -> Measured:
    """The firewall's view of a run. A cache hit spent nothing, so it counts as zero. Stubbed
    side effects are the observable actions, compared by content hash because one node can
    serve many tools, so a pass that changes them is a structural break, never model noise."""
    nodes = [
        NodeMeasurement(
            node_id=r.node_id,
            latency_ms=r.latency_ms,
            cached=r.cached,
            provider=r.provider,
            # Only a real provider call spends tokens: a model node, or a node that called a model
            # itself, such as a CrewAI task. Without the zero here, one transform node made the
            # whole run's token total unknown.
            tokens_in=0 if not r.calls_made else r.tokens_in,
            tokens_out=0 if not r.calls_made else r.tokens_out,
            cost_usd=0.0 if not r.calls_made else r.cost_usd,
            model_id=r.model_id,
            provider_calls=r.calls_made if r.kind != "model" and r.calls_made else None,
            error=r.error,
            started_ms=r.started_ms,
            worker=None if r.worker is None else str(r.worker),
        )
        for r in execution.node_runs
        if not r.discarded  # thrown-away guesses are spend, not measurements of the node
    ]
    actions = [{"target": r.target, "effect": r.effect, "node_hash": r.node_hash}
               for r in execution.node_runs if r.stubbed]
    return Measured(
        task_id=task_id,
        graph_id=execution.graph_id,
        split=split,
        cold=True,
        wall_ms=execution.wall_ms,
        nodes=nodes,
        output=execution.outputs.get("result"),
        error=execution.error,
        actions=actions,
    )


def noise_band(first: list[Measured], second: list[Measured], split: str) -> NoiseBand | None:
    """p95 of the absolute paired difference between two runs of the unmodified graph."""
    pairs = [(a, b) for a, b in zip(first, second, strict=True) if not a.crashed and not b.crashed and a.wall_ms]
    if not pairs:
        return None
    # Over the larger of the two, as in profile/collect.py measure_noise.
    spreads = sorted(abs(a.wall_ms - b.wall_ms) / max(a.wall_ms, b.wall_ms) * 100 for a, b in pairs)
    costs = [(execution_total(a, "cost_usd"), execution_total(b, "cost_usd")) for a, b in pairs]
    # No price means no cost noise to state, and the cost objective then stays honest and idle.
    cost_spreads = sorted(abs(x - y) / x * 100 for x, y in costs if x and y is not None)
    return NoiseBand(
        latency_pct=spreads[min(len(spreads) - 1, int(0.95 * len(spreads)))],
        samples=len(pairs),
        split=split,
        cost_pct=cost_spreads[min(len(cost_spreads) - 1, int(0.95 * len(cost_spreads)))] if cost_spreads else None,
    )


async def run_one(
    graph: Graph, task_id: str, task_input: dict, root: Path, split: str = DEMO_SPLIT, implementations=None
) -> Measured:
    # A fresh cache per run, so every measurement is cold and no run can warm another.
    cache = Cache(Store(root / task_id / graph.id))
    execution = await run_graph(
        graph, task_input=task_input, task_id=task_id, split=split, cache=cache, mode=ExecutionMode.VERIFY,
        implementations=implementations,
    )
    cache.store.close()
    run = measured(execution, task_id, split)
    charge(run)
    return run


async def _measure(original: Graph, candidate: Graph, tasks: list[dict[str, Any]], implementations=None):
    with tempfile.TemporaryDirectory(prefix="synapse-verify-", ignore_cleanup_errors=True) as tmp:
        root = Path(tmp)
        before = [await run_one(original, t["id"], t["input"], root / "before", implementations=implementations)
                  for t in tasks]
        again = [await run_one(original, t["id"], t["input"], root / "again", implementations=implementations)
                 for t in tasks]
        after = [await run_one(candidate, t["id"], t["input"], root / "after", implementations=implementations)
                 for t in tasks]
    return before, again, after


def answer_diff(before: Any, after: Any) -> list[str]:
    """Where two answers differ, line by line. Empty when they match."""
    if before == after:
        return []
    return [
        line
        for line in difflib.ndiff(str(before).splitlines(), str(after).splitlines())
        if line.startswith(("- ", "+ "))
    ]


def force_pass(store: StoreProtocol, graph_id: str, pass_name: str) -> dict:
    """Apply one chosen pass with no search, and let the firewall judge it. Passes still refuse what
    their preconditions forbid, so this cannot push a change past the effect rules."""
    from synapse0g.graph import demo, workbench

    original = store.load_graph(graph_id)
    if original.name not in demo.WORKFLOWS and not workbench.supports(original):
        raise NotVerifiable(f"{original.name} is not a demo workflow, so it cannot be judged here.")
    profile = store.latest_profile(graph_id) or Profile.empty(graph_id)
    forced, results = run_pipeline(original, profile, [pass_name])
    result = results[0]
    base = {"pass": pass_name, "findings": [f.model_dump(mode="json") for f in result.findings],
            "note": result.refused or result.estimate.note or None}
    if not result.applied:
        return {**base, "applied": False, "verdict": None}
    name = f"{original.name} (forced {pass_name})"
    forced = forced.copy_with(id=f"{graph_id}_forced_{pass_name}", name=name).validate_graph()
    store.save_graph(forced)
    result = verify_graph(store, forced.id)
    verdict = result["verdict"]
    if verdict["broken"] or verdict["crashed"]:
        from synapse0g.graph.quarantine import add

        add(store, original.name, pass_name, verdict["reason"])
        base["quarantined"] = True
    return {**base, "applied": True, "graph_id": forced.id, **result}


def settle_actions(before: list[Measured], again: list[Measured], after: list[Measured]) -> list[tuple]:
    """Pairs for the firewall, with content variation the original shows by itself taken out of the action check.

    An action is compared by content hash, so a stubbed email whose text came from a model differs whenever the model's
    words do. When the unchanged workflow's two runs already differ that way, the difference can't be blamed on a
    rewrite, so that task compares which actions ran and in what order. Which actions, their order and their count stay
    strict always, and so does the content of any task whose original repeated itself exactly.
    """
    def shape(run: Measured) -> list:
        return [(a["target"], a["effect"]) for a in run.actions]

    def bare(run: Measured) -> Measured:
        return run.model_copy(update={"actions": [{"target": a["target"], "effect": a["effect"]} for a in run.actions]})

    pairs = []
    for first, second, candidate in zip(before, again, after, strict=True):
        varies = first.actions != second.actions and shape(first) == shape(second)
        pairs.append((bare(first), bare(candidate)) if varies else (first, candidate))
    return pairs


def verify_candidate(
    original: Graph,
    candidate: Graph,
    tasks: list[dict[str, Any]],
    checker,
    *,
    implementations=None,
    split: str = DEMO_SPLIT,
):
    """The firewall for a graph that carries its own code, such as a compiled CrewAI crew.

    Same procedure as `verify_graph`: every task runs cold on the original, again on the original
    for the noise band, then on the candidate, and `verify_pairs` decides. `tasks` are
    `{"id", "input"}`; `checker(task_id, output)` is the task's own check. The split defaults to
    `train`, so a verdict from here is never reported as a headline number.
    """
    before, again, after = asyncio.run(_measure(original, candidate, tasks, implementations))
    report = verify_pairs(
        settle_actions(before, again, after),
        noise=noise_band(before, again, split),
        split=split,
        checker=lambda task_id, output: bool(checker(task_id, output)),
    )
    return report, before, after


def verify_graph(
    store: StoreProtocol, graph_id: str, *, corpus: str | None = None, relation: str = "checker", split: str = "test"
) -> dict:
    """Verify an optimized graph against the graph it came from.

    `corpus` and `split` are accepted because the contract sends them, but a demo workflow has
    one built-in task set and always runs as `train`. The response states the split it used.
    """
    from synapse0g.graph import demo

    candidate = store.load_graph(graph_id)
    if not candidate.derived_from:
        raise NotVerifiable(f"{graph_id} is not an optimized graph. Optimize it, then verify the result.")
    original = store.load_graph(candidate.derived_from)
    from synapse0g.graph import workbench

    real_model = original.name not in demo.WORKFLOWS and workbench.supports(original)
    if original.name not in demo.WORKFLOWS and not real_model:
        raise NotVerifiable(
            f"{original.name} is not a demo workflow. Real corpora run through `synapse bench --workflow "
            f"{original.name} --split calib`, which ends in a firewall verdict."
        )
    if real_model:
        try:
            tasks = workbench.tasks_for(original, store)
        except workbench.NotHere as exc:
            raise NotVerifiable(str(exc)) from None
    else:
        demo.prepare_for_serving(original)  # a stored graph carries no code
        tasks = demo.verification_tasks(original.name)
    checks = {t["id"]: t["check"] for t in tasks}
    with spending() as spent:
        before, again, after = asyncio.run(_measure(original, candidate, tasks))
    ledger = add_to(store, graph_id, spent, saved_per_run(before, after), fake_provider=not real_model)
    report = verify_pairs(
        list(zip(before, after, strict=True)),
        noise=noise_band(before, again, DEMO_SPLIT),
        relation=relation,
        split=DEMO_SPLIT,
        checker=lambda task_id, output: bool(checks[task_id](output)),
    )
    outputs = {a.task_id: (a.output, b.output) for a, b in zip(before, after, strict=True)}
    per_task = [
        {
            "task_id": row.task_id,
            "status": row.status,
            "before": outputs[row.task_id][0],
            "after": outputs[row.task_id][1],
            "diff": answer_diff(*outputs[row.task_id]),
            "note": demo.explain_break(original.name, outputs[row.task_id][1])
            if row.status == "broken" and not real_model else row.error,
        }
        for row in report.per_task
    ]
    result = {"verdict": report.verdict.model_dump(mode="json"), "per_task": per_task, "ledger": ledger,
              "demo": not real_model,
              "note": "Measured on the real model, on a small set of tasks. Not a headline number."
              if real_model else NOTE}
    store.put_artifact("verdict", graph_id, result)
    return result
