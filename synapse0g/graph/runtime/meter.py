"""Provider calls made from inside a node that is not itself a model node.

A `model` node makes exactly one provider call, and the executor reads its tokens off the
response. Some nodes do more: a CrewAI task is a whole agent loop, with one call per thought.
Those calls still go through `providers.chat`, and `providers.chat` reports each one to the meter
active for the node being run, so their tokens are measured in the same single place.

Rule 5 holds for the sum: when any call in a node did not report a figure, that node's total is
`None`, never the sum of the calls that did.
"""

from __future__ import annotations

import contextlib
import contextvars
import threading
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Meter:
    calls: int = 0
    _tokens_in: list[int | None] = field(default_factory=list)
    _tokens_out: list[int | None] = field(default_factory=list)
    _cost: list[float | None] = field(default_factory=list)
    model_ids: list[str] = field(default_factory=list)
    providers: list[str] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def add(self, result: Any) -> None:
        with self._lock:
            self.calls += 1
            self._tokens_in.append(result.tokens_in)
            self._tokens_out.append(result.tokens_out)
            self._cost.append(result.cost_usd)
            if result.model_id not in self.model_ids:
                self.model_ids.append(result.model_id)
            if result.provider not in self.providers:
                self.providers.append(result.provider)

    @staticmethod
    def _total(values: list) -> Any:
        if not values or any(v is None for v in values):
            return None
        return sum(values)

    @property
    def tokens_in(self) -> int | None:
        return self._total(self._tokens_in)

    @property
    def tokens_out(self) -> int | None:
        return self._total(self._tokens_out)

    @property
    def cost_usd(self) -> float | None:
        return self._total(self._cost)

    @property
    def model_id(self) -> str | None:
        return ",".join(self.model_ids) or None

    @property
    def provider(self) -> str | None:
        return ",".join(self.providers) or None


_ACTIVE: contextvars.ContextVar[Meter | None] = contextvars.ContextVar("synapse_meter", default=None)


@contextlib.contextmanager
def measuring() -> Iterator[Meter]:
    """Count every provider call made inside this block, including from worker threads started
    with `asyncio.to_thread`, which copies the context."""
    meter = Meter()
    token = _ACTIVE.set(meter)
    try:
        yield meter
    finally:
        _ACTIVE.reset(token)


def record(result: Any) -> None:
    """Called by `providers.chat` for every call. Does nothing outside `measuring()`."""
    meter = _ACTIVE.get()
    if meter is not None:
        meter.add(result)
