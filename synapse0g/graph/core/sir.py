"""SIR -- the Synapse Intermediate Representation. THE CONTRACT.

This module is the implementation of `docs/IR_SPEC.md`. Every other module reads it:
the frontends write it, the passes transform it, the runtime executes it, the API
serves it and the dashboard renders it.

Changing a field needs a `SIR_VERSION` bump, a migration note in the PR, regenerated
`fixtures/`, and a message to the team **before** merging. Adding an optional field
with a default is allowed without a bump (CLAUDE.md rule 1).

Design, in one line each (docs/IR_SPEC.md section 1):

1. Every dependency is explicit -- in `args` or in `reads`. No hidden channels. This is
   what makes every optimization decidable rather than guessed.
2. Values are SSA: produced once, by one node, never reassigned.
3. Effects are typed and the default is restrictive: an unknown effect is `external`.
4. Control flow is structured (regions), not unrolled.
5. Profiles are attached, not embedded. A graph is meaningful without ever being run.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

SIR_VERSION = 1

NodeKind = Literal["model", "tool", "retrieval", "transform", "control", "io"]
EffectName = Literal["pure", "read", "write", "external", "irreversible"]
Determinism = Literal["deterministic", "seeded", "stochastic"]
RegionKind = Literal["loop", "branch", "map"]


class Effect(StrEnum):
    """The effect lattice, ordered most permissive to most restrictive.

    A StrEnum so `node.effect == "pure"` and `node.effect is Effect.PURE` both work and
    the JSON the dashboard receives is a plain string. The ordering predicates live in
    `synapse.core.effects` -- passes ask that module rather than comparing by hand.
    """

    PURE = "pure"
    READ = "read"
    WRITE = "write"
    EXTERNAL = "external"
    IRREVERSIBLE = "irreversible"


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


class SIRError(ValueError):
    """An invalid graph. Always raised, never warned (docs/IR_SPEC.md section 7)."""


# --------------------------------------------------------------------------- args


class Ref(BaseModel):
    """A dependency on another node's output."""

    model_config = ConfigDict(frozen=True)

    node: str
    output: str = "value"

    def __str__(self) -> str:  # pragma: no cover - display only
        return f"{self.node}.{self.output}"


class Literal_(BaseModel):
    """A constant argument. Named with a trailing underscore so it does not shadow
    `typing.Literal`, which this module also uses."""

    model_config = ConfigDict(frozen=True)

    value: Any = None


Arg = Ref | Literal_


def as_arg(value: Any) -> Arg:
    """Coerce a plain Python value into an `Arg`. Refs pass through."""
    if isinstance(value, (Ref, Literal_)):
        return value
    return Literal_(value=value)


# ----------------------------------------------------------------------- profiles


#: Alias kept because `profile/` and `correctness/` both subclass a shared base.
Model = BaseModel

Split = Literal["train", "calib", "test"]


class NodeProfile(BaseModel):
    """Measured facts about one node, attached by `profile/collect.py`.

    Never written by a frontend. Every field is a measurement: a value the provider did
    not report is `None`, never zero and never an average stood in for a measurement
    (CLAUDE.md rule 5).

    The variance and `partial` fields are the profiler's (Aditya owns this shape per
    ASSIGNMENTS section 2). They are additive optional fields, so they need no
    `SIR_VERSION` bump. `partial` names the measurements that were missing from some
    samples, which is how an aggregate stays honest about what it could not see instead
    of averaging over a hole.
    """

    samples: int = 0
    latency_p50_ms: float = 0.0
    latency_p95_ms: float = 0.0
    tokens_in_mean: float | None = None
    tokens_out_mean: float | None = None
    cost_usd_mean: float | None = None
    failure_rate: float = 0.0
    cache_hit_rate: float = 0.0
    slack_ms: float | None = None
    tokens_in_variance: float | None = None
    tokens_out_variance: float | None = None
    latency_samples: int = 0
    partial: list[str] = Field(default_factory=list)


class ProfileTotals(BaseModel):
    """Whole-graph measurements for one profiling session. Shape fixed by
    docs/API_FOR_UI.md section 2.

    `samples` and `partial` come from the profiler; `tokens` is the convenience sum the
    comparison table renders. Everything is nullable because "the provider did not tell
    us" is a different statement from "zero".
    """

    samples: int = 0
    wall_p50_ms: float = 0.0
    wall_p95_ms: float = 0.0
    tokens_in: float | None = None
    tokens_out: float | None = None
    tokens: float | None = None
    cost_usd: float | None = None
    provider_calls: float | None = None
    cache_hit_rate: float | None = None
    partial: list[str] = Field(default_factory=list)


#: The profiler's name for the same thing.
Measurements = ProfileTotals


class NoiseBand(BaseModel):
    """The measured run-to-run spread of the *unmodified* graph.

    A "gain" inside this band is not a gain (docs/CORRECTNESS.md section 4.3). Measured
    on `train` or `calib` only -- measuring it on `test` would be tuning on the split we
    report from.
    """

    latency_pct: float
    samples: int
    split: Literal["train", "calib"] = "calib"
    cost_pct: float | None = None
    method: str = "p95 absolute paired relative difference"


class CriticalPathEntry(BaseModel):
    node: str
    latency_p50_ms: float
    slack_ms: float = 0.0


class Bottleneck(BaseModel):
    node: str
    share_of_critical_path: float
    reason: str  # a human sentence written by the profiler; the UI renders it verbatim


class Profile(BaseModel):
    """A graph's measured profile.

    Produced by `synapse/profile/collect.py` (Aditya) and consumed by every pass's
    `preconditions()` and `predict()`. It lives here, beside `NodeProfile`, because the
    pass protocol and the HTTP contract both need its shape frozen at H2.

    `Profile.empty(graph_id)` is the honest "nothing measured yet" value: a pass that
    needs measurements to justify itself returns a zero-confidence estimate against it
    rather than inventing numbers.
    """

    graph_id: str
    executions: int = 0
    crashed: int = 0  # counted separately, never as a slow workflow (CLAUDE.md rule 10)
    cold: bool = True
    total: ProfileTotals = Field(default_factory=ProfileTotals)
    nodes: dict[str, NodeProfile] = Field(default_factory=dict)
    critical_path: list[CriticalPathEntry] = Field(default_factory=list)
    bottlenecks: list[Bottleneck] = Field(default_factory=list)
    noise: NoiseBand | None = None
    split: Split | None = None
    task_ids: list[str] = Field(default_factory=list)
    meta: dict = Field(default_factory=dict)

    @property
    def noise_band_pct(self) -> float | None:
        """The latency noise band as a percentage, or `None` if it was never measured.

        `None` is a real answer and the passes treat it as one: with no measured band,
        `Estimate.beats_noise` declines to rule a gain out, and the firewall is what
        settles it later.
        """
        return self.noise.latency_pct if self.noise else None

    @classmethod
    def empty(cls, graph_id: str) -> Profile:
        return cls(graph_id=graph_id, executions=0)

    @property
    def is_empty(self) -> bool:
        return self.executions == 0

    def node(self, node_id: str) -> NodeProfile | None:
        return self.nodes.get(node_id)

    def latency_of(self, node_id: str) -> float:
        p = self.nodes.get(node_id)
        return p.latency_p50_ms if p else 0.0

    def slack_of(self, node_id: str) -> float | None:
        p = self.nodes.get(node_id)
        return p.slack_ms if p else None

    def critical_path_ids(self) -> list[str]:
        return [e.node for e in self.critical_path]


# -------------------------------------------------------------------------- nodes


class Node(BaseModel):
    """One unit of work. Its dependencies are exactly what is in `args` and `reads`."""

    model_config = ConfigDict(protected_namespaces=())

    id: str
    kind: NodeKind
    target: str  # registry model id / tool name / retriever name -- never a raw provider tag
    args: dict[str, Arg] = Field(default_factory=dict)
    outputs: list[str] = Field(default_factory=lambda: ["value"])

    effect: Effect = Effect.EXTERNAL  # restrictive default, deliberately (IR_SPEC section 4.3)
    determinism: Determinism = "deterministic"

    reads: list[str] = Field(default_factory=list)  # state keys observed
    writes: list[str] = Field(default_factory=list)  # state keys mutated

    cache_key_fields: list[str] | None = None  # None = every argument
    staleness_s: float | None = None  # `read` only: how long a cached observation stays valid
    region: str | None = None

    profile: NodeProfile | None = None
    origin: dict = Field(default_factory=dict)  # {"file":..,"line":..} or {"run":..,"step":..}
    meta: dict = Field(default_factory=dict)

    # -- dependencies ------------------------------------------------------
    def refs(self) -> list[tuple[str, Ref]]:
        """(argument name, Ref) for every argument that depends on another node."""
        return [(name, arg) for name, arg in self.args.items() if isinstance(arg, Ref)]

    def depends_on(self) -> set[str]:
        return {r.node for _, r in self.refs()}

    def literal_args(self) -> dict[str, Any]:
        return {name: arg.value for name, arg in self.args.items() if isinstance(arg, Literal_)}

    def all_args_literal(self) -> bool:
        return all(isinstance(a, Literal_) for a in self.args.values())

    def label(self) -> str:  # pragma: no cover - display only
        return f"{self.id} {self.kind}:{self.target}"


class Region(BaseModel):
    """Structured control flow. Loops and branches are regions, never unrolled --
    a 300-node unrolled graph is not a picture a human can read, and legibility is a
    product requirement here (docs/IR_SPEC.md section 1.4)."""

    id: str
    kind: RegionKind
    body: list[str] = Field(default_factory=list)
    condition: Ref | None = None
    max_iterations: int | None = None
    over: Arg | None = None  # `map` only: the collection mapped over
    meta: dict = Field(default_factory=dict)


# -------------------------------------------------------------------------- edges


class DataEdge(BaseModel):
    """`from` produced a value `to` consumes. An absolute ordering constraint, but it
    constrains *only* these two nodes -- which is why parallelism can break the rest."""

    model_config = ConfigDict(populate_by_name=True)

    src: str = Field(alias="from")
    dst: str = Field(alias="to")
    output: str
    arg: str

    def model_dump(self, **kw):  # serialize as {"from": .., "to": ..} for the UI
        kw.setdefault("by_alias", True)
        return super().model_dump(**kw)


class EffectEdge(BaseModel):
    """An ordering constraint that no pass may break. Derived from the effect lattice
    and the reads/writes sets -- never authored by hand, so it cannot drift."""

    model_config = ConfigDict(populate_by_name=True)

    src: str = Field(alias="from")
    dst: str = Field(alias="to")
    reason: str

    def model_dump(self, **kw):
        kw.setdefault("by_alias", True)
        return super().model_dump(**kw)


# -------------------------------------------------------------------------- graph


class Graph(BaseModel):
    """A workflow as a dependency-aware execution graph."""

    id: str
    sir_version: int = SIR_VERSION
    name: str
    nodes: list[Node] = Field(default_factory=list)
    regions: list[Region] = Field(default_factory=list)
    inputs: dict[str, Any] = Field(default_factory=dict)
    outputs: dict[str, Ref] = Field(default_factory=dict)
    derived_from: str | None = None  # the graph this was optimized from
    pipeline: list[str] = Field(default_factory=list)  # passes applied to reach this graph
    created_at: str = Field(default_factory=utc_now)
    meta: dict = Field(default_factory=dict)

    # -- lookup ------------------------------------------------------------
    @property
    def node_ids(self) -> list[str]:
        return [n.id for n in self.nodes]

    def node(self, node_id: str) -> Node:
        for n in self.nodes:
            if n.id == node_id:
                return n
        raise KeyError(f"no node {node_id!r} in graph {self.id!r}")

    def has(self, node_id: str) -> bool:
        return any(n.id == node_id for n in self.nodes)

    def region(self, region_id: str) -> Region:
        for r in self.regions:
            if r.id == region_id:
                return r
        raise KeyError(f"no region {region_id!r} in graph {self.id!r}")

    def region_of(self, node_id: str) -> Region | None:
        rid = self.node(node_id).region
        return self.region(rid) if rid else None

    def index(self, node_id: str) -> int:
        """Program order position. Effect edges are derived in this order."""
        for i, n in enumerate(self.nodes):
            if n.id == node_id:
                return i
        raise KeyError(f"no node {node_id!r} in graph {self.id!r}")

    # -- derived edges (docs/IR_SPEC.md section 3) -------------------------
    def data_edges(self) -> list[DataEdge]:
        out: list[DataEdge] = []
        for n in self.nodes:
            for arg_name, ref in n.refs():
                out.append(DataEdge(**{"from": ref.node, "to": n.id, "output": ref.output, "arg": arg_name}))
        return out

    def effect_edges(self) -> list[EffectEdge]:
        """Ordering constraints that exist because of observable effects, not data.

        Two rules, both from docs/IR_SPEC.md section 3.2:
        - same state key, at least one writer -> an edge in program order (RAW/WAW/WAR);
        - every `external`/`irreversible` node is ordered against every other one, so
          observable side effects keep their relative order. Always.
        """
        out: list[EffectEdge] = []
        for i, a in enumerate(self.nodes):
            for b in self.nodes[i + 1 :]:
                reason = self._effect_reason(a, b)
                if reason:
                    out.append(EffectEdge(**{"from": a.id, "to": b.id, "reason": reason}))
        return out

    @staticmethod
    def _effect_reason(a: Node, b: Node) -> str | None:
        side_effecting = {Effect.EXTERNAL, Effect.IRREVERSIBLE}
        if a.effect in side_effecting and b.effect in side_effecting:
            return f"observable side effects keep program order ({a.effect} then {b.effect})"
        aw, bw = set(a.writes), set(b.writes)
        ar, br = set(a.reads), set(b.reads)
        if shared := aw & bw:
            return f"write-after-write on key {sorted(shared)[0]!r}"
        if shared := aw & br:
            return f"read-after-write on key {sorted(shared)[0]!r}"
        if shared := ar & bw:
            return f"write-after-read on key {sorted(shared)[0]!r}"
        return None

    def predecessors(self, node_id: str, *, effects: bool = True) -> set[str]:
        """Everything that must complete before `node_id` may start."""
        preds = self.node(node_id).depends_on()
        if effects:
            preds |= {e.src for e in self.effect_edges() if e.dst == node_id}
        return preds

    def successors(self, node_id: str, *, effects: bool = True) -> set[str]:
        out = {n.id for n in self.nodes if node_id in n.depends_on()}
        if effects:
            out |= {e.dst for e in self.effect_edges() if e.src == node_id}
        return out

    def consumers(self, node_id: str) -> list[tuple[str, str]]:
        """(consumer node id, output name) for every reader of this node's outputs."""
        return [(n.id, r.output) for n in self.nodes for _, r in n.refs() if r.node == node_id]

    def topo_order(self, *, effects: bool = True) -> list[str]:
        """Kahn's algorithm, stable in program order so two runs schedule identically."""
        preds = {n.id: set(self.predecessors(n.id, effects=effects)) for n in self.nodes}
        order: list[str] = []
        done: set[str] = set()
        while True:
            ready = sorted(
                (n.id for n in self.nodes if n.id not in done and preds[n.id] <= done),
                key=self.index,
            )
            if not ready:
                break
            for nid in ready:
                order.append(nid)
                done.add(nid)
        if len(order) != len(self.nodes):
            missing = [n.id for n in self.nodes if n.id not in done]
            raise SIRError(f"graph {self.id!r} has a cycle through {', '.join(missing)}")
        return order

    def reachable_from_outputs(self) -> set[str]:
        """Nodes whose values the workflow's declared outputs actually depend on.
        `dce` uses this; so does `context_prune` when deciding what is referenced."""
        seen: set[str] = set()
        frontier = [r.node for r in self.outputs.values()]
        while frontier:
            nid = frontier.pop()
            if nid in seen or not self.has(nid):
                continue
            seen.add(nid)
            frontier.extend(self.node(nid).depends_on())
            region = self.region_of(nid)
            if region and region.condition:
                frontier.append(region.condition.node)
        return seen

    # -- mutation (passes build new graphs; they never edit in place) ------
    def copy_with(
        self,
        *,
        id: str | None = None,
        nodes: list[Node] | None = None,
        regions: list[Region] | None = None,
        outputs: dict[str, Ref] | None = None,
        pipeline: list[str] | None = None,
        derived_from: str | None = None,
        name: str | None = None,
        meta: dict | None = None,
    ) -> Graph:
        """A new graph with some parts replaced. Passes return these; nothing is
        mutated under the caller, so an optimization can always be shown side by side
        with the original."""
        return Graph(
            id=id or self.id,
            sir_version=self.sir_version,
            name=name if name is not None else self.name,
            nodes=[n.model_copy(deep=True) for n in (nodes if nodes is not None else self.nodes)],
            regions=[r.model_copy(deep=True) for r in (regions if regions is not None else self.regions)],
            inputs=dict(self.inputs),
            outputs=dict(outputs if outputs is not None else self.outputs),
            derived_from=derived_from if derived_from is not None else self.derived_from,
            pipeline=list(pipeline if pipeline is not None else self.pipeline),
            meta={**self.meta, **(meta or {})},
        )

    def with_profile(self, profile: Profile) -> Graph:
        """Attach per-node measurements for serving to the UI. Does not change structure."""
        g = self.copy_with()
        for n in g.nodes:
            n.profile = profile.nodes.get(n.id)
        return g

    def rewrite_refs(self, mapping: dict[str, str]) -> Graph:
        """Point every Ref at `old` to `new`. The workhorse of `cse`."""
        if not mapping:
            return self.copy_with()
        nodes = []
        for n in self.nodes:
            copy = n.model_copy(deep=True)
            copy.args = {
                name: (Ref(node=mapping.get(a.node, a.node), output=a.output) if isinstance(a, Ref) else a)
                for name, a in copy.args.items()
            }
            nodes.append(copy)
        outputs = {k: Ref(node=mapping.get(r.node, r.node), output=r.output) for k, r in self.outputs.items()}
        regions = []
        for r in self.regions:
            copy = r.model_copy(deep=True)
            copy.body = [mapping.get(b, b) for b in copy.body]
            if copy.condition:
                copy.condition = Ref(
                    node=mapping.get(copy.condition.node, copy.condition.node), output=copy.condition.output
                )
            regions.append(copy)
        return self.copy_with(nodes=nodes, regions=regions, outputs=outputs)

    def without(self, node_ids: Iterable[str]) -> Graph:
        """Drop nodes. The caller is responsible for there being no dangling Refs --
        `validate_graph()` will say so loudly if there are."""
        drop = set(node_ids)
        regions = []
        for r in self.regions:
            copy = r.model_copy(deep=True)
            copy.body = [b for b in copy.body if b not in drop]
            regions.append(copy)
        return self.copy_with(
            nodes=[n for n in self.nodes if n.id not in drop],
            regions=[r for r in regions if r.body],
        )

    # -- validation (docs/IR_SPEC.md section 7) ----------------------------
    def validate_graph(self) -> Graph:
        """Run on every construction and after every pass. Raises `SIRError`.

        A pass that produces an invalid graph is a bug that must fail loudly and
        immediately, rather than produce a subtly wrong execution. Validation failures
        are never warnings and are never swallowed.
        """
        problems: list[str] = []

        if self.sir_version != SIR_VERSION:
            problems.append(f"sir_version {self.sir_version} != running SIR_VERSION {SIR_VERSION}")

        seen: set[str] = set()
        for n in self.nodes:
            if n.id in seen:
                problems.append(f"duplicate node id {n.id!r}")
            seen.add(n.id)

        for n in self.nodes:
            for arg_name, ref in n.refs():
                if ref.node == n.id:
                    problems.append(f"{n.id}.{arg_name} refers to itself")
                elif ref.node not in seen:
                    problems.append(f"{n.id}.{arg_name} refers to unknown node {ref.node!r}")
                elif ref.output not in self.node(ref.node).outputs:
                    problems.append(
                        f"{n.id}.{arg_name} wants output {ref.output!r} of {ref.node!r}, "
                        f"which declares {self.node(ref.node).outputs}"
                    )

            # effect / declaration consistency
            if n.effect == Effect.WRITE and not n.writes:
                problems.append(f"{n.id} has effect 'write' but declares no writes; say which keys it mutates")
            if n.effect == Effect.PURE and (n.reads or n.writes):
                problems.append(f"{n.id} is 'pure' but declares reads={n.reads} writes={n.writes}")
            if n.staleness_s is not None and n.effect != Effect.READ:
                problems.append(f"{n.id} sets staleness_s but its effect is {n.effect!r}, not 'read'")
            if not n.outputs:
                problems.append(f"{n.id} declares no outputs")
            if n.cache_key_fields is not None:
                unknown = [f for f in n.cache_key_fields if f not in n.args]
                if unknown:
                    problems.append(f"{n.id}.cache_key_fields names non-arguments: {', '.join(unknown)}")

        region_members: dict[str, str] = {}
        for r in self.regions:
            for b in r.body:
                if b not in seen:
                    problems.append(f"region {r.id!r} lists unknown node {b!r}")
                elif b in region_members:
                    problems.append(f"node {b!r} is in both region {region_members[b]!r} and {r.id!r}")
                else:
                    region_members[b] = r.id
            if r.condition and r.condition.node not in seen:
                problems.append(f"region {r.id!r} condition refers to unknown node {r.condition.node!r}")
            if r.kind == "loop" and r.condition is None:
                problems.append(f"loop region {r.id!r} has no continuation condition")

        for name, ref in self.outputs.items():
            if ref.node not in seen:
                problems.append(f"output {name!r} refers to unknown node {ref.node!r}")
            elif ref.output not in self.node(ref.node).outputs:
                problems.append(f"output {name!r} wants {ref.output!r} of {ref.node!r}, which does not declare it")

        if not problems:
            try:
                self.topo_order()
            except SIRError as exc:
                problems.append(str(exc))

        if problems:
            raise SIRError(f"invalid graph {self.id!r}:\n  - " + "\n  - ".join(problems))
        return self

    # -- serialization -----------------------------------------------------
    def to_ui(self, profile: Profile | None = None) -> dict:
        """The JSON shape docs/API_FOR_UI.md section 1 promises: the graph plus derived
        edges, so the dashboard never recomputes them and never disagrees with the
        scheduler."""
        graph = self.with_profile(profile) if profile else self
        body = graph.model_dump(mode="json")
        body["edges"] = {
            "data": [e.model_dump() for e in self.data_edges()],
            "effect": [e.model_dump() for e in self.effect_edges()],
        }
        body["n_nodes"] = len(self.nodes)
        if profile:
            body["critical_path"] = profile.critical_path_ids()
            body["critical_path_ms"] = sum(e.latency_p50_ms for e in profile.critical_path)
        return body

    def summary(self) -> dict:
        """The list-endpoint row (docs/API_FOR_UI.md section 1)."""
        return {
            "id": self.id,
            "name": self.name,
            "sir_version": self.sir_version,
            "n_nodes": len(self.nodes),
            "derived_from": self.derived_from,
            "pipeline": list(self.pipeline),
            "created_at": self.created_at,
        }
