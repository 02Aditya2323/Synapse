"""Node execution and measurement.

This module owns node execution, measurement, and cache-hit bookkeeping.

Three rules live in this file, and they are not negotiable from the outside:

**Measured, never estimated** (docs/CORRECTNESS.md). Latency is wall-clock around the
call. Tokens come from the provider's `usage`. Cost is tokens x the price in
models.yaml. If the provider did not report a number, the field is `None` -- never a
zero, never an average standing in for a measurement.

**`irreversible` nodes are never executed** during optimization search, equivalence
runs or benchmarks. The executor refuses regardless of what the caller asked for; only
`ExecutionMode.LIVE` may run one, and even then only when the caller has explicitly
allowed it.

**A crash is a crash, not a slow workflow** (rule 10). A failed node records its error
and marks the execution `crashed`. A runtime bug must never arrive in a benchmark as a
performance number.
"""

from __future__ import annotations

import asyncio
import contextvars
import copy
import inspect
import time
from collections.abc import Callable
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from synapse0g.graph.core import effects
from synapse0g.graph.core.hashing import fingerprint, node_hash
from synapse0g.graph.core.sir import Effect, Graph, Literal_, Node, Ref, utc_now
from synapse0g.graph.frontend import dsl
from synapse0g.graph.runtime import meter
from synapse0g.graph.runtime.cache import Cache, CacheHit


class ExecutionMode(StrEnum):
    """What this execution is for. It decides what may touch the world.

    LIVE    a real run the user asked for: every tier may execute.
    SEARCH  the optimizer trying pipelines: `external` stubbed, `irreversible` refused.
    VERIFY  an equivalence run: same, and the stubs are recorded so both sides match.
    BENCH   a benchmark: same as VERIFY, and crashes are counted separately.
    """

    LIVE = "live"
    SEARCH = "search"
    VERIFY = "verify"
    BENCH = "bench"

    @property
    def stubs_external(self) -> bool:
        return self is not ExecutionMode.LIVE


class NodeRun(BaseModel):
    """One node instance, measured. The unit `profile/collect.py` aggregates."""

    model_config = ConfigDict(protected_namespaces=())

    node_id: str
    instance: str  # "n1" or "n1#3" for the fourth element of a map region
    target: str
    kind: str
    effect: str
    node_hash: str | None = None
    started_ms: float = 0.0  # from execution start, so a late-joining UI can draw the timeline
    latency_ms: float = 0.0
    cached: bool = False
    cache_age_s: float | None = None
    stubbed: bool = False
    skipped: bool = False
    skip_reason: str | None = None
    tokens_in: int | None = None
    tokens_out: int | None = None
    cost_usd: float | None = None
    model_id: str | None = None
    provider: str | None = None
    output_digest: str | None = None
    error: str | None = None
    worker: int | None = None
    speculative: bool = False  # started before its branch was resolved
    discarded: bool = False  # speculative and not needed: the spend is real, the output was thrown away
    # Provider calls a non-model node made itself, e.g. a CrewAI task's agent loop. None when it
    # made none. A model node is always one call and leaves this None.
    provider_calls: int | None = None

    @property
    def ok(self) -> bool:
        return self.error is None

    @property
    def calls_made(self) -> int:
        """Provider calls this run really made: 0 for a cache hit, a stub or a skip."""
        if self.cached or self.stubbed or self.skipped:
            return 0
        return 1 if self.kind == "model" else (self.provider_calls or 0)


class Execution(BaseModel):
    """One run of one graph on one task. What the store persists and the API serves."""

    model_config = ConfigDict(protected_namespaces=())

    execution_id: str
    graph_id: str
    task_id: str | None = None
    split: str | None = None
    status: str = "running"  # running | done | crashed
    cold: bool = True
    optimized: bool = False
    mode: str = ExecutionMode.LIVE.value
    wall_ms: float = 0.0
    tokens_in: int | None = None
    tokens_out: int | None = None
    cost_usd: float | None = None
    provider_calls: int = 0
    cache_hits: int = 0
    concurrency_limit: int = 1
    success: bool | None = None
    outputs: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    node_runs: list[NodeRun] = Field(default_factory=list)
    created_at: str = Field(default_factory=utc_now)
    meta: dict = Field(default_factory=dict)

    # -- aggregates ---------------------------------------------------------
    @property
    def crashed(self) -> bool:
        return self.status == "crashed"

    @property
    def cache_hit_rate(self) -> float | None:
        ran = [nr for nr in self.node_runs if not nr.skipped]
        return (sum(1 for nr in ran if nr.cached) / len(ran)) if ran else None

    def totals(self) -> dict:
        """What this execution actually spent.

        A cache hit keeps the original call's counts on its `NodeRun`, as a record of what
        it saved, but they were not spent this time and stay out of the totals. A real
        model call that reported nothing makes the total `None`, never an undercount.
        """

        def spent(field: str) -> int | float | None:
            real = [getattr(nr, field) for nr in self.node_runs if nr.calls_made]
            if real:
                return None if any(v is None for v in real) else sum(real)
            hits = [getattr(nr, field) for nr in self.node_runs if nr.cached]
            return 0 if any(v is not None for v in hits) else None

        return {
            "wall_ms": self.wall_ms,
            "tokens_in": spent("tokens_in"),
            "tokens_out": spent("tokens_out"),
            "cost_usd": spent("cost_usd"),
            "provider_calls": self.provider_calls,
            "cache_hits": self.cache_hits,
        }

    def apply_totals(self) -> Execution:
        t = self.totals()
        self.tokens_in = t["tokens_in"]
        self.tokens_out = t["tokens_out"]
        self.cost_usd = t["cost_usd"]
        return self


#: True only while an irreversible step runs because the caller allowed it or a person
#: approved it. A tool can check it as a last tripwire (`irreversible_permitted()`).
_IRREVERSIBLE_PERMITTED: contextvars.ContextVar[bool] = contextvars.ContextVar("irreversible_permitted",
                                                                                default=False)


def irreversible_permitted() -> bool:
    return _IRREVERSIBLE_PERMITTED.get()


class NodeRefused(RuntimeError):
    """The executor declined to run a node. Carries the finding so the UI can explain
    which precondition said no."""

    def __init__(self, finding: effects.Finding):
        super().__init__(finding.message)
        self.finding = finding


# ----------------------------------------------------------------- the executor


class Executor:
    """Runs one node instance and measures it. The scheduler decides *when*; this
    decides *what happens*, and refuses what must not happen."""

    def __init__(
        self,
        *,
        cache: Cache | None = None,
        mode: ExecutionMode = ExecutionMode.LIVE,
        allow_irreversible: bool = False,
        implementations: dict[str, Callable[..., Any]] | None = None,
        on_event: Callable[[dict], None] | None = None,
        approver: Callable[..., Any] | None = None,
    ):
        self.cache = cache
        self.mode = mode
        # Two locks, not one: `irreversible` needs both the mode to be LIVE *and* the
        # caller to have said so. A demo that forgets the flag places no orders.
        self.allow_irreversible = allow_irreversible and mode is ExecutionMode.LIVE
        # The one other way to say so: a person asked at the moment the step is reached, through
        # `approver`. Only ever consulted in a LIVE run.
        self.approver = approver if mode is ExecutionMode.LIVE else None
        self.workflow: str | None = None
        self.implementations = implementations or {}
        self.on_event = on_event or (lambda e: None)
        # Calls running now, by content hash, so a duplicate waits rather than repeating.
        self._in_flight: dict[str, asyncio.Future] = {}

    # -- implementation lookup ---------------------------------------------
    def implementation(self, node: Node) -> Callable[..., Any]:
        """The function that does this node's work.

        Looked up by declaration first (`meta.impl`), then by target. The two differ for
        a model node, whose target is the models.yaml id and whose declaration is the
        prompt builder -- several nodes share one model.
        """
        for key in (node.meta.get("impl"), node.target):
            if not key:
                continue
            if key in self.implementations:
                return self.implementations[key]
            spec = dsl.spec_for(key)
            if spec is not None:
                return spec.impl
        wanted = node.meta.get("impl") or node.target
        raise KeyError(
            f"node {node.id!r} needs the implementation {wanted!r}, which is not declared. Declare it with "
            f"@node, or pass it in `implementations=` (lifted graphs often need this)."
        )

    # -- one node -----------------------------------------------------------
    async def run_node(
        self,
        node: Node,
        resolved: dict[str, Any],
        *,
        instance: str | None = None,
        t0: float,
        worker: int | None = None,
    ) -> tuple[NodeRun, dict[str, Any]]:
        """Execute one node instance. Returns its measurement and its outputs by name.

        Never raises for a node-level failure: the error lands on the `NodeRun`, and the
        scheduler decides whether the execution as a whole is crashed. It *does* raise
        `NodeRefused` when a node must not run at all, because that is not a failure of
        the workflow -- it is Synapse declining, and the caller needs to know the
        difference.
        """
        run = NodeRun(
            node_id=node.id,
            instance=instance or node.id,
            target=node.target,
            kind=node.kind,
            effect=str(node.effect),
            started_ms=(time.perf_counter() - t0) * 1000.0,
            worker=worker,
        )

        # The hard refusal, checked before anything else and independent of the caller.
        # In a LIVE run a person may lift it for this one step; nobody can in any other mode.
        approved, why = False, None
        if effects.is_irreversible(node) and not self.allow_irreversible and self.approver is not None:
            self.on_event({"type": "approval_wait", "node": run.instance, "target": node.target})
            approved, why = await self.approver(node, resolved, workflow=self.workflow)
            run.started_ms = (time.perf_counter() - t0) * 1000.0  # the wait is a person's, not the step's
        if effects.is_irreversible(node):
            _IRREVERSIBLE_PERMITTED.set(self.allow_irreversible or approved)
        if effects.is_irreversible(node) and not self.allow_irreversible and not approved:
            finding = effects.may_execute_during_search(node)
            assert finding is not None
            run.stubbed = True
            run.node_hash = _stub_hash(node, resolved)
            run.skip_reason = why or finding.message
            run.latency_ms = 0.0
            value = _stub_value(node)
            run.output_digest = fingerprint(value)
            self.on_event(
                {
                    "type": "node_stub",
                    "node": run.instance,
                    "t_ms": round(run.started_ms),
                    "effect": str(node.effect),
                    "reason": run.skip_reason,
                }
            )
            return run, _stub_outputs(node, value)

        if effects.is_side_effecting(node) and self.mode.stubs_external:
            run.stubbed = True
            run.node_hash = _stub_hash(node, resolved)
            run.skip_reason = (
                f"{node.target} is {node.effect}; it is stubbed during a {self.mode.value} run so the "
                f"effect happens once, in a live run, and never once per candidate pipeline."
            )
            value = _stub_value(node)
            run.output_digest = fingerprint(value)
            self.on_event(
                {"type": "node_stub", "node": run.instance, "t_ms": round(run.started_ms),
                 "effect": str(node.effect), "reason": run.skip_reason}
            )
            return run, _stub_outputs(node, value)

        # Content address. Computed for every node, whether or not it is cacheable,
        # because `cse` and the UI both want to see when two nodes agree.
        try:
            run.node_hash = node_hash(node, resolved)
        except KeyError as exc:  # a cache-key field with no value: a real bug, say so
            run.error = f"{type(exc).__name__}: {exc}"
            return run, {}

        flight: asyncio.Future | None = None

        # Cache. Only when the memoize pass marked this node, or the caller asked for
        # the cache-only baseline -- otherwise a "cold" number would quietly be warm.
        if self.cache is not None and _cache_allowed(node, self.cache):
            if hasattr(self.cache, "note_inputs"):
                self.cache.note_inputs(node, run.node_hash, resolved)
            got = self.cache.get(node, run.node_hash)
            if isinstance(got, CacheHit):
                run.cached = True
                run.cache_age_s = got.age_s
                run.latency_ms = (time.perf_counter() - t0) * 1000.0 - run.started_ms
                run.tokens_in = got.tokens_in
                run.tokens_out = got.tokens_out
                run.cost_usd = got.cost_usd
                run.model_id = got.model_id
                run.output_digest = fingerprint(got.value)
                self.on_event(
                    {"type": "cache_hit", "node": run.instance, "t_ms": round(run.started_ms),
                     "age_s": round(got.age_s, 1), "saved_ms": round(got.latency_ms)}
                )
                return run, _as_outputs(node, got.value)

        # A map's duplicate elements start together, so they all miss the cache. Only the
        # first calls; the rest wait for it. Nothing awaits between the lookup above and this
        # check, so two nodes cannot both become the first. A waiter holds its scheduler slot,
        # but the call it waits on already has one, so this cannot deadlock.
        if (
            self.cache is not None
            and _cache_allowed(node, self.cache)
            and self.cache.enabled
            and effects.may_reuse(node, transform="cache") is None
        ):
            running = self._in_flight.get(run.node_hash)
            if running is not None:
                shared = await running
                if shared is not None:
                    return self._served_by_running_call(node, run, shared, t0)
            else:
                flight = asyncio.get_running_loop().create_future()
                self._in_flight[run.node_hash] = flight

        self.on_event(
            {"type": "node_start", "node": run.instance, "t_ms": round(run.started_ms), "worker": worker,
             "kind": node.kind, "effect": str(node.effect)}
        )

        started = time.perf_counter()
        try:
            return await self._call_and_record(node, resolved, run, started, flight)
        finally:
            # Here so a cancelled or failed call still releases its waiters. They receive
            # None and run the node themselves.
            if flight is not None:
                self._in_flight.pop(run.node_hash, None)
                if not flight.done():
                    flight.set_result(None)

    async def _call_and_record(
        self, node: Node, resolved: dict[str, Any], run: NodeRun, started: float, flight: asyncio.Future | None
    ) -> tuple[NodeRun, dict[str, Any]]:
        try:
            if node.kind == "model":
                value, meta = await self._run_model(node, resolved)
                run.tokens_in = meta.get("tokens_in")
                run.tokens_out = meta.get("tokens_out")
                run.cost_usd = meta.get("cost_usd")
                run.model_id = meta.get("model_id")
                run.provider = meta.get("provider")
            else:
                # A plain node may call models itself (a CrewAI task is a whole agent loop).
                # Those calls are measured by `providers.chat` into this meter. A node that
                # made none keeps `None`, because it spent no tokens to report.
                with meter.measuring() as used:
                    value = await self._run_plain(node, resolved)
                if used.calls:
                    run.tokens_in = used.tokens_in
                    run.tokens_out = used.tokens_out
                    run.cost_usd = used.cost_usd
                    run.model_id = used.model_id
                    run.provider = used.provider
                    run.provider_calls = used.calls
        except Exception as exc:  # noqa: BLE001 - recorded, re-raised by the scheduler as a crash
            run.latency_ms = (time.perf_counter() - started) * 1000.0
            run.error = f"{type(exc).__name__}: {exc}"
            self.on_event(
                {"type": "error", "node": run.instance, "t_ms": round(run.started_ms), "message": run.error}
            )
            return run, {}

        run.latency_ms = (time.perf_counter() - started) * 1000.0
        run.output_digest = fingerprint(value)
        if flight is not None:
            flight.set_result(
                {
                    "value": value,
                    "tokens_in": run.tokens_in,
                    "tokens_out": run.tokens_out,
                    "cost_usd": run.cost_usd,
                    "model_id": run.model_id,
                    "latency_ms": run.latency_ms,
                }
            )
        if self.cache is not None and run.node_hash:
            self.cache.put(
                node,
                run.node_hash,
                value,
                latency_ms=run.latency_ms,
                tokens_in=run.tokens_in,
                tokens_out=run.tokens_out,
                cost_usd=run.cost_usd,
                model_id=run.model_id,
            )
        self.on_event(
            {"type": "node_end", "node": run.instance, "t_ms": round(run.started_ms + run.latency_ms),
             "latency_ms": round(run.latency_ms), "cached": False,
             "tokens_in": run.tokens_in, "tokens_out": run.tokens_out}
        )
        return run, _as_outputs(node, value)

    def _served_by_running_call(
        self, node: Node, run: NodeRun, shared: dict, t0: float
    ) -> tuple[NodeRun, dict[str, Any]]:
        """Record a duplicate that waited for an identical call: a cache hit, aged zero."""
        value = copy.deepcopy(shared["value"])
        run.cached = True
        run.cache_age_s = 0.0
        run.latency_ms = (time.perf_counter() - t0) * 1000.0 - run.started_ms
        run.tokens_in = shared["tokens_in"]
        run.tokens_out = shared["tokens_out"]
        run.cost_usd = shared["cost_usd"]
        run.model_id = shared["model_id"]
        run.output_digest = fingerprint(value)
        self.on_event(
            {
                "type": "cache_hit",
                "node": run.instance,
                "t_ms": round(run.started_ms),
                "age_s": 0.0,
                "saved_ms": round(shared["latency_ms"]),
            }
        )
        return run, _as_outputs(node, value)

    # -- kinds --------------------------------------------------------------
    async def _run_model(self, node: Node, resolved: dict[str, Any]) -> tuple[Any, dict]:
        """The implementation builds the prompt; this makes the call and measures it.

        Keeping the provider call here rather than in the workflow is what makes
        "measured, never estimated" enforceable: there is exactly one place where
        tokens are read off a response, and no workflow author can forget to report
        them.
        """
        from synapse0g.graph.runtime import providers

        impl = self.implementation(node)
        if getattr(impl, "__synapse_replay__", False):
            # A replayed node from a lifted trace: the recording already holds the
            # answer, so there is nothing to ask a provider. Tokens and cost stay
            # `None` here on purpose -- the recording's numbers belong to the original
            # execution, and reporting them as this run's measurements would be the
            # kind of double-counting that makes a benchmark fiction.
            return await _maybe_await(impl(**resolved)), {"model_id": node.target, "provider": "replay"}

        prompt = await _maybe_await(impl(**resolved))
        messages, tools, params = _as_request(prompt)
        if node.meta.get("trim_chars"):
            from synapse0g.graph.passes.context_trim import clip_messages

            messages = clip_messages(messages, int(node.meta["trim_chars"]))
        result = await asyncio.to_thread(providers.chat, node.target, messages, tools, **params)
        meta = {
            "tokens_in": result.tokens_in,
            "tokens_out": result.tokens_out,
            "cost_usd": result.cost_usd,
            "model_id": result.model_id,
            "provider": result.provider,
        }
        if len(node.outputs) == 1 and node.outputs[0] not in ("tool_calls", "finish_reason"):
            # One output, whatever its name, is the text. Filtering by name here once turned
            # `outputs=["answer"]` into an empty dict, silently.
            value: Any = result.text
        else:
            fields = {"value": result.text, "text": result.text, "tool_calls": result.tool_calls,
                      "finish_reason": result.finish_reason}
            value = {k: v for k, v in fields.items() if k in node.outputs}
            if len(node.outputs) == 1:
                value = value[node.outputs[0]]
        return value, meta

    async def _run_plain(self, node: Node, resolved: dict[str, Any]) -> Any:
        impl = self.implementation(node)
        if inspect.iscoroutinefunction(impl):
            return await impl(**resolved)
        # Off the event loop: a synchronous tool that blocks for 2 s must not stall the
        # other nodes the scheduler is running beside it.
        return await asyncio.to_thread(lambda: impl(**resolved))


# ---------------------------------------------------------------------- helpers


def _cache_allowed(node: Node, cache: Cache) -> bool:
    """Whether to consult the cache for this node.

    Only when `memoize` marked it, or the whole execution asked to cache everything
    (Radhesh's cache-only baseline). Consulting it by default would make every "cold"
    measurement quietly warm, which is the one number a judge is guaranteed to probe.
    """
    return bool(node.meta.get("memoize")) or bool(getattr(cache, "cache_everything", False))


def _stub_hash(node: Node, resolved: dict[str, Any]) -> str:
    """What a stubbed node would have done, so the firewall can compare side effects by
    their resolved arguments. A refusal never fails on this: a missing key is recorded."""
    try:
        return node_hash(node, resolved)
    except KeyError as exc:
        return f"unhashable: {exc}"


def _stub_value(node: Node) -> dict:
    """What a stubbed side effect returns.

    Deliberately self-describing and deterministic: both halves of an equivalence run
    see the same stub, so a difference between them is never the stub's fault.
    """
    return {
        "__stubbed__": True,
        "node": node.id,
        "target": node.target,
        "effect": str(node.effect),
        "note": "Synapse did not perform this side effect during a non-live run.",
    }


def _stub_outputs(node: Node, value: dict) -> dict[str, Any]:
    """Every output of a stubbed node is the stub: the effect did not happen, so none of its values exist.
    One stub per output, so a node with several outputs is stubbed as cleanly as a node with one."""
    return dict.fromkeys(node.outputs, value)


def _as_outputs(node: Node, value: Any) -> dict[str, Any]:
    """Map an implementation's return value onto the node's declared outputs."""
    if len(node.outputs) == 1:
        return {node.outputs[0]: value}
    if isinstance(value, dict):
        missing = [o for o in node.outputs if o not in value]
        if missing:
            raise KeyError(
                f"node {node.id!r} declares outputs {node.outputs} but its result is missing "
                f"{', '.join(missing)}"
            )
        return {o: value[o] for o in node.outputs}
    if isinstance(value, (list, tuple)) and len(value) == len(node.outputs):
        return dict(zip(node.outputs, value, strict=True))
    raise TypeError(
        f"node {node.id!r} declares {len(node.outputs)} outputs, so its implementation must return a dict "
        f"keyed by output name (or a sequence of that length), not {type(value).__name__}"
    )


def _as_request(prompt: Any) -> tuple[list[dict], list[dict] | None, dict]:
    """A model node's implementation may return a string, a messages list, or a dict."""
    if isinstance(prompt, str):
        return [{"role": "user", "content": prompt}], None, {}
    if isinstance(prompt, list):
        return prompt, None, {}
    if isinstance(prompt, dict):
        body = dict(prompt)
        messages = body.pop("messages", None)
        if messages is None:
            raise TypeError("a model node returning a dict must include 'messages'")
        tools = body.pop("tools", None)
        return messages, tools, body
    raise TypeError(
        f"a model node's implementation must return a prompt string, a messages list, or a dict with "
        f"'messages' -- not {type(prompt).__name__}"
    )


async def _maybe_await(value: Any) -> Any:
    return await value if inspect.isawaitable(value) else value


def resolve_args(node: Node, values: dict[tuple[str, str], Any]) -> dict[str, Any]:
    """Turn a node's `args` into concrete values, given what has been computed.

    `values` is keyed by (producer instance, output name). This is the only place a Ref
    becomes a value, which is why there are no hidden channels: a node sees exactly what
    its arguments name.
    """
    out: dict[str, Any] = {}
    for name, arg in node.args.items():
        if isinstance(arg, Literal_):
            out[name] = arg.value
        else:
            out[name] = values[(arg.node, arg.output)]
    return out


def bind_inputs(graph: Graph, task_input: dict | None) -> dict[tuple[str, str], Any]:
    """Seed the value table from the workflow's input node.

    Values come from the task; anything the task does not name falls back to the
    constants the workflow was compiled with (`meta.input_defaults`). A parameter with
    neither is an error, named explicitly -- a workflow silently running on `None` is a
    benchmark number nobody can trust.
    """
    if not graph.inputs:
        return {}
    defaults = graph.meta.get("input_defaults") or {}
    given = dict(task_input or {})
    values: dict[tuple[str, str], Any] = {}
    missing: list[str] = []
    for name in graph.inputs:
        if name in given:
            values[(dsl.INPUT_NODE_ID, name)] = given[name]
        elif name in defaults:
            values[(dsl.INPUT_NODE_ID, name)] = defaults[name]
        else:
            missing.append(name)
    if missing:
        raise KeyError(
            f"graph {graph.id!r} needs workflow inputs {', '.join(missing)}; the task supplied "
            f"{sorted(given) or 'nothing'}"
        )
    return values


def output_values(graph: Graph, values: dict[tuple[str, str], Any]) -> dict[str, Any]:
    """What the workflow returned, by declared output name."""
    out: dict[str, Any] = {}
    for name, ref in graph.outputs.items():
        out[name] = values.get((ref.node, ref.output))
    return out


def provider_of(node: Node) -> str:
    """Which provider's concurrency budget this node spends.

    Tools and retrievers are local work; only model calls hit a rate-limited provider,
    and the free tiers in this build are a real constraint rather than a theoretical
    one.
    """
    if node.kind != "model":
        return "local"
    try:
        from synapse0g.graph.runtime.providers import model_config

        return model_config(node.target).provider
    except Exception:  # noqa: BLE001 - an unknown target spends the shared budget
        return "unknown"


def ref_key(ref: Ref) -> tuple[str, str]:
    return (ref.node, ref.output)


def effect_of(node: Node) -> Effect:
    return node.effect
