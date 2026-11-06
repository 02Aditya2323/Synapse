"""Measured execution aggregation.

Produces `synapse.core.sir.Profile` (the shape Aryan's `core/sir.py` froze at H2)
from a list of `Execution` records. Unknown observations stay None — never 0.
Crashed runs are counted separately and excluded from timing aggregates.
"""

from __future__ import annotations

from collections import defaultdict
from statistics import mean
from typing import Any, Literal

from pydantic import BaseModel, Field

from synapse0g.graph.core.sir import (
    CriticalPathEntry,
    Graph,
    NodeProfile,
    NoiseBand,
    Profile,
    ProfileTotals,
)

Split = Literal["train", "calib", "test"]


class NodeMeasurement(BaseModel):
    node_id: str
    latency_ms: float | None = Field(default=None, ge=0)
    tokens_in: int | None = Field(default=None, ge=0)
    tokens_out: int | None = Field(default=None, ge=0)
    cost_usd: float | None = Field(default=None, ge=0)
    cached: bool = False
    provider: str | None = None
    model_id: str | None = None
    # Provider calls this node made. None means one, the model-node case. A CrewAI task is an
    # agent loop and makes several, which a count of nodes would undercount.
    provider_calls: int | None = Field(default=None, ge=0)
    error: str | None = None
    started_ms: float | None = Field(default=None, ge=0)
    worker: str | None = None


class Execution(BaseModel):
    task_id: str
    graph_id: str
    split: Split
    cold: bool
    wall_ms: float | None = Field(default=None, ge=0)
    nodes: list[NodeMeasurement] = Field(default_factory=list)
    output: Any = None
    error: str | None = None
    seed: int | None = None
    repeat: int = Field(default=0, ge=0)
    actions: list[dict] = Field(default_factory=list)

    @property
    def crashed(self) -> bool:
        return self.error is not None or any(n.error is not None for n in self.nodes)


class Measurements(BaseModel):
    """Whole-run aggregate from successful executions.  Shape mirrors ProfileTotals
    for the parts the dashboard needs; extras live here for the firewall."""

    samples: int = 0
    wall_p50_ms: float | None = None
    wall_p95_ms: float | None = None
    tokens_in: float | None = None
    tokens_out: float | None = None
    tokens: float | None = None
    cost_usd: float | None = None
    provider_calls: float | None = None
    cache_hit_rate: float | None = None
    partial: list[str] = Field(default_factory=list)




# ------------------------------------------------------------------ helpers


def percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    xs = sorted(values)
    index = (len(xs) - 1) * q
    lo = int(index)
    return xs[lo] + (xs[min(lo + 1, len(xs) - 1)] - xs[lo]) * (index - lo)


def complete_sum(values):
    values = list(values)
    return sum(values) if all(v is not None for v in values) else None


def complete_mean(values):
    values = list(values)
    return mean(values) if values and all(v is not None for v in values) else None


def execution_total(run: Execution, field: str):
    return complete_sum(getattr(n, field) for n in run.nodes)


def summarize(runs: list[Execution]) -> Measurements:
    good = [r for r in runs if not r.crashed]
    out = Measurements(samples=len(good))
    if not good:
        return out
    walls = [r.wall_ms for r in good]
    if all(v is not None for v in walls):
        out.wall_p50_ms = percentile(walls, 0.5)
        out.wall_p95_ms = percentile(walls, 0.95)
    else:
        out.partial.append("wall_ms")
    for field in ("tokens_in", "tokens_out", "cost_usd"):
        value = complete_mean(execution_total(r, field) for r in good)
        setattr(out, field, value)
        if value is None:
            out.partial.append(field)
    out.tokens = complete_sum([out.tokens_in, out.tokens_out])
    out.provider_calls = mean(
        sum(1 if n.provider_calls is None else n.provider_calls
            for n in r.nodes if n.provider is not None and not n.cached)
        for r in good
    )
    steps = [n for r in good for n in r.nodes]
    out.cache_hit_rate = mean(n.cached for n in steps) if steps else None
    return out


# ------------------------------------------------------------------ public API


def collect(
    graph: Graph,
    executions: list[Execution],
    *,
    noise: NoiseBand | None = None,
    critical_path_ids: list[str] | None = None,
) -> Profile:
    """Aggregate `executions` into a `Profile` (the shape core/sir.py froze).

    `critical_path_ids` may be supplied by `critical_path.py` after the profile is
    computed; it is optional here so the two modules are not circularly dependent.
    """
    if not executions:
        raise ValueError("profile needs executions")
    if any(r.graph_id != graph.id for r in executions):
        raise ValueError("profile graph mismatch")
    conditions = {(r.split, r.cold) for r in executions}
    if len(conditions) != 1:
        raise ValueError("do not mix split or cold/warm conditions")

    rows: dict[str, list[tuple[NodeMeasurement, bool]]] = defaultdict(list)
    known = {n.id for n in graph.nodes}
    for run in executions:
        for n in run.nodes:
            if n.node_id not in known:
                raise ValueError(f"unknown measured node {n.node_id}")
            rows[n.node_id].append((n, run.crashed))

    node_profiles: dict[str, NodeProfile] = {}
    latency_samples_map: dict[str, int] = {}
    partial_map: dict[str, list[str]] = {}

    for node in graph.nodes:
        pairs = rows[node.id]
        good = [n for n, crashed in pairs if not crashed]
        latencies = [n.latency_ms for n in good if n.latency_ms is not None]

        p = NodeProfile(
            samples=len(pairs),
            latency_p50_ms=percentile(latencies, 0.5) or 0.0,
            latency_p95_ms=percentile(latencies, 0.95) or 0.0,
            failure_rate=mean(n.error is not None for n, _ in pairs) if pairs else 0.0,
            cache_hit_rate=mean(n.cached for n in good) if good else 0.0,
        )
        partial: list[str] = []
        if len(latencies) != len(good) or not good:
            partial.append("latency_ms")
        for field in ("tokens_in", "tokens_out", "cost_usd"):
            values = [getattr(n, field) for n in good]
            value = complete_mean(values)
            attr = field + "_mean"
            if hasattr(p, attr):
                setattr(p, attr, value)
            if value is None:
                partial.append(field)

        node_profiles[node.id] = p
        latency_samples_map[node.id] = len(latencies)
        partial_map[node.id] = partial

    split, cold = next(iter(conditions))
    total_meas = summarize(executions)

    # Build ProfileTotals from Measurements
    totals = ProfileTotals(
        wall_p50_ms=total_meas.wall_p50_ms or 0.0,
        wall_p95_ms=total_meas.wall_p95_ms or 0.0,
        tokens_in=int(total_meas.tokens_in) if total_meas.tokens_in is not None else None,
        tokens_out=int(total_meas.tokens_out) if total_meas.tokens_out is not None else None,
        cost_usd=total_meas.cost_usd,
        provider_calls=int(total_meas.provider_calls or 0),
        cache_hit_rate=total_meas.cache_hit_rate or 0.0,
    )

    # Attach critical path entries if provided
    cp_entries: list[CriticalPathEntry] = []
    if critical_path_ids:
        for nid in critical_path_ids:
            if nid in node_profiles:
                cp_entries.append(
                    CriticalPathEntry(
                        node=nid,
                        latency_p50_ms=node_profiles[nid].latency_p50_ms,
                        slack_ms=node_profiles[nid].slack_ms or 0.0,
                    )
                )

    return Profile(
        graph_id=graph.id,
        executions=len(executions),
        cold=cold,
        total=totals,
        nodes=node_profiles,
        critical_path=cp_entries,
        noise=noise,
        split=split,
        meta={
            "crashed": sum(r.crashed for r in executions),
            "task_ids": sorted({r.task_id for r in executions}),
            "partial": {nid: v for nid, v in partial_map.items() if v},
            "latency_samples": latency_samples_map,
        },
    )


def measure_noise(first: list[Execution], second: list[Execution]) -> NoiseBand:
    """Pair two repeat runs of the unmodified graph to calibrate natural variance."""

    def index(runs):
        out = {(r.task_id, r.repeat): r for r in runs}
        if len(out) != len(runs):
            raise ValueError("duplicate noise sample")
        return out

    a, b = index(first), index(second)
    if not a or a.keys() != b.keys():
        raise ValueError("noise runs must pair by task and repeat")
    conditions = {(r.graph_id, r.split, r.cold) for r in first + second}
    if len(conditions) != 1 or first[0].split == "test":
        raise ValueError("noise calibration requires one graph/condition on train or calib")
    latency, costs = [], []
    for key, before in a.items():
        after = b[key]
        if before.crashed or after.crashed or before.seed != after.seed:
            raise ValueError("noise pair crashed or has inconsistent seeds")
        if before.wall_ms is None or after.wall_ms is None or before.wall_ms <= 0:
            raise ValueError("noise requires measured positive baseline wall time")
        # Over the larger of the two, so the band stays under 100%. A gain cannot pass 100%, so a band
        # above that made a latency win impossible. Changed 1 Nov 2026, after seeing Groq calib results.
        latency.append(abs(after.wall_ms - before.wall_ms) / max(after.wall_ms, before.wall_ms) * 100)
        ca, cb = execution_total(before, "cost_usd"), execution_total(after, "cost_usd")
        if ca is not None and cb is not None and ca > 0:
            costs.append(abs(cb - ca) / ca * 100)
    return NoiseBand(
        latency_pct=percentile(latency, 0.95),
        samples=len(latency),
        split=first[0].split,
        cost_pct=percentile(costs, 0.95) if len(costs) == len(latency) else None,
    )
