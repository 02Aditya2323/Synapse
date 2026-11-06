"""The baselines Synapse has to beat (docs/BENCHMARKS.md §3).

    sequential        the workflow as written, one node at a time, no cache
    cache_only_cold   a memoization layer and no graph analysis, cache empty
    cache_only_warm   the same layer, second time over the same tasks

The cache-only baseline is what a competent engineer adds in an afternoon, so it is the
one that matters. Its cold and warm rows are never merged. The framework-default baseline
is a stretch goal and is not here.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from synapse0g.graph.core.store import Store
from synapse0g.graph.runtime.cache import Cache


def cache_everything(root: Path) -> Cache:
    """A cache consulted for every node, not only those `memoize` marked. Whether a node
    may be stored at all is still the cache's own decision."""
    cache = Cache(Store(root))
    cache.cache_everything = True
    return cache


def fresh_cache() -> Cache:
    """An empty cache in its own directory, so a cold row can never be quietly warm."""
    return cache_everything(Path(tempfile.mkdtemp(prefix="synapse-baseline-")))


def marked_only_cache() -> Cache:
    """An empty cache consulted only for nodes `memoize` marked: what an optimized graph
    runs with. One per task, because tasks can share a query and a later task would start
    warm. Hits here are duplicates within one task."""
    return Cache(Store(Path(tempfile.mkdtemp(prefix="synapse-optimized-"))))
