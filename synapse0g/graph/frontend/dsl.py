"""The decorator DSL: write a workflow in the obvious sequential style, get SIR.

    from synapse0g.graph import workflow, node, Effect

    @node(kind="retrieval", effect=Effect.READ, staleness_s=3600)
    def search(query: str) -> list[dict]: ...

    @node(kind="model", target="agent-small")
    def summarize(docs: list[dict]) -> str: ...

    @workflow
    def research(question: str) -> str:
        subs  = plan(question)
        hits  = [search(s) for s in subs]      # a map region: independent by construction
        notes = [summarize(h) for h in hits]   # another one
        return synthesize(question, notes)

`research.compile()` -- and calling `research(...)` -- executes nothing. It traces the
call structure and returns a `Graph`. Dependencies fall out of the Python: a node's
arguments *are* its data edges, so there is nothing to infer and nothing to guess.
That is why `research_fanout` can be written sequentially and still be parallelized,
which is precisely the inefficiency the problem statement's backstory describes.

Two conventions worth knowing before you read the code:

**Workflow parameters become one explicit input node** (`target="workflow_input"`,
effect `pure`), whose outputs are the parameter names. docs/IR_SPEC.md section 8 shows
them as literals, which is right for a workflow compiled with constants; but the
benchmark runs one graph over forty tasks, and a graph whose task is baked in as a
literal is a different graph per task. An input node keeps "every dependency is
explicit" (section 1.1) while letting the runtime bind a task per execution. The
literal form is still produced for any argument the author passes a constant for.

**Iterating a handle makes a map region.** `for s in subs` where `subs` is another
node's output yields exactly one element handle, and every node created inside the
iteration joins a `map` region over `subs`. So a comprehension over *k* sub-queries is
one node in the IR, not *k* -- the IR stays the size of the source (section 1.4) and
the scheduler fans it out at execution time.

Region membership then follows the data: a node whose argument is a bare handle on a
node already inside a map region joins that same region, because consuming a
per-element value makes you per-element work. Passing the *comprehension's list* is the
gather, and a gather node stays outside. One consequence is worth flagging, because it
departs from the worked example in docs/IR_SPEC.md section 8: chained comprehensions
produce **one** map region holding `search` and `summarize`, not two. That is the
better IR -- it says each element's `search -> summarize` chain is independent, so no
gather-and-rescatter sits in the middle -- and the critical-path claim is unchanged:
`plan -> search -> summarize -> synthesize`, four node latencies rather than `1+k+k+1`.

**The node implementation contract**, which `runtime/executor.py` relies on:

- `kind="model"`: the function receives the resolved arguments and returns the *prompt*
  -- a string, a messages list, or `{"messages": [...], "tools": [...], **params}`. The
  runtime makes the provider call, so tokens and cost are measured in one place and no
  workflow author has to remember to report them. Declare outputs `text`, `tool_calls`
  or `finish_reason` to see the whole result.
- every other kind: the function returns the value. With several declared outputs it
  returns a dict keyed by output name.
"""

from __future__ import annotations

import contextlib
import contextvars
import functools
import inspect
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any

from synapse0g.graph.core.hashing import fingerprint
from synapse0g.graph.core.sir import (
    Arg,
    Determinism,
    Effect,
    Graph,
    Node,
    NodeKind,
    Ref,
    Region,
    as_arg,
)

INPUT_NODE_ID = "n_in"
INPUT_TARGET = "workflow_input"


# --------------------------------------------------------------------- registry


@dataclass(frozen=True)
class NodeSpec:
    """Everything the IR and the runtime need to know about one declared node type.

    `name` and `target` are different things, and conflating them was a real bug worth
    recording. `target` is the registry model id or tool name -- what models.yaml calls
    it. `name` is *this declaration*: three model nodes in one workflow legitimately
    share the model `agent-small` while building three different prompts, so the model
    id cannot identify the implementation.

    It also cannot identify the *work*: `cse` and the content hash must both include the
    declaration, or two different prompts to the same model with the same arguments
    would look identical and one would be served the other's answer.
    """

    name: str
    target: str
    kind: NodeKind
    effect: Effect
    determinism: Determinism
    outputs: tuple[str, ...]
    reads: tuple[str, ...]
    writes: tuple[str, ...]
    staleness_s: float | None
    cache_key_fields: tuple[str, ...] | None
    batch: bool
    impl: Callable[..., Any]
    origin: dict


_REGISTRY: dict[str, NodeSpec] = {}


def registry() -> dict[str, NodeSpec]:
    """Every declared node type, by declaration name. `runtime/executor.py` looks
    implementations up here; `frontend/lift.py` consults it to recover an effect for a
    recorded tool."""
    return dict(_REGISTRY)


def spec_for(key: str) -> NodeSpec | None:
    """By declaration name, falling back to target.

    A lifted trace knows only the tool name, so the fallback is what lets a recorded
    `get_price` step find the effect someone declared for it.
    """
    if key in _REGISTRY:
        return _REGISTRY[key]
    return next((s for s in _REGISTRY.values() if s.target == key), None)


def clear_registry() -> None:
    """Tests only. Declaring the same target twice in one process is otherwise an error,
    which is what you want: two different effects for one tool name is a real bug."""
    _REGISTRY.clear()


# ------------------------------------------------------------------- the tracer


class Handle:
    """A deferred value: one output of one node.

    The author never constructs these. They are what a traced call returns, and passing
    one into another call is what creates a data edge.
    """

    __slots__ = ("_ref", "_builder")

    def __init__(self, ref: Ref, builder: _Builder):
        self._ref = ref
        self._builder = builder

    @property
    def ref(self) -> Ref:
        return self._ref

    def __iter__(self) -> Iterator[Handle]:
        """Yield exactly one element handle, inside a fresh map region.

        One element, not *k*: the IR describes the shape of the work, and the scheduler
        decides how wide to fan it out from the actual collection at run time.
        """
        return self._builder.map_over(self)

    def __repr__(self) -> str:  # pragma: no cover - display only
        return f"<handle {self._ref.node}.{self._ref.output}>"

    # Arithmetic, truthiness and indexing are deliberately absent. A workflow that
    # branches on a traced value needs a `control` node so the branch is in the IR;
    # silently letting Python evaluate it would put a dependency outside the graph,
    # which is the one thing the IR promises never to allow.
    def __bool__(self) -> bool:
        raise TypeError(
            f"{self!r} is a deferred value, so Python cannot test it while tracing. "
            "Express the decision as a `control` node so the branch is visible in the IR."
        )

    def __getitem__(self, key: Any) -> Handle:
        raise TypeError(
            f"{self!r} cannot be indexed while tracing. Add a `transform` node that takes "
            "the whole value and returns the part you want, so the dependency is explicit."
        )


class _Builder:
    """Accumulates nodes and regions for one workflow trace."""

    def __init__(self, name: str, params: dict[str, Any], param_types: dict[str, str]):
        self.name = name
        self.nodes: list[Node] = []
        self.regions: list[Region] = []
        self.param_types = param_types
        self.param_defaults = params
        self._counter = 0
        self._region_stack: list[str] = []
        self.agent: str | None = None
        self._map_regions: dict[str, str] = {}  # handle key -> region id
        self.input_node: Node | None = None
        if param_types:
            self.input_node = Node(
                id=INPUT_NODE_ID,
                kind="transform",
                target=INPUT_TARGET,
                effect=Effect.PURE,
                determinism="deterministic",
                outputs=list(param_types),
                origin={"kind": "workflow_input"},
                meta={"types": dict(param_types)},
            )
            self.nodes.append(self.input_node)

    # -- ids ---------------------------------------------------------------
    def next_id(self, target: str) -> str:
        nid = f"n{self._counter}"
        self._counter += 1
        return nid

    def next_region_id(self, kind: str) -> str:
        return f"r_{kind}{len([r for r in self.regions if r.kind == kind]) + 1}"

    # -- map regions -------------------------------------------------------
    def map_over(self, handle: Handle) -> Iterator[Handle]:
        existing = self.region_of_node(handle.ref.node)
        if existing and existing.kind == "map":
            # Iterating a value that is already per-element: stay in its region rather
            # than nesting a second map over the same collection.
            self._region_stack.append(existing.id)
            try:
                yield Handle(handle.ref, self)
            finally:
                self._region_stack.pop()
            return
        region_id = self.next_region_id("map")
        self.regions.append(Region(id=region_id, kind="map", body=[], over=handle.ref))
        self._region_stack.append(region_id)
        try:
            # The element of a mapped collection is, as far as the IR is concerned, the
            # collection's producer: the region is what says "once per element".
            yield Handle(handle.ref, self)
        finally:
            self._region_stack.pop()
            region = next(r for r in self.regions if r.id == region_id)
            if not region.body:
                # Nothing was created in the loop body, so there is no region. Keeping an
                # empty one would fail validation and confuse the graph view.
                self.regions.remove(region)

    def current_region(self) -> str | None:
        return self._region_stack[-1] if self._region_stack else None

    def region_of_node(self, node_id: str) -> Region | None:
        for n in self.nodes:
            if n.id == node_id and n.region:
                return next(r for r in self.regions if r.id == n.region)
        return None

    def inherited_region(self, args: dict[str, Arg], gathered: set[str]) -> str | None:
        """A node consuming a per-element value is per-element work itself.

        This is what makes `[summarize(h) for h in hits]` land in the same map region as
        the `search` that produced `hits`. An argument that came in as the whole
        comprehension list (`gathered`) is the join point and does not propagate.
        """
        for name, arg in args.items():
            if name in gathered or not isinstance(arg, Ref):
                continue
            region = self.region_of_node(arg.node)
            if region and region.kind == "map":
                return region.id
        return None

    # -- nodes -------------------------------------------------------------
    def add(self, spec: NodeSpec, args: dict[str, Arg], gathered: set[str] | None = None) -> Node:
        region = self.current_region() or self.inherited_region(args, gathered or set())
        meta: dict = {}
        if spec.batch:
            meta["batch"] = True
        if self.agent:
            meta["agent"] = self.agent
        if spec.name != spec.target:
            # Which declaration built this node. Part of the content hash and of the CSE
            # key, because the model id alone does not say which prompt was built.
            meta["impl"] = spec.name

        node = Node(
            id=self.next_id(spec.target),
            kind=spec.kind,
            target=spec.target,
            args=args,
            outputs=list(spec.outputs),
            effect=spec.effect,
            determinism=spec.determinism,
            reads=list(spec.reads),
            writes=list(spec.writes),
            staleness_s=spec.staleness_s,
            cache_key_fields=list(spec.cache_key_fields) if spec.cache_key_fields is not None else None,
            region=region,
            origin=dict(spec.origin),
            meta=meta,
        )
        self.nodes.append(node)
        if region:
            next(r for r in self.regions if r.id == region).body.append(node.id)
        return node


_TRACE: contextvars.ContextVar[_Builder | None] = contextvars.ContextVar("synapse_trace", default=None)


def tracing() -> bool:
    return _TRACE.get() is not None


@contextlib.contextmanager
def agent(name: str):
    """Nodes created inside belong to this agent, for a multi-agent workflow.

    Ownership adds no ordering. Agents are ordered only by what they really share: data they pass
    and state keys they touch. See `synapse/core/agents.py`.
    """
    builder = _TRACE.get()
    if builder is None:
        raise RuntimeError("agent() only works inside a @workflow")
    before, builder.agent = builder.agent, name
    try:
        yield
    finally:
        builder.agent = before


# ------------------------------------------------------------------ decorators


def node(
    *,
    kind: NodeKind = "tool",
    target: str | None = None,
    effect: Effect | str = Effect.EXTERNAL,
    determinism: Determinism | None = None,
    outputs: list[str] | None = None,
    reads: list[str] | None = None,
    writes: list[str] | None = None,
    staleness_s: float | None = None,
    cache_key_fields: list[str] | None = None,
    batch: bool = False,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Declare one node type.

    `effect` defaults to `external` -- the restrictive default (docs/IR_SPEC.md
    section 4.3). A tool whose effect nobody declared is assumed to move money until
    someone says otherwise, which makes it slow and correct rather than fast and
    occasionally catastrophic.

    `determinism` is inferred for `model` nodes from the `params` in models.yaml (a seed
    at temperature 0 is `seeded`, which is what makes a model call CSE-able) and
    defaults to `deterministic` for everything else. State it explicitly to override.

    `cache_key_fields` narrows the content-addressed key. The default is every argument,
    and narrowing it is explicit because listing the wrong fields serves one call's
    answer to a different call -- the most dangerous bug in the system, since it
    produces a plausible wrong answer rather than a crash.
    """

    def declare(fn: Callable[..., Any]) -> Callable[..., Any]:
        declaration = fn.__name__
        name = target or declaration
        resolved_effect = Effect(str(effect))
        resolved_determinism = determinism or _infer_determinism(kind, name)
        try:
            source_file = inspect.getsourcefile(fn) or ""
            source_line = inspect.getsourcelines(fn)[1]
        except (OSError, TypeError):  # pragma: no cover - interactive definitions
            source_file, source_line = "", 0

        spec = NodeSpec(
            name=declaration,
            target=name,
            kind=kind,
            effect=resolved_effect,
            determinism=resolved_determinism,
            outputs=tuple(outputs or ["value"]),
            reads=tuple(reads or ()),
            writes=tuple(writes or ()),
            staleness_s=staleness_s,
            cache_key_fields=tuple(cache_key_fields) if cache_key_fields is not None else None,
            batch=batch,
            impl=fn,
            origin={"file": source_file, "line": source_line},
        )
        if resolved_effect == Effect.READ and staleness_s is None:
            raise ValueError(
                f"@node {name!r} has effect 'read' but no staleness_s. There is no default: how stale an "
                "observation may be is a decision only the workflow author can make, and getting it wrong "
                "means serving yesterday's results (docs/PASSES.md section 2)."
            )
        if resolved_effect == Effect.WRITE and not spec.writes:
            raise ValueError(f"@node {name!r} has effect 'write' but declares no writes=[...] keys")
        if declaration in _REGISTRY and _REGISTRY[declaration] != spec:
            raise ValueError(
                f"@node {declaration!r} is declared twice with different settings. One declaration must mean "
                "one effect, or no optimization decision about it is sound."
            )
        other = next((s for s in _REGISTRY.values() if s.target == name and s.effect != spec.effect), None)
        if other is not None:
            raise ValueError(
                f"{name!r} is declared with effect {spec.effect} by {declaration!r} and {other.effect} by "
                f"{other.name!r}. One tool name must mean one effect."
            )
        _REGISTRY[declaration] = spec

        signature = inspect.signature(fn)

        @functools.wraps(fn)
        def call(*call_args, **call_kwargs):
            builder = _TRACE.get()
            if builder is None:
                # Outside a trace the decorator is transparent, so a node implementation
                # stays an ordinary testable function.
                return fn(*call_args, **call_kwargs)
            bound = signature.bind(*call_args, **call_kwargs)
            bound.apply_defaults()
            args: dict[str, Arg] = {}
            gathered: set[str] = set()
            for key, value in bound.arguments.items():
                arg, is_gather = _to_arg(key, value, name)
                args[key] = arg
                if is_gather:
                    gathered.add(key)
            created = builder.add(spec, args, gathered)
            if gathered:
                created.meta["gathers"] = sorted(gathered)
            handles = [Handle(Ref(node=created.id, output=out), builder) for out in created.outputs]
            return handles[0] if len(handles) == 1 else tuple(handles)

        call.__synapse_spec__ = spec  # type: ignore[attr-defined]
        return call

    return declare


def tool(**kw) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """`@node(kind="tool", ...)`, for the common case."""
    kw.setdefault("kind", "tool")
    return node(**kw)


def model(**kw) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """`@node(kind="model", effect=pure, ...)`.

    A model call is `pure` by default: it observes nothing outside the workflow. Its
    determinism, not its effect, is what decides whether two of its samples may be
    collapsed (docs/IR_SPEC.md section 4.2).
    """
    kw.setdefault("kind", "model")
    kw.setdefault("effect", Effect.PURE)
    return node(**kw)


def _infer_determinism(kind: NodeKind, target: str) -> Determinism:
    if kind != "model":
        return "deterministic"
    try:
        from synapse0g.graph.runtime.providers import model_config

        return model_config(target).determinism  # type: ignore[return-value]
    except Exception:  # noqa: BLE001 - an unknown model is stochastic until models.yaml says otherwise
        return "stochastic"


class Workflow:
    """A traced workflow. `compile()` returns a `Graph`; nothing executes."""

    def __init__(self, fn: Callable[..., Any], *, name: str | None = None):
        self.fn = fn
        self.name = name or fn.__name__
        self.signature = inspect.signature(fn)
        functools.update_wrapper(self, fn)

    def __call__(self, *args, **kwargs) -> Graph:
        """Calling a workflow compiles it. It does not run it (SYSTEM.md section 4.1)."""
        return self.compile(*args, **kwargs)

    def compile(self, *args, **kwargs) -> Graph:
        bound = self.signature.bind_partial(*args, **kwargs)
        bound.apply_defaults()
        param_types = {
            key: _type_name(param) for key, param in self.signature.parameters.items()
        }
        builder = _Builder(self.name, dict(bound.arguments), param_types)
        token = _TRACE.set(builder)
        try:
            call_args = {
                key: Handle(Ref(node=INPUT_NODE_ID, output=key), builder) for key in param_types
            }
            result = self.fn(**call_args)
        finally:
            _TRACE.reset(token)

        outputs = _result_outputs(result)
        graph = Graph(
            id=_graph_id(self.name, builder.nodes),
            name=self.name,
            nodes=builder.nodes,
            regions=builder.regions,
            inputs=param_types,
            outputs=outputs,
            meta={
                "frontend": "dsl",
                "input_defaults": {k: v for k, v in builder.param_defaults.items() if not isinstance(v, Handle)},
            },
        )
        return graph.validate_graph()


def workflow(fn: Callable[..., Any] | None = None, *, name: str | None = None):
    """Make a function a workflow. Calling it returns a `Graph`."""
    if fn is None:
        return lambda f: Workflow(f, name=name)
    return Workflow(fn, name=name)


# ---------------------------------------------------------------------- helpers


def _to_arg(name: str, value: Any, target: str) -> tuple[Arg, bool]:
    """One call argument as an `Arg`, plus whether it was a gather.

    Returns `(arg, is_gather)`. A bare handle is per-element consumption; a list of
    handles -- what a comprehension over a map region produces -- is the gather that
    ends the region.
    """
    if isinstance(value, Handle):
        return value.ref, False
    if isinstance(value, (list, tuple)):
        handles = [v for v in value if isinstance(v, Handle)]
        if not handles:
            return as_arg(value), False
        if len(handles) != len(value):
            raise TypeError(
                f"{target}({name}=...) mixes node outputs with plain values in one list. Put the constants in "
                "their own argument so the graph says which parts are data dependencies."
            )
        producers = {h.ref.node for h in handles}
        if len(producers) > 1:
            raise TypeError(
                f"{target}({name}=...) gathers outputs of several nodes ({', '.join(sorted(producers))}). Add a "
                "`transform` node that combines them and pass its output, so the join is a node in the IR."
            )
        return handles[0].ref, True
    return as_arg(value), False


def _type_name(param: inspect.Parameter) -> str:
    annotation = param.annotation
    if annotation is inspect.Parameter.empty:
        return "any"
    return getattr(annotation, "__name__", str(annotation))


def _result_outputs(result: Any) -> dict[str, Ref]:
    """What the workflow returns, as declared outputs."""
    if result is None:
        return {}
    if isinstance(result, Handle):
        return {"result": result.ref}
    if isinstance(result, dict):
        out: dict[str, Ref] = {}
        for key, value in result.items():
            if not isinstance(value, Handle):
                raise TypeError(
                    f"workflow output {key!r} is a plain value, not a node output. A workflow returns what "
                    "its nodes computed, so the graph's outputs resolve to nodes."
                )
            out[str(key)] = value.ref
        return out
    if isinstance(result, (tuple, list)):
        return {f"out{i}": h.ref for i, h in enumerate(result) if isinstance(h, Handle)}
    raise TypeError(
        f"a workflow must return node outputs, not {type(result).__name__}. Nothing has executed yet at "
        "compile time, so a concrete value cannot be the answer."
    )


def _graph_id(name: str, nodes: list[Node]) -> str:
    """Content-addressed, so recompiling an unchanged workflow is the same graph and the
    store does not fill up with near-duplicates during a demo."""
    shape = [
        (n.id, n.kind, n.target, n.effect, n.determinism, sorted(n.args), n.outputs, n.region)
        for n in nodes
    ]
    return f"g_{name}_{fingerprint(shape)[:10]}"
