"""An evidence card for one measured comparison, in the shape a survey of this field asks results to take.

Yue et al., "From Static Templates to Dynamic Runtime Graphs" (arXiv 2603.22386), ask papers to report graph
metrics, cost per success rather than raw cost, offline against deployment cost, repeats and failure analysis.
This computes those from runs already measured. It spends nothing and calls no model.

The intervals are a paired bootstrap over tasks: each resample picks tasks, keeping a task's before and after
together, so a task that is slow on both sides doesn't read as noise. Crashed tasks are counted, never resampled.
A range is omitted, not guessed, when a side has no measurements.
"""

from __future__ import annotations

import random

from synapse0g.graph.core.sir import Graph

RESAMPLES = 2000
SEED = 7


def _median(values: list[float]) -> float:
    ordered = sorted(values)
    mid = len(ordered) // 2
    return ordered[mid] if len(ordered) % 2 else (ordered[mid - 1] + ordered[mid]) / 2


def _interval(pairs: list[tuple[float, float]], statistic) -> dict | None:
    if len(pairs) < 2:
        return None
    rng = random.Random(SEED)
    values = sorted(statistic(rng.choices(pairs, k=len(pairs))) for _ in range(RESAMPLES))
    return {"point": round(statistic(pairs), 3), "low": round(values[int(0.025 * RESAMPLES)], 3),
            "high": round(values[int(0.975 * RESAMPLES)], 3), "confidence": 0.95, "n": len(pairs)}


def _speedup(pairs: list[tuple[float, float]]) -> float:
    """The median task's own ratio, which is the speedup we quote."""
    return _median([a / b for a, b in pairs if b])


def _total_speedup(pairs: list[tuple[float, float]]) -> float:
    """All the time before over all the time after. A few slow tasks dominate this one, so we show both."""
    return sum(a for a, _ in pairs) / sum(b for _, b in pairs)


def _reduction(pairs: list[tuple[float, float]]) -> float:
    before = sum(a for a, _ in pairs)
    return 100 * (before - sum(b for _, b in pairs)) / before if before else 0.0


def _paired(before: list[dict], after: list[dict], read) -> list[tuple[float, float]]:
    pairs = []
    for a, b in zip(before, after, strict=True):
        x, y = read(a), read(b)
        if x is not None and y is not None and not a.get("crashed") and not b.get("crashed"):
            pairs.append((float(x), float(y)))
    return pairs


def _tokens(row: dict) -> float | None:
    if row.get("tokens_in") is None or row.get("tokens_out") is None:
        return None
    return row["tokens_in"] + row["tokens_out"]


def _cost_per_success(rows: list[dict]) -> float | None:
    costs = [r.get("cost_usd") for r in rows if not r.get("crashed")]
    passed = sum(1 for r in rows if r.get("passed"))
    if not passed or not costs or any(c is None for c in costs):
        return None
    return round(sum(costs) / passed, 7)


def card(rows: dict[str, list[dict]], *, verdict: dict | None = None, ledger: dict | None = None,
         original: Graph | None = None, meta: dict | None = None) -> dict:
    before, after = rows["sequential"], rows["optimized"]
    outcome = {"broke": 0, "fixed": 0, "kept_passing": 0, "kept_failing": 0}
    for a, b in zip(before, after, strict=True):
        was, now = bool(a.get("passed")), bool(b.get("passed"))
        name = "broke" if was and not now else "fixed" if now and not was else "kept_passing" if now else "kept_failing"
        outcome[name] += 1
    wall = _paired(before, after, lambda r: r.get("wall_ms"))
    tokens = _paired(before, after, _tokens)
    cost = _paired(before, after, lambda r: r.get("cost_usd"))
    return {
        "meta": meta or {},
        "tasks": len(before),
        "speedup": _interval(wall, _speedup),
        "speedup_of_total_time": _interval(wall, _total_speedup),
        "tokens_reduction_pct": _interval(tokens, _reduction) if tokens else None,
        "cost_reduction_pct": _interval(cost, _reduction) if cost else None,
        "cost_per_success_usd": {"before": _cost_per_success(before), "after": _cost_per_success(after)},
        "outcomes": outcome,
        "crashed": {"before": sum(1 for r in before if r.get("crashed")),
                    "after": sum(1 for r in after if r.get("crashed"))},
        "verdict": verdict.get("decision") if verdict else None,
        "verdict_reason": verdict.get("reason") if verdict else None,
        "optimization_cost": ledger,
        "graph": graph_metrics(original) if original else None,
    }


def graph_metrics(graph: Graph) -> dict:
    """Size and depth, by the dependencies that must hold. A map step counts once, however wide it fans out."""
    ids = [n.id for n in graph.nodes]
    edges = [(e.src, e.dst) for e in graph.data_edges()]
    parents: dict[str, list[str]] = {i: [] for i in ids}
    for src, dst in edges + [(e.src, e.dst) for e in graph.effect_edges()]:
        if src in parents and dst in parents:
            parents[dst].append(src)
    level: dict[str, int] = {}

    def depth(node: str) -> int:
        if node not in level:
            level[node] = 1 + max((depth(p) for p in parents[node]), default=0)
        return level[node]

    return {"nodes": len(ids), "edges": len(edges), "depth": max((depth(i) for i in ids), default=0)}


def recurring_sequences(runs: list[dict], *, length: int = 2, at_least: int = 2) -> list[dict]:
    """Calls that repeat in the same order across runs, by request hash. Candidates only: nothing is rewritten.

    Mining trace history for repeated sequences is the idea in AWO (Abuzakuk et al., arXiv 2601.22037), which
    turns them into composite tools. An exact match on the request is the only thing a hash can prove, so it
    finds work that could be reused, and it can't see two prompts that merely mean the same thing.
    """
    seen: dict[tuple[str, ...], dict] = {}
    for run in runs:
        calls = [c for c in run.get("calls", []) if c.get("request_hash")]
        counted: set[tuple[str, ...]] = set()
        for start in range(len(calls) - length + 1):
            window = calls[start:start + length]
            key = tuple(c["request_hash"] for c in window)
            entry = seen.setdefault(key, {"models": [c["model"] for c in window], "times": 0, "runs": 0, "tokens": 0})
            entry["times"] += 1
            entry["tokens"] += sum((c.get("tokens_in") or 0) + (c.get("tokens_out") or 0) for c in window)
            if key not in counted:
                entry["runs"] += 1
                counted.add(key)
    found = [{**e, "tokens_each_time": e["tokens"] // e["times"]} for e in seen.values() if e["times"] >= at_least]
    return sorted(found, key=lambda e: -e["tokens"])[:10]
