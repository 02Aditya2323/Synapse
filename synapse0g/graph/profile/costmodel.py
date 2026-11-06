"""Deterministic list-schedule simulator. All returned numbers are predictions.

Uses Graph.predecessors() — the same readiness predicate the runtime scheduler uses
(CLAUDE.md rule 4), so predicted and realized schedules are structurally identical.
"""

from __future__ import annotations

from pydantic import BaseModel

from synapse0g.graph.core.sir import Graph, Profile
from synapse0g.graph.profile.collect import complete_sum
from synapse0g.graph.profile.critical_path import weights


class Slot(BaseModel):
    node: str
    start_ms: float
    end_ms: float
    worker: int


class Schedule(BaseModel):
    wall_ms: float
    cost_usd: float | None
    tokens: float | None
    slots: list[Slot]
    predicted: bool = True


def ready_nodes(graph: Graph, pending: list[str], done: set[str]) -> list[str]:
    """Shared readiness primitive: returns nodes whose full predecessor set is done.

    Exported so the validation runner can import it and both use the same logic.
    """
    return [n for n in pending if graph.predecessors(n) <= done]


def simulate(
    graph: Graph,
    profile: Profile,
    *,
    max_concurrency: int = 1,
    providers: dict[str, str] | None = None,
    provider_limits: dict[str, int] | None = None,
) -> Schedule:
    if max_concurrency < 1 or any(v < 1 for v in (provider_limits or {}).values()):
        raise ValueError("concurrency limits must be positive")
    w = weights(graph, profile)
    providers_map, caps = providers or {}, provider_limits or {}
    pending: list[str] = [n.id for n in graph.nodes]
    done: set[str] = set()
    active: list[Slot] = []
    slots: list[Slot] = []
    time = 0.0

    while pending or active:
        for n in ready_nodes(graph, pending, done):
            occupied = {s.worker for s in active}
            if len(active) >= max_concurrency:
                break
            provider = providers_map.get(n)
            if provider in caps and sum(providers_map.get(s.node) == provider for s in active) >= caps[provider]:
                continue
            worker = next(i for i in range(max_concurrency) if i not in occupied)
            slot = Slot(node=n, start_ms=time, end_ms=time + w[n], worker=worker)
            active.append(slot)
            slots.append(slot)
            pending.remove(n)
        if not active:
            if pending:
                raise ValueError("schedule deadlock")
            break
        time = min(s.end_ms for s in active)
        finished = [s for s in active if s.end_ms == time]
        done.update(s.node for s in finished)
        active = [s for s in active if s.end_ms != time]

    ps = [profile.nodes[n.id] for n in graph.nodes if n.id in profile.nodes]
    return Schedule(
        wall_ms=time,
        slots=slots,
        cost_usd=complete_sum(p.cost_usd_mean for p in ps),
        tokens=complete_sum(v for p in ps for v in [p.tokens_in_mean, p.tokens_out_mean]),
    )
