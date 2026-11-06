"""The scheduler: an async ready-queue over the dependency graph.

A node becomes ready when all its data dependencies have values **and** all its
effect-edge predecessors have completed. Ready work runs concurrently up to a global
limit and a per-provider limit.

**Effect ordering is enforced here, not by the passes** (SYSTEM.md section 7.1). The
scheduler derives the effect edges from the graph itself and refuses to violate them
whatever a pass claims, and it additionally asserts that side-effecting nodes execute
one at a time in program order. Defence in depth: a buggy pass must produce a slow run,
never an unsafe one.

**A map region is one unit of the IR and *k* chains at run time.** The region's `over`
collection is resolved, each element gets its own pass through the region body, and the
chains run concurrently. That is where the headline `research_fanout` win comes from:
the IR stays four nodes deep while the execution is *k* wide.

Concurrency is read from `graph.meta["max_concurrency"]` and defaults to **1**. An
un-optimized graph therefore executes exactly as a naive engine would, which is what
makes the measured before/after comparison honest -- the "before" run is not a
hobbled version of the "after" one, it is the obvious implementation.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from synapse0g.graph.core import effects
from synapse0g.graph.core.hashing import fingerprint
from synapse0g.graph.core.sir import Graph, Node, Region, SIRError
from synapse0g.graph.runtime.cache import Cache
from synapse0g.graph.runtime.executor import (
    Execution,
    ExecutionMode,
    Executor,
    NodeRun,
    bind_inputs,
    output_values,
    provider_of,
    resolve_args,
)

Values = dict[tuple[str, str], Any]


class SchedulerError(RuntimeError):
    """The schedule itself is impossible (a cycle, an unresolvable region). Distinct
    from a node failing: this is a graph bug and must not look like a slow workflow."""


@dataclass
class _Unit:
    """A schedulable unit: one plain node, or one whole map region."""

    id: str
    nodes: list[str]
    region: Region | None = None
    deps: set[str] = field(default_factory=set)

    @property
    def is_map(self) -> bool:
        return self.region is not None and self.region.kind == "map"


class Scheduler:
    def __init__(
        self,
        *,
        executor: Executor | None = None,
        cache: Cache | None = None,
        mode: ExecutionMode = ExecutionMode.LIVE,
        concurrency: int | None = None,
        allow_irreversible: bool = False,
        implementations: dict[str, Callable[..., Any]] | None = None,
        on_event: Callable[[dict], None] | None = None,
        max_map_width: int = 64,
    ):
        self.on_event = on_event or (lambda e: None)
        self.executor = executor or Executor(
            cache=cache,
            mode=mode,
            allow_irreversible=allow_irreversible,
            implementations=implementations,
            on_event=self.on_event,
        )
        self.mode = mode
        self.cache = cache
        self.concurrency_override = concurrency
        self.max_map_width = max_map_width
        self._provider_locks: dict[str, asyncio.Semaphore] = {}
        self._side_effects_in_flight = 0
        self._side_effect_log: list[str] = []
        self._worker_seq = 0
        self._speculative: dict[str, asyncio.Task] = {}
        self._speculation_began: set[str] = set()

    # ---------------------------------------------------------------- units
    def plan(self, graph: Graph) -> list[_Unit]:
        """Collapse the graph into schedulable units and wire their dependencies."""
        owner: dict[str, str] = {}
        units: dict[str, _Unit] = {}
        for region in graph.regions:
            if not region.body:
                continue
            unit = _Unit(id=f"u_{region.id}", nodes=list(region.body), region=region)
            units[unit.id] = unit
            for nid in region.body:
                owner[nid] = unit.id
        for node in graph.nodes:
            if node.id in owner:
                continue
            unit = _Unit(id=f"u_{node.id}", nodes=[node.id])
            units[unit.id] = unit
            owner[node.id] = unit.id

        for unit in units.values():
            for nid in unit.nodes:
                for pred in graph.predecessors(nid):
                    pred_unit = owner.get(pred)
                    if pred_unit and pred_unit != unit.id:
                        unit.deps.add(pred_unit)
            if unit.region is not None and unit.region.over is not None:
                over = unit.region.over
                if hasattr(over, "node"):
                    pred_unit = owner.get(over.node)
                    if pred_unit and pred_unit != unit.id:
                        unit.deps.add(pred_unit)
            if unit.region is not None and unit.region.kind == "branch" and unit.region.condition is not None:
                # Without this, an arm with no data edge to its condition could read it unset
                # and be skipped as "not taken".
                pred_unit = owner.get(unit.region.condition.node)
                if pred_unit and pred_unit != unit.id:
                    unit.deps.add(pred_unit)

        order = {nid: i for i, nid in enumerate(graph.node_ids)}
        return sorted(units.values(), key=lambda u: min(order[n] for n in u.nodes))

    # -------------------------------------------------------------- running
    async def run(
        self,
        graph: Graph,
        *,
        task_input: dict | None = None,
        task_id: str | None = None,
        split: str | None = None,
        execution_id: str | None = None,
    ) -> Execution:
        graph.validate_graph()
        limit = self.concurrency_override or int(graph.meta.get("max_concurrency", 1))
        limit = max(1, limit)
        execution = Execution(
            execution_id=execution_id or f"x_{fingerprint((graph.id, task_id, time.time()))[:12]}",
            graph_id=graph.id,
            task_id=task_id,
            split=split,
            cold=not (self.cache and self.cache.enabled),
            optimized=bool(graph.derived_from),
            mode=self.mode.value,
            concurrency_limit=limit,
        )

        units = self.plan(graph)
        values: Values = bind_inputs(graph, task_input)
        self.executor.workflow = graph.name

        t0 = time.perf_counter()
        cpu0 = time.process_time()
        self.on_event(
            {
                "type": "start",
                "execution_id": execution.execution_id,
                "graph_id": graph.id,
                "n_nodes": len(graph.nodes),
                "concurrency_limit": limit,
                "mode": self.mode.value,
            }
        )
        # Nodes a pass removed, announced once so the dashboard can grey them out on the
        # original layout while the optimized graph runs (docs/API_FOR_UI.md section 6).
        for node_id, reason in (graph.meta.get("elided") or {}).items():
            self.on_event({"type": "node_skip", "node": node_id, "reason": reason})

        semaphore = asyncio.Semaphore(limit)
        done: set[str] = set()
        pending: dict[asyncio.Task, _Unit] = {}
        remaining = {u.id: u for u in units}

        try:
            while remaining or pending:
                in_flight = {u.id for u in pending.values()}
                ready = [u for u in remaining.values() if u.deps <= done and u.id not in in_flight]
                for unit in ready:
                    remaining.pop(unit.id, None)
                    task = asyncio.create_task(
                        self._run_unit(graph, unit, values, execution, semaphore, t0),
                        name=unit.id,
                    )
                    pending[task] = unit
                self._start_speculation(graph, units, done, values, semaphore, t0, limit - len(pending), execution)
                if not pending:
                    if remaining:
                        stuck = ", ".join(sorted(remaining))
                        raise SchedulerError(
                            f"graph {graph.id!r} cannot make progress: {stuck} wait on work that never becomes "
                            f"ready. This is a graph bug, not a slow workflow."
                        )
                    break
                finished, _ = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
                for task in finished:
                    pending.pop(task)
                    unit = task.result()  # re-raises a scheduler-level failure
                    done.add(unit.id)
                    if execution.status == "crashed":
                        for other in list(pending):
                            other.cancel()
                        pending.clear()
                        remaining.clear()
                        break
        finally:
            for task in pending:
                task.cancel()
            for node_id in list(self._speculative):
                self._discard(graph.node(node_id), execution, t0)

        execution.wall_ms = (time.perf_counter() - t0) * 1000.0
        execution.meta["resource"] = _resource_use(cpu0)
        execution.provider_calls = sum(nr.calls_made for nr in execution.node_runs)
        execution.cache_hits = sum(1 for nr in execution.node_runs if nr.cached)
        execution.apply_totals()
        if execution.status != "crashed":
            execution.status = "done"
            execution.outputs = output_values(graph, values)
        self._assert_side_effects_ordered(graph, execution)
        self.on_event(
            {
                "type": "end",
                "execution_id": execution.execution_id,
                "status": execution.status,
                "wall_ms": round(execution.wall_ms),
                "tokens": _sum_or_none(execution.tokens_in, execution.tokens_out),
                "cost_usd": execution.cost_usd,
                "cache_hits": execution.cache_hits,
                "error": execution.error,
            }
        )
        return execution

    # ------------------------------------------------------------ one unit
    async def _run_unit(
        self,
        graph: Graph,
        unit: _Unit,
        values: Values,
        execution: Execution,
        semaphore: asyncio.Semaphore,
        t0: float,
    ) -> _Unit:
        region = unit.region
        if unit.is_map:
            await self._run_map(graph, unit, values, execution, semaphore, t0)
        elif region is not None and region.kind == "loop":
            await self._run_loop(graph, unit, values, execution, semaphore, t0)
        elif region is not None and region.kind == "branch":
            await self._run_branch(graph, unit, values, execution, semaphore, t0)
        else:
            for node_id in unit.nodes:
                await self._run_one(graph.node(node_id), graph, values, execution, semaphore, t0, overlay=None)
        return unit

    async def _run_loop(
        self,
        graph: Graph,
        unit: _Unit,
        values: Values,
        execution: Execution,
        semaphore: asyncio.Semaphore,
        t0: float,
    ) -> None:
        """Iterate a loop region while its condition holds.

        `loop_cutoff` is honoured here rather than in the pass: when the pass has marked
        this region, the scheduler hashes the region's outputs after each iteration and
        stops as soon as two successive iterations agree. A loop that cannot change its
        own state cannot change the result, so further iterations are pure cost
        (docs/PASSES.md section 6).
        """
        region = unit.region
        assert region is not None
        body = [nid for nid in graph.topo_order() if nid in set(region.body)]
        cutoff = region.id in (graph.meta.get("loop_cutoff") or [])
        cap = region.max_iterations if region.max_iterations is not None else 16
        previous_state: str | None = None
        iterations = 0

        while iterations < cap:
            iterations += 1
            for node_id in body:
                await self._run_one(graph.node(node_id), graph, values, execution, semaphore, t0, overlay=None)
                if execution.status == "crashed":
                    return
            state = fingerprint([[values.get((nid, out)) for out in graph.node(nid).outputs] for nid in body])
            if cutoff and previous_state is not None and state == previous_state:
                self.on_event(
                    {
                        "type": "loop_cutoff",
                        "region": region.id,
                        "iterations": iterations,
                        "reason": "two successive iterations produced the same state hash, so the loop has "
                        "reached a fixpoint and cannot change the result",
                    }
                )
                break
            previous_state = state
            if region.condition is None:
                break
            keep_going = values.get((region.condition.node, region.condition.output))
            if not keep_going:
                break
        self.on_event({"type": "loop", "region": region.id, "iterations": iterations, "cap": cap})

    async def _run_branch(
        self,
        graph: Graph,
        unit: _Unit,
        values: Values,
        execution: Execution,
        semaphore: asyncio.Semaphore,
        t0: float,
    ) -> None:
        """Both arms are in the graph; exactly one executes."""
        region = unit.region
        assert region is not None
        taken = True
        if region.condition is not None:
            taken = bool(values.get((region.condition.node, region.condition.output)))
        if not taken:
            for node_id in region.body:
                for out in graph.node(node_id).outputs:
                    values[(node_id, out)] = None
                if node_id in self._speculative:
                    self._discard(graph.node(node_id), execution, t0)
            self.on_event({"type": "branch", "region": region.id, "taken": False, "nodes": list(region.body)})
            return
        self.on_event({"type": "branch", "region": region.id, "taken": True, "nodes": list(region.body)})
        for node_id in [nid for nid in graph.topo_order() if nid in set(region.body)]:
            if node_id in self._speculative:
                await self._adopt(graph.node(node_id), values, execution)
            else:
                await self._run_one(graph.node(node_id), graph, values, execution, semaphore, t0, overlay=None)
            if execution.status == "crashed":
                return

    # ---------------------------------------------------------- speculation
    def _start_speculation(
        self,
        graph: Graph,
        units: list[_Unit],
        done: set[str],
        values: Values,
        semaphore: asyncio.Semaphore,
        t0: float,
        free: int,
        execution: Execution,
    ) -> None:
        """Start marked arms whose branch is undecided, only with a free slot, so a guess never
        delays work the run is sure to need."""
        finished = {nid for u in units if u.id in done for nid in u.nodes}
        for unit in units:
            region = unit.region
            if region is None or region.kind != "branch" or unit.id in done:
                continue
            if region.condition is None or region.condition.node in finished:
                continue  # already decided, so this is no longer a guess
            for node_id in region.body:
                if free <= 0:
                    return
                node = graph.node(node_id)
                if not node.meta.get("speculate") or node_id in self._speculative:
                    continue
                # The pass already checked this. Effects are law, so the runtime checks again.
                if effects.may_speculate(node) is not None:
                    continue
                if not set(graph.predecessors(node_id)) <= finished:
                    continue
                try:
                    resolved = resolve_args(node, values)
                except KeyError:
                    continue
                self._speculative[node_id] = asyncio.create_task(
                    self._speculate_one(node, graph, resolved, semaphore, t0), name=f"speculate_{node_id}"
                )
                free -= 1
                self._speculation_count(execution, "started")
                self.on_event({"type": "speculate", "node": node_id, "region": region.id})

    async def _speculate_one(
        self, node: Node, graph: Graph, resolved: dict, semaphore: asyncio.Semaphore, t0: float
    ) -> tuple[NodeRun, dict]:
        async with semaphore, self._provider_semaphore(provider_of(node), graph):
            self._speculation_began.add(node.id)
            return await self.executor.run_node(node, resolved, instance=node.id, t0=t0, worker=self._next_worker())

    async def _adopt(self, node: Node, values: Values, execution: Execution) -> None:
        """The branch was taken: the guess is the real result, so it is recorded like any node."""
        run, outputs = await self._speculative.pop(node.id)
        run.speculative = True
        execution.node_runs.append(run)
        self._speculation_count(execution, "used")
        if run.error:
            execution.status = "crashed"
            execution.error = f"{node.id}: {run.error}"
            return
        for name, value in outputs.items():
            values[(node.id, name)] = value

    def _discard(self, node: Node, execution: Execution, t0: float) -> None:
        """The branch was not taken. A finished call keeps its measured spend. A cancelled model
        call may still be billed and reports nothing, so its tokens and cost stay `None`, which
        makes the execution's totals unknown rather than an undercount."""
        task = self._speculative.pop(node.id)
        if task.done() and not task.cancelled() and task.exception() is None:
            run = task.result()[0]
            self._speculation_count(execution, "discarded")
        elif task.done() and not task.cancelled():
            # It raised, so nothing was thrown away. It failed, and nothing depended on it.
            self._speculation_count(execution, "failed")
            return
        elif node.id not in self._speculation_began:
            # Never reached a provider, so it spent nothing and its totals stay known.
            task.cancel()
            self._speculation_count(execution, "never_started")
            return
        else:
            task.cancel()
            run = NodeRun(
                node_id=node.id, instance=node.id, target=node.target, kind=node.kind, effect=str(node.effect),
                started_ms=(time.perf_counter() - t0) * 1000.0, skip_reason="speculation cancelled",
            )
            self._speculation_count(execution, "cancelled")
        run.speculative = True
        run.discarded = True
        execution.node_runs.append(run)
        self.on_event({"type": "speculate_discard", "node": node.id})

    @staticmethod
    def _speculation_count(execution: Execution, key: str) -> None:
        counts = execution.meta.setdefault("speculation", {"started": 0, "used": 0, "discarded": 0, "cancelled": 0})
        counts[key] = counts.get(key, 0) + 1

    async def _run_map(
        self,
        graph: Graph,
        unit: _Unit,
        values: Values,
        execution: Execution,
        semaphore: asyncio.Semaphore,
        t0: float,
    ) -> None:
        """Fan a map region out over its collection, one chain per element."""
        region = unit.region
        assert region is not None
        collection = self._resolve_collection(region, values)
        body = [nid for nid in graph.topo_order() if nid in set(region.body)]

        if not collection:
            for node_id in body:
                for out in graph.node(node_id).outputs:
                    values[(node_id, out)] = []
            self.on_event({"type": "map", "region": region.id, "width": 0, "nodes": body})
            return

        width = len(collection)
        if width > self.max_map_width:
            raise SchedulerError(
                f"map region {region.id!r} would fan out {width} ways, over the {self.max_map_width} limit. "
                f"Raise max_map_width deliberately rather than letting a workflow spend a quota by accident."
            )
        self.on_event({"type": "map", "region": region.id, "width": width, "nodes": body})

        per_element: list[Values] = [{} for _ in range(width)]

        async def chain(index: int) -> None:
            overlay = per_element[index]
            if region.over is not None and hasattr(region.over, "node"):
                overlay[(region.over.node, region.over.output)] = collection[index]
            for node_id in body:
                await self._run_one(
                    graph.node(node_id), graph, values, execution, semaphore, t0,
                    overlay=overlay, instance_suffix=f"#{index}",
                )
                if execution.status == "crashed":
                    return

        await asyncio.gather(*(chain(i) for i in range(width)))

        # Publish the gathered results in element order, so a gather node downstream
        # sees a list and not whichever chain happened to finish last.
        for node_id in body:
            for out in graph.node(node_id).outputs:
                values[(node_id, out)] = [el.get((node_id, out)) for el in per_element]

    def _resolve_collection(self, region: Region, values: Values) -> list[Any]:
        over = region.over
        if over is None:
            raise SchedulerError(f"map region {region.id!r} does not say what it maps over")
        if hasattr(over, "node"):
            if (over.node, over.output) not in values:
                raise SchedulerError(
                    f"map region {region.id!r} maps over {over.node}.{over.output}, which has no value yet"
                )
            collection = values[(over.node, over.output)]
        else:
            collection = over.value
        if collection is None:
            return []
        if isinstance(collection, (str, bytes)):
            raise SchedulerError(
                f"map region {region.id!r} maps over a string. Mapping over characters is almost never what a "
                f"workflow meant; return a list from the producing node."
            )
        if not isinstance(collection, (list, tuple)):
            return [collection]
        return list(collection)

    # ------------------------------------------------------------- one node
    async def _run_one(
        self,
        node: Node,
        graph: Graph,
        values: Values,
        execution: Execution,
        semaphore: asyncio.Semaphore,
        t0: float,
        *,
        overlay: Values | None,
        instance_suffix: str = "",
    ) -> None:
        if node.target == "workflow_input":
            return  # its outputs were bound before the run started
        visible: Values = values if overlay is None else {**values, **overlay}
        try:
            resolved = resolve_args(node, visible)
        except KeyError as exc:
            execution.status = "crashed"
            execution.error = (
                f"node {node.id!r} expected a value for {exc}, which no completed node produced. "
                f"This is a scheduling bug in Synapse, not a workflow failure."
            )
            return

        provider = provider_of(node)
        instance = f"{node.id}{instance_suffix}"
        side_effecting = effects.is_side_effecting(node)

        async with semaphore, self._provider_semaphore(provider, graph):
            if side_effecting:
                # Independent re-check, after the dependency graph has already ordered
                # these: two observable effects must never overlap, whatever a pass did
                # to the graph.
                if self._side_effects_in_flight:
                    execution.status = "crashed"
                    execution.error = (
                        f"two side-effecting nodes overlapped ({instance} while another was in flight). "
                        f"The scheduler refuses this regardless of what a pass claimed."
                    )
                    return
                self._side_effects_in_flight += 1
                self._side_effect_log.append(node.id)
            worker = self._next_worker()
            try:
                run, outputs = await self.executor.run_node(
                    node, resolved, instance=instance, t0=t0, worker=worker
                )
            finally:
                if side_effecting:
                    self._side_effects_in_flight -= 1

        execution.node_runs.append(run)
        if run.error:
            execution.status = "crashed"
            execution.error = f"{instance}: {run.error}"
            return
        target = overlay if overlay is not None else values
        for name, value in outputs.items():
            target[(node.id, name)] = value

    # -------------------------------------------------------------- helpers
    def _provider_semaphore(self, provider: str, graph: Graph) -> asyncio.Semaphore:
        if provider not in self._provider_locks:
            self._provider_locks[provider] = asyncio.Semaphore(self._provider_limit(provider, graph))
        return self._provider_locks[provider]

    def _provider_limit(self, provider: str, graph: Graph) -> int:
        override = (graph.meta.get("per_provider") or {}).get(provider)
        if override:
            return int(override)
        if provider in ("local", "unknown"):
            return max(1, self.concurrency_override or int(graph.meta.get("max_concurrency", 1)))
        try:
            from synapse0g.graph.runtime.providers import load_registry

            return load_registry().provider_limit(provider)
        except Exception:  # noqa: BLE001 - no registry on this machine: fall back to the global limit
            return max(1, self.concurrency_override or int(graph.meta.get("max_concurrency", 1)))

    def _next_worker(self) -> int:
        self._worker_seq += 1
        return self._worker_seq

    def _assert_side_effects_ordered(self, graph: Graph, execution: Execution) -> None:
        """Last line of defence, checked after the fact and reported, never swallowed.

        If this ever fires, an optimization changed observable behaviour and the result
        must not be reported as a win.
        """
        expected = list(effects.side_effect_order(graph))
        observed = [nid for nid in self._side_effect_log if nid in set(expected)]
        if observed != [nid for nid in expected if nid in set(observed)]:
            execution.status = "crashed"
            execution.error = (
                f"side effects executed in the order {observed}, but the graph's program order is {expected}. "
                f"Refusing to report this execution as a result."
            )


def _resource_use(cpu0: float) -> dict:
    """CPU and memory of this Python process. A local model server is a separate process and is
    not counted, and overlapping runs in one process share these numbers."""
    try:
        import psutil

        rss_mb = round(psutil.Process().memory_info().rss / 1e6, 1)
    except ImportError:
        rss_mb = None
    return {"cpu_ms": round((time.process_time() - cpu0) * 1000.0, 1), "rss_mb": rss_mb, "scope": "synapse process"}


def _sum_or_none(a: int | None, b: int | None) -> int | None:
    if a is None and b is None:
        return None
    return (a or 0) + (b or 0)


async def run_graph(
    graph: Graph,
    *,
    task_input: dict | None = None,
    task_id: str | None = None,
    split: str | None = None,
    cache: Cache | None = None,
    mode: ExecutionMode = ExecutionMode.LIVE,
    concurrency: int | None = None,
    allow_irreversible: bool = False,
    implementations: dict[str, Callable[..., Any]] | None = None,
    on_event: Callable[[dict], None] | None = None,
) -> Execution:
    """Run one graph once. The convenience entry point the CLI and API both use."""
    scheduler = Scheduler(
        cache=cache,
        mode=mode,
        concurrency=concurrency,
        allow_irreversible=allow_irreversible,
        implementations=implementations,
        on_event=on_event,
    )
    return await scheduler.run(graph, task_input=task_input, task_id=task_id, split=split)


def run_graph_sync(graph: Graph, **kw) -> Execution:
    """For the CLI and tests. Never called from inside a running loop."""
    return asyncio.run(run_graph(graph, **kw))


__all__ = [
    "Execution",
    "ExecutionMode",
    "NodeRun",
    "Scheduler",
    "SchedulerError",
    "SIRError",
    "run_graph",
    "run_graph_sync",
]
