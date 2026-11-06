"""Hold less: what is kept, for how long, and whether keeping it pays for itself.

Three defaults, because storage can eat what optimization saves:

- **Measurements only.** A saved execution keeps timings, tokens and a digest of its outputs,
  not the outputs. They often hold retrieved documents and user data, and nothing reads them
  back. `SYNAPSE_CAPTURE=full` opts in, for debugging.
- **Raw runs expire.** `SYNAPSE_RETAIN_DAYS` (default 30). Graphs, profiles and verdicts stay,
  because they are what a run was measured for. Set 0 to keep runs forever.
- **The cache is capped.** `SYNAPSE_CACHE_MB` (default 256). Oldest entries go first. Age is
  not last use: a popular old entry can be evicted, and it costs one model call to get back.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from typing import Any

DEFAULT_RETAIN_DAYS = 30.0
DEFAULT_CACHE_MB = 256.0


def _number(name: str, default: float) -> float:
    raw = os.environ.get(name, "").split("#")[0].strip()
    try:
        return float(raw) if raw else default
    except ValueError:
        raise ValueError(f"{name} must be a number, got {raw!r}") from None


def retain_days() -> float:
    return _number("SYNAPSE_RETAIN_DAYS", DEFAULT_RETAIN_DAYS)


def cache_limit_mb() -> float:
    return _number("SYNAPSE_CACHE_MB", DEFAULT_CACHE_MB)


def capture_full() -> bool:
    return os.environ.get("SYNAPSE_CAPTURE", "").strip().lower() == "full"


def enforce_cache_limit(store: Any, limit_mb: float | None = None) -> int:
    """Evict the oldest cache entries until the cache fits. Returns how many went."""
    limit = (cache_limit_mb() if limit_mb is None else limit_mb) * 1e6
    items = store.cache_items()
    # By unique blob: entries sharing a value share its bytes, so space only comes back
    # when the last entry using a blob goes.
    used = sum({digest: size for _, digest, size, _ in items}.values())
    evicted = 0
    for node_hash, digest, size, _ in sorted(items, key=lambda i: i[3]):
        if used <= limit:
            break
        store.cache_delete(node_hash)
        evicted += 1
        if digest and not store.cache_blob_in_use(digest):
            store.blob_delete(digest)
            used -= size
    return evicted


def prune(store: Any, *, days: float | None = None, cache_mb: float | None = None) -> dict:
    """Apply both limits. `days=0` keeps every run."""
    days = retain_days() if days is None else days
    removed = 0
    if days > 0:
        cutoff = (datetime.now(UTC) - timedelta(days=days)).isoformat()
        removed = store.delete_executions_before(cutoff)
    return {"executions_removed": removed, "cache_entries_evicted": enforce_cache_limit(store, cache_mb)}


def report(store: Any) -> dict:
    """What we hold against what holding it saved. Savings count only what the cache really
    avoided on recorded runs, so a store with no hits shows none."""
    held = store.size_report()
    hits, tokens, cost = store.cache_savings()
    return {
        "mode": store.mode,
        "bytes": held,
        "total_mb": round(sum(held.values()) / 1e6, 3),
        "cache_hits_recorded": hits,
        "tokens_saved": tokens,
        "cost_saved_usd": cost,
        "bytes_per_token_saved": round(sum(held.values()) / tokens, 1) if tokens else None,
        "limits": {"retain_days": retain_days(), "cache_mb": cache_limit_mb(), "capture_full": capture_full()},
    }
