"""Longest weighted path and slack on the combined data/effect DAG.

Uses Graph.predecessors() from Aryan's core/sir.py — the same dependency relation
the runtime scheduler uses, so critical-path predictions and realized schedules agree.
"""

from __future__ import annotations

from pydantic import BaseModel

from synapse0g.graph.core.sir import Graph, Profile


class CriticalPath(BaseModel):
    nodes: list[str]
    duration_ms: float
    slack_ms: dict[str, float]
    earliest_start_ms: dict[str, float]


def repeats(graph: Graph, profile: Profile, node_id: str) -> float:
    """How many times per run this node holds up the path.

    Measured, as samples per execution. A branch not always taken counts for less. A map runs
    its elements one after another unless the graph is scheduled concurrently, and then the
    path only waits for one element at a time.
    """
    node = graph.node(node_id)
    region = graph.region(node.region) if node.region else None
    if region is not None and region.kind == "map" and int(graph.meta.get("max_concurrency", 1)) > 1:
        return 1.0
    p = profile.nodes.get(node_id)
    return p.samples / profile.executions if p and profile.executions else 1.0


def weights(graph: Graph, profile: Profile) -> dict[str, float]:
    if graph.id != profile.graph_id:
        raise ValueError("profile graph mismatch")
    if any(r.kind == "loop" for r in graph.regions):
        raise ValueError("expand regions to an execution DAG before schedule analysis: a loop has no fixed depth")
    samples = profile.meta.get("latency_samples", {})
    result = {}
    for n in graph.nodes:
        if n.target == "workflow_input":  # not work: it holds the task and takes no time
            result[n.id] = 0.0
            continue
        p = profile.nodes.get(n.id)
        partial = profile.meta.get("partial", {}).get(n.id, [])
        zero_measured = p is not None and p.latency_p50_ms == 0.0 and samples.get(n.id, 0) > 0
        if p is None or (p.latency_p50_ms == 0.0 and not zero_measured) or "latency_ms" in partial:
            if n.region and samples.get(n.id, 0) == 0:  # a branch no task took costs nothing here
                result[n.id] = 0.0
                continue
            raise ValueError(f"missing measured latency for {n.id}")
        result[n.id] = p.latency_p50_ms * repeats(graph, profile, n.id)
    return result


def critical_path(graph: Graph, profile: Profile) -> CriticalPath:
    w = weights(graph, profile)
    order = graph.topo_order()

    # Build predecessor map once (using Graph's unified data+effect deps)
    pred_map = {nid: graph.predecessors(nid) for nid in order}

    starts: dict[str, float] = {}
    ends: dict[str, float] = {}
    predecessor: dict[str, str | None] = {}
    children: dict[str, set[str]] = {n: set() for n in order}

    for nid in order:
        for p in pred_map[nid]:
            children[p].add(nid)
        pred = max(
            sorted(pred_map[nid]), key=lambda p: ends[p], default=None
        )
        predecessor[nid] = pred
        starts[nid] = ends[pred] if pred is not None else 0.0
        ends[nid] = starts[nid] + w[nid]

    total = max(ends.values(), default=0.0)

    latest: dict[str, float] = {}
    for nid in reversed(order):
        latest[nid] = min((latest[c] for c in children[nid]), default=total) - w[nid]

    path: list[str] = []
    node: str | None = max(order, key=lambda n: ends[n], default=None)
    while node is not None:
        path.append(node)
        node = predecessor[node]

    return CriticalPath(
        nodes=list(reversed(path)),
        duration_ms=total,
        earliest_start_ms=starts,
        slack_ms={n: max(0.0, latest[n] - starts[n]) for n in order},
    )
