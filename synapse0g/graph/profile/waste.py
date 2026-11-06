"""A waste report: what a workflow spent that it did not need to, from one recorded run.

It reads a trace and changes nothing. `duplicate` means two steps the compiler would collapse, so
their spend is measured. `waiting` is measured time in series minus the time the same steps would
take with only their real dependencies in order. That second figure is a bound worked out from
the graph, and only a real run of the optimized graph shows how much of it is reachable.
"""

from __future__ import annotations

from synapse0g.graph.core.sir import Graph, Profile
from synapse0g.graph.passes.base import collect_findings, collect_transformations, run_pipeline
from synapse0g.graph.profile.pricing import priced_at


def _recorded(graph: Graph) -> dict[str, dict]:
    """What the trace measured for each step. The input node is not a step."""
    return {
        n.id: {
            "kind": n.kind,
            "latency_ms": n.meta.get("recorded_latency_ms"),
            "tokens_in": n.meta.get("recorded_tokens_in"),
            "tokens_out": n.meta.get("recorded_tokens_out"),
        }
        for n in graph.nodes
        if n.target != "workflow_input"
    }


def waste(graph: Graph) -> dict:
    steps = _recorded(graph)
    after, results = run_pipeline(graph, Profile.empty(graph.id), ["cse", "dce"])
    elided = after.meta.get("elided") or {}
    latency = {nid: s.get("latency_ms") for nid, s in steps.items()}

    def total(ids, field) -> float | None:
        """Only a model call owes a token count, and one that reported none makes the total unknown."""
        values = [steps[i][field] for i in ids if steps[i]["kind"] == "model"]
        return None if any(v is None for v in values) else sum(values)

    removed = list(elided)
    known = all(v is not None for v in latency.values())
    serial_ms = sum(latency.values()) if known else None
    longest = None
    if known:
        reach: dict[str, float] = {}
        for nid in (i for i in graph.topo_order() if i in latency):
            before = [reach[p] for p in graph.predecessors(nid) if p in reach]
            reach[nid] = (max(before) if before else 0.0) + latency[nid]
        longest = max(reach.values(), default=0.0)
    wasted_in, wasted_out = total(removed, "tokens_in"), total(removed, "tokens_out")
    return {
        "steps": len(steps),
        "removable": [{"node": nid, "target": graph.node(nid).target, "reason": why} for nid, why in elided.items()],
        "tokens_in_removable": wasted_in,
        "tokens_out_removable": wasted_out,
        "priced_at": priced_at(wasted_in, wasted_out),
        "serial_ms": serial_ms,
        "dependency_bound_ms": longest,
        # The sum of step times, so a recorded run that already overlapped reads as more waiting than it had.
        "waiting_ms": None if longest is None else max(0.0, serial_ms - longest),
        "refused": [{"node": f.node, "why": f.message} for f in collect_findings(results)],
        "rewrites": [t.description for t in collect_transformations(results)],
    }
