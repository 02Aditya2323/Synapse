"""The Net Savings Ledger: what optimizing cost, what it saves per run, and when it pays back.

Checking an optimization spends tokens: the runs that build a noise band, try candidates and feed
the firewall, plus warm-ups. A saving that ignores them flatters itself. Every run goes through
`charge`, so this is a count of real runs, not an estimate. Time spent is the runs' summed wall
clock, one after another.

A saving is an average on the tasks measured, and it applies to runs that look like those tasks. A
value nobody reported stays unknown, and an unknown makes the total unknown, never smaller.
"""

from __future__ import annotations

import contextvars
import math
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

from synapse0g.graph.profile.collect import Execution as Measured
from synapse0g.graph.profile.collect import execution_total


@dataclass
class Tally:
    runs: int = 0
    tokens: float | None = 0.0
    cost_usd: float | None = 0.0
    wall_ms: float = 0.0

    def add(self, run: Measured) -> None:
        self.runs += 1
        self.wall_ms += run.wall_ms or 0.0
        tokens_in, tokens_out = execution_total(run, "tokens_in"), execution_total(run, "tokens_out")
        cost = execution_total(run, "cost_usd")
        self.tokens = None if self.tokens is None or tokens_in is None or tokens_out is None else (
            self.tokens + tokens_in + tokens_out)
        self.cost_usd = None if self.cost_usd is None or cost is None else self.cost_usd + cost


_current: contextvars.ContextVar[Tally | None] = contextvars.ContextVar("synapse_ledger", default=None)


@contextmanager
def spending() -> Iterator[Tally]:
    """Everything charged inside this block lands on the tally it yields."""
    tally = Tally()
    token = _current.set(tally)
    try:
        yield tally
    finally:
        _current.reset(token)


def charge(run: Measured) -> None:
    tally = _current.get()
    if tally is not None:
        tally.add(run)


def _mean(runs: list[Measured], read) -> float | None:
    values = [read(r) for r in runs if not r.crashed]
    return None if not values or any(v is None for v in values) else sum(values) / len(values)


def _tokens(run: Measured) -> float | None:
    tokens_in, tokens_out = execution_total(run, "tokens_in"), execution_total(run, "tokens_out")
    return None if tokens_in is None or tokens_out is None else tokens_in + tokens_out


def saved_per_run(before: Iterable[Measured], after: Iterable[Measured]) -> dict:
    before, after = list(before), list(after)

    def gap(read) -> float | None:
        a, b = _mean(before, read), _mean(after, read)
        return None if a is None or b is None else a - b

    return {
        "tokens": gap(_tokens),
        "cost_usd": gap(lambda r: execution_total(r, "cost_usd")),
        "wall_ms": gap(lambda r: r.wall_ms),
    }


def _break_even(spent: float | None, saved: float | None) -> dict:
    """Runs until the saving covers the spend. `never` when it saves nothing, and null when unknown."""
    if spent is None or saved is None:
        return {"runs": None, "never": False}
    if saved <= 0:
        return {"runs": None, "never": True}
    return {"runs": max(1, math.ceil(spent / saved)), "never": False}


def report(spent: Tally, saved: dict, *, fake_provider: bool = False) -> dict:
    return {
        "spent": {"runs": spent.runs, "tokens": spent.tokens, "cost_usd": spent.cost_usd, "wall_ms": spent.wall_ms},
        "saved_per_run": saved,
        "break_even": {
            "tokens": _break_even(spent.tokens, saved["tokens"]),
            "cost": _break_even(spent.cost_usd, saved["cost_usd"]),
            "wall": _break_even(spent.wall_ms, saved["wall_ms"]),
        },
        "fake_provider": fake_provider,
    }


def add_to(store: Any, graph_id: str, spent: Tally, saved: dict, *, fake_provider: bool = False) -> dict:
    """Add this spend to what the graph has already cost, and keep the latest saving."""
    old = (store.get_artifact("ledger", graph_id) or {}).get("spent")
    if old:
        spent = Tally(
            runs=spent.runs + old["runs"],
            tokens=None if spent.tokens is None or old["tokens"] is None else spent.tokens + old["tokens"],
            cost_usd=None if spent.cost_usd is None or old["cost_usd"] is None else spent.cost_usd + old["cost_usd"],
            wall_ms=spent.wall_ms + old["wall_ms"],
        )
    result = report(spent, saved, fake_provider=fake_provider)
    store.put_artifact("ledger", graph_id, result)
    return result


def describe(ledger: dict) -> str:
    spent, saved, even = ledger["spent"], ledger["saved_per_run"], ledger["break_even"]

    def amount(value, unit: str) -> str:
        if value is None:
            return "unknown"
        return f"${value:.6f}" if unit == "$" else f"{value:,.0f} {unit}"

    def pays(row: dict) -> str:
        if row["never"]:
            return "never at this saving"
        return "unknown" if row["runs"] is None else f"after {row['runs']:,} runs"

    lines = [
        f"Spent checking it: {spent['runs']} runs, {amount(spent['tokens'], 'tokens')}, "
        f"{amount(spent['cost_usd'], '$')}, {amount(spent['wall_ms'], 'ms')}",
        f"Saved per run: {amount(saved['tokens'], 'tokens')}, {amount(saved['cost_usd'], '$')}, "
        f"{amount(saved['wall_ms'], 'ms')}",
        f"Pays back: tokens {pays(even['tokens'])}; cost {pays(even['cost'])}; time {pays(even['wall'])}",
    ]
    if ledger.get("fake_provider"):
        lines.append("Demo model: this shows the accounting, not real spend.")
    return "\n".join(lines)
