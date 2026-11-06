"""A semantic cache that the firewall judges before anything trusts it. Off unless asked for.

Similar wording makes a step a candidate for reuse, and only that. `semantic_check` runs each task
with and without it, and any task that breaks makes the verdict BLOCK. Similarity is word overlap, so
looking up costs no tokens and no embedding call. That is cruder than embeddings: "the price today"
and "the price yesterday" look close, which is exactly what the check is there to catch.
"""

from __future__ import annotations

import asyncio
import re
import tempfile
from pathlib import Path

from synapse0g.graph.core.sir import Graph, Node
from synapse0g.graph.core.store import Store
from synapse0g.graph.runtime.cache import Cache, CacheHit, CacheMiss
from synapse0g.graph.runtime.executor import ExecutionMode
from synapse0g.graph.runtime.scheduler import run_graph

_WORD = re.compile(r"[a-z0-9]+")


def similarity(a: str, b: str) -> float:
    x, y = set(_WORD.findall(a.lower())), set(_WORD.findall(b.lower()))
    return len(x & y) / len(x | y) if x or y else 0.0


class SemanticCache(Cache):
    cache_everything = True

    def __init__(self, store: Store, *, threshold: float = 0.8):
        super().__init__(store)
        self.threshold = threshold
        self.seen: dict[tuple[str, object], list[tuple[str, str]]] = {}
        self.pending: dict[str, str] = {}
        self.semantic_hits = 0

    def note_inputs(self, node: Node, node_hash: str, resolved: dict) -> None:
        self.pending[node_hash] = " ".join(str(v) for _, v in sorted(resolved.items()))

    def get(self, node: Node, node_hash: str) -> CacheHit | CacheMiss:
        exact = super().get(node, node_hash)
        text = self.pending.get(node_hash, "")
        group = self.seen.setdefault((node.target, node.meta.get("impl")), [])
        if isinstance(exact, CacheHit):
            return exact
        best = max(group, key=lambda row: similarity(text, row[1]), default=None)
        if best is not None and best[0] != node_hash and similarity(text, best[1]) >= self.threshold:
            near = super().get(node, best[0])
            if isinstance(near, CacheHit):
                self.semantic_hits += 1
                return near
        group.append((node_hash, text))
        return exact


async def _pass(graph: Graph, tasks: list[dict], cache: Cache | None) -> list:
    return [await run_graph(graph, task_input=t["input"], task_id=t["id"], cache=cache, mode=ExecutionMode.VERIFY)
            for t in tasks]


def semantic_check(graph: Graph, tasks: list[dict], *, threshold: float = 0.8) -> dict:
    """Every task with and without the semantic cache. A task that breaks blocks it."""
    with tempfile.TemporaryDirectory(prefix="synapse-semantic-", ignore_cleanup_errors=True) as tmp:
        store = Store(Path(tmp))
        cache = SemanticCache(store, threshold=threshold)
        plain = asyncio.run(_pass(graph, tasks, None))
        cached = asyncio.run(_pass(graph, tasks, cache))
        store.close()

    def output(run):
        return next(iter(run.outputs.values()), None)

    broken = [t["id"] for t, a, b in zip(tasks, plain, cached, strict=True)
              if bool(t["check"](output(a))) and not bool(t["check"](output(b)))]

    def spent(runs) -> int | None:
        total = 0
        for run in runs:
            if run.tokens_in is None or run.tokens_out is None:
                return None
            total += run.tokens_in + run.tokens_out
        return total

    before, after = spent(plain), spent(cached)
    return {
        "decision": "BLOCK" if broken else ("PASS" if cache.semantic_hits else "NO_HITS"),
        "threshold": threshold,
        "semantic_hits": cache.semantic_hits,
        "broken": broken,
        "tokens_without": before,
        "tokens_with": after,
        "tasks": len(tasks),
        "note": "Similar wording only nominates a reuse. These tasks decide whether it was safe.",
    }
