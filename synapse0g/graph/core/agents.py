"""Multi-agent analysis: which agents may overlap, and what they hand to each other.

A multi-agent workflow is still one graph. Each node carries the agent it belongs to (`with
agent("name"):` in the DSL), so the rules are the same as for one agent. Two agents may run at
the same time when no data or effect edge connects any of their nodes, and agents that touch the
same state key are ordered, because that is an effect edge. Identical work done by two agents
collapses under `cse`, since a node's key does not include its agent.
"""

from __future__ import annotations

from synapse0g.graph.core.sir import Graph


def members(graph: Graph) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for node in graph.nodes:
        if node.meta.get("agent"):
            out.setdefault(node.meta["agent"], []).append(node.id)
    return out


def _reach(graph: Graph) -> dict[str, set[str]]:
    reach: dict[str, set[str]] = {}
    for nid in reversed(graph.topo_order()):
        found: set[str] = set()
        for succ in graph.successors(nid):
            found |= {succ} | reach.get(succ, set())
        reach[nid] = found
    return reach


def analyze(graph: Graph) -> dict:
    """Agents, what passes between them, and which pairs may overlap, each with its reason."""
    groups = members(graph)
    owner = {nid: name for name, ids in groups.items() for nid in ids}
    reach = _reach(graph)
    handoffs = sorted(
        {(owner[e.src], owner[e.dst], e.output) for e in graph.data_edges()
         if e.src in owner and e.dst in owner and owner[e.src] != owner[e.dst]}
    )
    names = list(groups)
    pairs = []
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            linked = next(
                ((x, y) for x in groups[a] for y in groups[b] if y in reach[x] or x in reach[y]), None
            )
            reason = (
                f"{linked[0]} and {linked[1]} are connected, so one has to finish first"
                if linked
                else "no data or effect edge connects them"
            )
            touched = [{k for nid in ids for k in (*graph.node(nid).writes, *graph.node(nid).reads)}
                       for ids in (groups[a], groups[b])]
            pairs.append({"agents": [a, b], "may_overlap": linked is None, "reason": reason,
                          "shared_state": sorted(touched[0] & touched[1])})
    return {
        "agents": [
            {
                "name": name,
                "nodes": ids,
                "writes": sorted({k for nid in ids for k in graph.node(nid).writes}),
                "reads": sorted({k for nid in ids for k in graph.node(nid).reads}),
            }
            for name, ids in groups.items()
        ],
        "handoffs": [{"from": a, "to": b, "output": out} for a, b, out in handoffs],
        "pairs": pairs,
    }
