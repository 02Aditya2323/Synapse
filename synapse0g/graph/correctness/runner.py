"""Local validation runner for expanded DAGs, pending integration with Aryan's runtime.

Registered handlers are trusted code. Effect declarations are a contract, not a Python
sandbox. Protected targets are NEVER looked up in the live handler registry: only
explicit stubs run. This runner deliberately rejects unresolved control-flow regions.
"""

from __future__ import annotations

import asyncio
import copy
import inspect
import time
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, Field

from synapse0g.graph.core.effects import is_side_effecting, may_reuse
from synapse0g.graph.core.hashing import fingerprint, node_hash
from synapse0g.graph.core.sir import Graph, Ref
from synapse0g.graph.profile.collect import Execution, NodeMeasurement
from synapse0g.graph.profile.costmodel import ready_nodes


class Task(BaseModel):
    id: str
    inputs: dict[str, Any] = Field(default_factory=dict)
    split: str = "calib"
    seed: int | None = None


class NodeResult(BaseModel):
    outputs: dict[str, Any]
    tokens_in: int | None = Field(default=None, ge=0)
    tokens_out: int | None = Field(default=None, ge=0)
    cost_usd: float | None = Field(default=None, ge=0)
    provider: str | None = None
    model_id: str | None = None


class ValidationRunner:
    def __init__(
        self,
        handlers: dict[str, Callable],
        *,
        stubs: dict[str, Callable] | None = None,
        max_concurrency: int = 1,
        providers=None,
        provider_limits=None,
    ):
        if max_concurrency < 1 or any(v < 1 for v in (provider_limits or {}).values()):
            raise ValueError("concurrency limits must be positive")
        self.handlers, self.stubs = handlers, stubs or {}
        self.max_concurrency = max_concurrency
        self.providers, self.provider_limits = providers or {}, provider_limits or {}
        self.cache = {}

    def clear_cache(self):
        self.cache.clear()

    async def run(self, graph: Graph, task: Task, *, cold=True, repeat=0) -> Execution:
        graph.validate_graph()
        if graph.regions:
            raise ValueError("validation runner needs an expanded execution DAG, not regions")
        if cold:
            self.clear_cache()
        for n in graph.nodes:
            if is_side_effecting(n) and n.target not in self.stubs:
                raise ValueError(f"protected target {n.target} requires a declared stub")
        started = time.perf_counter()
        run = Execution(task_id=task.id, graph_id=graph.id, split=task.split, cold=cold, seed=task.seed, repeat=repeat)
        values, done, measurements = {}, set(), {}
        by_id = {n.id: n for n in graph.nodes}
        pending, active = list(by_id), {}
        cap = graph.meta.get("max_concurrency", self.max_concurrency)
        if type(cap) is not int or cap < 1:
            raise ValueError("invalid graph concurrency")
        cap = min(cap, self.max_concurrency)

        async def execute(nid):
            node = by_id[nid]
            begin = time.perf_counter()
            measurement = NodeMeasurement(node_id=nid, started_ms=(begin - started) * 1000)
            try:
                args = {
                    k: copy.deepcopy(values[a.node][a.output] if isinstance(a, Ref) else a.value)
                    for k, a in node.args.items()
                }
                # Use Aryan's canonical node_hash; extend the key with task inputs and seed
                # so the same node with different task inputs never shares a cache entry.
                resolved = dict(args)
                node_key = node_hash(node, resolved)
                task_key = fingerprint({"inputs": task.inputs, "seed": task.seed, "node_seed": node.meta.get("seed")})
                key = node_key + ":" + task_key
                saved = self.cache.get(key)
                caching = bool(node.meta.get("memoize")) and not is_side_effecting(node)
                # may_reuse returns None if reuse is legal; we also check age for reads
                age_s = time.monotonic() - saved[0] if saved else 0
                reuse_ok = caching and saved is not None and may_reuse(node) is None
                if node.staleness_s is not None and age_s > node.staleness_s:
                    reuse_ok = False
                if reuse_ok:
                    result = saved[1].model_copy(deep=True)
                    measurement.cached = True
                    measurement.tokens_in = measurement.tokens_out = 0
                    measurement.cost_usd = 0
                else:
                    if is_side_effecting(node):
                        handler = self.stubs[node.target]
                        run.actions.append(
                            {"node": nid, "target": node.target, "args": copy.deepcopy(args), "stubbed": True}
                        )
                    else:
                        handler = self.handlers[node.target]
                    if inspect.iscoroutinefunction(handler):
                        result = await handler(args, task.model_copy(deep=True))
                    else:
                        result = await asyncio.to_thread(handler, args, task.model_copy(deep=True))
                    if not isinstance(result, NodeResult):
                        result = NodeResult(outputs={"value": result})
                    measurement.tokens_in, measurement.tokens_out = result.tokens_in, result.tokens_out
                    measurement.cost_usd = result.cost_usd
                    if caching and may_reuse(node) is None:
                        self.cache[key] = (time.monotonic(), result.model_copy(deep=True))
                if not set(node.outputs) <= result.outputs.keys():
                    raise ValueError("handler omitted declared outputs")
                values[nid] = result.outputs
                measurement.provider, measurement.model_id = result.provider, result.model_id
            except Exception as exc:  # noqa: BLE001 - record plugin/handler failures in the returned report
                measurement.error = f"{type(exc).__name__}: {exc}"
            measurement.latency_ms = (time.perf_counter() - begin) * 1000
            measurements[nid] = measurement
            return measurement

        while pending or active:
            if run.error is None:
                for nid in ready_nodes(graph, pending, done):
                    if len(active) >= cap:
                        break
                    provider = self.providers.get(by_id[nid].target)
                    used = sum(self.providers.get(by_id[n].target) == provider for n in active.values())
                    if provider in self.provider_limits and used >= self.provider_limits[provider]:
                        continue
                    active[asyncio.create_task(execute(nid))] = nid
                    pending.remove(nid)
            if not active:
                if pending and run.error is None:
                    run.error = "dependency deadlock"
                break
            finished, _ = await asyncio.wait(active, return_when=asyncio.FIRST_COMPLETED)
            for future in finished:
                nid = active.pop(future)
                measurement = await future
                done.add(nid)
                if measurement.error:
                    run.error = measurement.error
            # Drain already-started work after a failure; never hide its usage.
        run.nodes = [measurements[n.id] for n in graph.nodes if n.id in measurements]
        if run.error is None:
            run.output = {key: values[ref.node][ref.output] for key, ref in graph.outputs.items()}
        run.wall_ms = (time.perf_counter() - started) * 1000
        return run


async def run_pairs(
    original: Graph, candidate: Graph, tasks: list[Task], runner: ValidationRunner, *, include_warm=True, repeats=1
) -> list[tuple[Execution, Execution]]:
    """Independent cold/warm caches per graph and task; alternate order to reduce timing bias."""
    if repeats < 1:
        raise ValueError("repeats must be positive")
    pairs = []
    for index, task in enumerate(tasks):
        for repeat in range(repeats):
            results = {}
            order = [("before", original), ("after", candidate)]
            if (index + repeat) % 2:
                order.reverse()
            for label, graph in order:
                try:
                    results[label, True] = await runner.run(graph, task, cold=True, repeat=repeat)
                    if include_warm:
                        results[label, False] = await runner.run(graph, task, cold=False, repeat=repeat)
                except Exception as exc:  # noqa: BLE001 - record plugin/handler failures in the returned report
                    for cold in [True, False] if include_warm else [True]:
                        results[label, cold] = Execution(
                            task_id=task.id,
                            graph_id=graph.id,
                            split=task.split,
                            cold=cold,
                            repeat=repeat,
                            seed=task.seed,
                            error=f"{type(exc).__name__}: {exc}",
                        )
            pairs.extend(
                (results["before", cold], results["after", cold])
                for cold in ([True, False] if include_warm else [True])
            )
    return pairs
