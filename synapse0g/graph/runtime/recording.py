"""Work you have already paid for, reused on purpose.

Every step is recorded under the hash of its exact inputs. Three features rest on that one idea:

* **Rerun one branch.** Change an input and only the steps that really see the change run again.
  Each step says why it was reused, or why it was not.
* **Resume after a crash.** Finished steps are recorded, so a second attempt starts where it stopped.
* **Replay check.** A candidate optimization runs against the recordings. If it sends the same
  prompts, it spends no new tokens on model calls. A changed prompt pays for itself and nothing else.

Limits, all of which the results state:

* A replay cannot see a server that changes answers when requests overlap: a recording always
  returns the same answer. `synapse audit` covers that, live.
* Replay timing is planned from recorded step times, never measured, because a replayed step takes
  no real time.
* All of this runs with side effects stubbed. A live run never reuses a recording, because reusing
  one could skip or repeat an email.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from synapse0g.graph.core import effects
from synapse0g.graph.core.sir import Graph, Ref
from synapse0g.graph.core.store import Store
from synapse0g.graph.runtime.cache import Cache
from synapse0g.graph.runtime.executor import Execution, ExecutionMode, NodeRun
from synapse0g.graph.runtime.scheduler import run_graph


def recording_cache(root: Path) -> Cache:
    cache = Cache(Store(Path(root)))
    cache.cache_everything = True
    return cache


async def _run(graph: Graph, task: dict | None, root: Path, task_id: str | None = None,
               implementations=None) -> Execution:
    cache = recording_cache(root)
    extra = {"implementations": implementations} if implementations else {}
    try:
        return await run_graph(graph, task_input=task, task_id=task_id, cache=cache, mode=ExecutionMode.VERIFY, **extra)
    finally:
        cache.store.close()


def run_recorded(graph: Graph, task: dict | None, root: Path, task_id: str | None = None,
                 *, implementations=None) -> Execution:
    """One run against the recordings under `root`, adding to them. A crash keeps what finished."""
    return asyncio.run(_run(graph, task, root, task_id, implementations))


def _changed_inputs(graph: Graph, node_id: str, old: dict[str, NodeRun], new: dict[str, NodeRun],
                    old_task: dict | None, new_task: dict | None) -> list[str]:
    node = graph.node(node_id)
    reasons = []
    for name, arg in node.args.items():
        if not isinstance(arg, Ref):
            continue
        source = graph.node(arg.node)
        if source.target == "workflow_input":
            if (old_task or {}).get(arg.output) != (new_task or {}).get(arg.output):
                reasons.append(f"input {arg.output!r} changed")
        elif source.id in old and source.id in new and old[source.id].output_digest != new[source.id].output_digest:
            reasons.append(f"{name} comes from {source.id}, whose result changed")
    return reasons


def explain(graph: Graph, new: Execution, old: Execution | None = None, old_task: dict | None = None,
            new_task: dict | None = None) -> list[dict]:
    """For each step: reused or not, and in plain words why."""
    earlier = {r.instance: r for r in old.node_runs} if old else {}
    current = {r.instance: r for r in new.node_runs}
    rows = []
    for run in new.node_runs:
        node = graph.node(run.node_id)
        if run.cached:
            state, why = "reused", "same inputs as a recorded step"
        elif run.stubbed:
            state, why = "stubbed", "it has a side effect, so it never runs here and is never reused"
        elif (finding := effects.may_reuse(node, transform="reuse")) is not None:
            state, why = "ran", finding.message
        else:
            changed = _changed_inputs(graph, run.node_id, earlier, current, old_task, new_task)
            before = earlier.get(run.instance)
            if changed:
                state, why = "ran", "; ".join(changed)
            elif before is not None and before.node_hash == run.node_hash:
                state, why = "ran", "same inputs, but the recording is too old for this step's staleness_s"
            elif before is not None:
                state, why = "ran", "its own inputs or configuration changed"
            else:
                state, why = "ran", "no earlier record of this step"
        rows.append({"node": run.instance, "target": run.target, "state": state, "why": why,
                     "tokens": _tokens(run)})
    return rows


def _tokens(run: NodeRun) -> int | None:
    """Tokens this run really spent. A reused step's counts belong to the original call."""
    if not run.calls_made:
        return 0
    return None if run.tokens_in is None or run.tokens_out is None else run.tokens_in + run.tokens_out


def _spent(execution: Execution) -> int | None:
    values = [_tokens(r) for r in execution.node_runs]
    return None if any(v is None for v in values) else sum(values)


def planned_ms(graph: Graph, recorded: dict[str, list[float]]) -> float | None:
    """How long `graph` takes on recorded step times, with only its real dependencies in order and
    the graph's own concurrency limit ignored. Null if a step has no recorded time."""
    allowed = int(graph.meta.get("max_concurrency", 1) or 1)
    finish: dict[str, float] = {}
    for node_id in graph.topo_order():
        node = graph.node(node_id)
        if node.target == "workflow_input" or node_id in (graph.meta.get("elided") or {}):
            finish[node_id] = 0.0
            continue
        times = recorded.get(node_id)
        if not times and effects.is_side_effecting(node):
            times = [0.0]
        if not times:
            return None
        region = graph.region_of(node_id)
        span = max(times) if region is not None and region.kind == "map" and allowed > 1 else sum(times)
        start = max((finish.get(p, 0.0) for p in graph.predecessors(node_id)), default=0.0)
        finish[node_id] = start + span
    if allowed == 1:
        return sum(sum(recorded.get(n.id, [])) for n in graph.nodes)
    return max(finish.values(), default=0.0)


def replay_check(original: Graph, candidate: Graph, tasks: list[dict], root: Path) -> dict:
    """Judge `candidate` against recordings of `original`: live tokens only where a prompt changed."""
    rows, baseline_tokens, candidate_tokens = [], 0, 0
    times: dict[str, list[float]] = {}
    for task in tasks:
        folder = Path(root) / str(task["id"])
        base = run_recorded(original, task["input"], folder, task["id"])
        if base.crashed:
            return {"status": "crashed", "error": f"{task['id']}: {base.error}"}
        for run in base.node_runs:
            if not run.cached:
                times.setdefault(run.node_id, []).append(run.latency_ms)
        spent_base = _spent(base)
        baseline_tokens = None if spent_base is None or baseline_tokens is None else baseline_tokens + spent_base
        after = run_recorded(candidate, task["input"], folder, task["id"])
        if after.crashed:
            return {"status": "crashed", "error": f"{task['id']}: {after.error}"}
        spent_after = _spent(after)
        candidate_tokens = None if spent_after is None or candidate_tokens is None else candidate_tokens + spent_after
        before_out, after_out = base.outputs, after.outputs
        check = task["check"]
        first = next(iter(before_out.values()), None)
        second = next(iter(after_out.values()), None)
        passed = bool(check(first)), bool(check(second))
        rows.append({"task_id": task["id"], "same_answer": before_out == after_out, "passed_before": passed[0],
                     "passed_after": passed[1], "new_model_calls": sum(r.calls_made for r in after.node_runs)})
    # Per task, so a map's step times stay separate.
    per_task = {k: [t / max(1, len(tasks)) for t in v] for k, v in times.items()}
    before_ms, after_ms = planned_ms(original, per_task), planned_ms(candidate, per_task)
    broken = [r["task_id"] for r in rows if r["passed_before"] and not r["passed_after"]]
    changed = [r["task_id"] for r in rows if not r["same_answer"]]
    return {
        "status": "done",
        "decision": "BLOCK" if broken or changed else "PASS",
        "kind": "replay",
        "tokens_recording_baseline": baseline_tokens,
        "tokens_checking_candidate": candidate_tokens,
        "new_model_calls": sum(r["new_model_calls"] for r in rows),
        "tasks": len(rows),
        "broken": broken,
        "changed_answers": changed,
        "planned_ms_before": before_ms,
        "planned_ms_after": after_ms,
        "rows": rows,
        "note": ("Replay check: answers come from recordings, and times are planned from recorded step times. "
                 "It cannot see a server that changes answers under concurrent requests, so run `synapse audit` "
                 "first. The full firewall remains the verdict of record."),
    }


def describe_rerun(rows: list[dict]) -> str:
    counts = {s: sum(r["state"] == s for r in rows) for s in ("reused", "ran", "stubbed")}
    lines = [f"Reused {counts['reused']}, ran {counts['ran']}, stubbed {counts['stubbed']}"]
    lines += [f"  {r['node']} {r['target']}: {r['state']}. {r['why']}" for r in rows if r["state"] != "reused"]
    return "\n".join(lines)


def demo_tasks_for(name: str) -> list[dict[str, Any]]:
    from synapse0g.graph import demo

    return demo.verification_tasks(name)
