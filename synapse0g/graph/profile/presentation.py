"""JSON-ready profile/graph payloads for the API and dashboard owners.

Delegates edge derivation to Graph.data_edges() / Graph.effect_edges() from
Aryan's core/sir.py — the same computation the scheduler uses, so the UI
and the runtime can never disagree about which edges exist.
"""

from __future__ import annotations

from synapse0g.graph.core.sir import Graph, Profile
from synapse0g.graph.profile.critical_path import critical_path


def profile_payload(graph: Graph, profile: Profile) -> dict:
    path = critical_path(graph, profile)
    bottlenecks = []
    for nid in path.nodes:
        node = profile.nodes[nid]
        bottlenecks.append(
            {
                "node": nid,
                "share_of_critical_path": node.latency_p50_ms / path.duration_ms if path.duration_ms else 0,
                "reason": (
                    f"Measured critical-path contribution: {node.latency_p50_ms:.2f} ms "
                    f"from {profile.meta.get('latency_samples', {}).get(nid, node.samples)} latency samples"
                ),
            }
        )
    return {
        "graph_id": graph.id,
        "executions": profile.executions,
        "split": profile.split,
        "cold": profile.cold,
        "crashed": profile.meta.get("crashed", 0),
        "total": profile.total.model_dump(),
        "nodes": {key: value.model_dump() for key, value in profile.nodes.items()},
        "critical_path": [
            {"node": nid, "latency_p50_ms": profile.nodes[nid].latency_p50_ms, "slack_ms": path.slack_ms[nid]}
            for nid in path.nodes
        ],
        "bottlenecks": sorted(bottlenecks, key=lambda b: -b["share_of_critical_path"]),
        "noise_band_pct": profile.noise_band_pct,
    }


def graph_payload(graph: Graph, profile: Profile) -> dict:
    path = critical_path(graph, profile)
    result = graph.model_dump(mode="json")
    for node in result["nodes"]:
        nid = node["id"]
        np = profile.nodes.get(nid)
        node["profile"] = np.model_dump() if np else None
        if node["profile"]:
            node["profile"]["slack_ms"] = path.slack_ms.get(nid, 0.0)

    # Delegate edge computation to Graph — single source of truth
    data = [e.model_dump() for e in graph.data_edges()]
    effects = [e.model_dump() for e in graph.effect_edges()]

    result.update(
        edges={"data": data, "effect": effects},
        critical_path=path.nodes,
        critical_path_ms=path.duration_ms,
    )
    return result
