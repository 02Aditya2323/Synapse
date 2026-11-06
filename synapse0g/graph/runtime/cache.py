"""The content-addressed node cache: cross-run, cross-workflow, cold/warm aware.

Keyed by `node_hash` (docs/IR_SPEC.md section 5), so two nodes in two different
workflows that do the same work share one entry. Backed by the store's `cache` table
and its blob directory, so a hit survives the process.

Three rules this module exists to keep:

1. **It never decides what is safe.** `synapse.core.effects.may_reuse` does that. The
   cache refuses to serve an entry for a node the lattice says may not be re-used, as a
   second line of defence, but the decision is not duplicated here.
2. **A `read` node's entry expires.** `staleness_s` is checked on every lookup against
   the entry's age. An expired entry is a miss, not a hit, and is reported as such.
3. **Hits are recorded, never hidden.** Every hit becomes a node run with
   `cached=True`, which is what lets the profile separate "fast because optimized" from
   "fast because warm" -- the distinction we have to be able to make on stage when
   someone asks whether the speedup is just a cache (docs/CORRECTNESS.md).
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from synapse0g.graph.core import effects
from synapse0g.graph.core.hashing import fingerprint
from synapse0g.graph.core.retention import enforce_cache_limit
from synapse0g.graph.core.sir import Effect, Node, utc_now
from synapse0g.graph.core.store import Store


@dataclass(frozen=True)
class CacheHit:
    value: Any
    age_s: float
    tokens_in: int | None
    tokens_out: int | None
    cost_usd: float | None
    latency_ms: float  # what the original execution took: what the hit saved
    model_id: str | None


@dataclass(frozen=True)
class CacheMiss:
    """Why there was no hit. `reason` is shown in the live-run view, because "miss
    because stale" and "miss because never seen" mean very different things when you
    are looking at a disappointing warm number."""

    reason: str


class Cache:
    """One cache over one store. Several executions share it; that is the point."""

    def __init__(self, store: Store, *, enabled: bool = True, scope: str | None = None):
        self.store = store
        self.enabled = enabled
        # Who this cache serves. With a scope, one user's answers can never reach another's run.
        self.scope = scope
        self.hits = 0
        self.misses = 0
        self._puts = 0

    def _key(self, node_hash: str) -> str:
        return node_hash if self.scope is None else fingerprint([node_hash, self.scope])

    # ---- lookup ------------------------------------------------------------
    def get(self, node: Node, node_hash: str) -> CacheHit | CacheMiss:
        if not self.enabled:
            return CacheMiss("cache disabled for this execution (cold measurement)")

        if finding := effects.may_reuse(node, transform="cache"):
            # Defence in depth. A pass should never have marked this node memoizable;
            # if one did, the cache still refuses and the finding says why.
            self.misses += 1
            return CacheMiss(f"not cacheable: {finding.message}")

        entry = self.store.cache_get(self._key(node_hash))
        if entry is None:
            self.misses += 1
            return CacheMiss("no entry for this content hash")

        age_s = max(0.0, time.time() - float(entry.get("created_mono") or 0.0))
        if node.effect == Effect.READ:
            if node.staleness_s is None:  # unreachable via may_reuse; kept explicit
                self.misses += 1
                return CacheMiss("read node declares no staleness window")
            if age_s > node.staleness_s:
                self.misses += 1
                return CacheMiss(f"entry is {age_s:.0f}s old, past this node's {node.staleness_s:.0f}s staleness")

        self.hits += 1
        return CacheHit(
            value=self.store.get_blob(entry["blob"]),
            age_s=age_s,
            tokens_in=entry.get("tokens_in"),
            tokens_out=entry.get("tokens_out"),
            cost_usd=entry.get("cost_usd"),
            latency_ms=float(entry.get("latency_ms") or 0.0),
            model_id=entry.get("model_id"),
        )

    # ---- store -------------------------------------------------------------
    def put(
        self,
        node: Node,
        node_hash: str,
        value: Any,
        *,
        latency_ms: float,
        tokens_in: int | None = None,
        tokens_out: int | None = None,
        cost_usd: float | None = None,
        model_id: str | None = None,
        seed: int | None = None,
    ) -> bool:
        """Record a result. Returns whether it was stored.

        A node the lattice forbids re-using is not written at all -- an entry that may
        never be served is a trap waiting for a future pass to spring.
        """
        if not self.enabled or effects.may_reuse(node, transform="cache") is not None:
            return False
        self.store.cache_put(
            {
                "node_hash": self._key(node_hash),
                "target": node.target,
                "effect": str(node.effect),
                "blob": self.store.put_blob(value),
                "tokens_in": tokens_in,
                "tokens_out": tokens_out,
                "cost_usd": cost_usd,
                "latency_ms": latency_ms,
                "seed": seed,
                "model_id": model_id,
                "created_at": utc_now(),
                "created_mono": time.time(),
            }
        )
        self._puts += 1
        if self._puts % 25 == 0:  # often enough to hold the limit, without a size scan per write
            enforce_cache_limit(self.store)
        return True

    # ---- housekeeping ------------------------------------------------------
    def clear(self) -> int:
        """Make the next measurement genuinely cold. `synapse compare` calls this
        between the cold and warm halves of a comparison."""
        self.hits = 0
        self.misses = 0
        return self.store.cache_clear()

    @property
    def hit_rate(self) -> float | None:
        total = self.hits + self.misses
        return self.hits / total if total else None
