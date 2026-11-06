"""LangGraph graphs -> SIR, with dependencies taken from measured state reads and writes.

    from synapse0g.graph.frontend.adapters.langgraph import compile_langgraph

    program = compile_langgraph(builder, sample={"topic": "bees"},
                                nodes={"facts": "pure", "search": {"effect": "read", "staleness_s": 3600}})
    program.graph                       # SIR
    program.run({"topic": "ants"}, graph=optimized)

LangGraph nodes share one state dict. A node reads some keys and returns updates to others, and the
graph's edges say what runs after what. Those edges are often a habit, not a need: `a -> b -> c`
written in order although `b` never reads anything `a` wrote. Synapse asks the data instead.

**Reads and writes are measured, never guessed.** Each node declared `pure` or `read` runs once on
`sample` against a state that records every key it reads, and its returned keys are what it writes.
Reading the state as a whole, such as `f"{state}"` or `len(state)`, counts as reading every key.
A node nobody declared is `external` : it is never run while tracing, and it is
assumed to read and write every key, so nothing moves around it. An `external` node may declare the
keys it updates (`{"effect": "external", "updates": ["published"]}`); it still reads every key.

**Edges in SIR come from that data flow**: a node depends on the last node before it, in the
graph's own order, that wrote a key it reads. Values are SSA, so a later write cannot change what an
earlier node read. The trace is one input: a node whose reads depend on the input could read more on
another, which is exactly what the firewall's corpus run is there to catch.

Refused, with a message, rather than approximated: conditional edges, cycles, and state keys with a
reducer (several writers merging into one key).
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, get_type_hints

from synapse0g.graph.core import effects
from synapse0g.graph.core.hashing import fingerprint
from synapse0g.graph.core.sir import Effect, Graph, Literal_, Node, Ref, SIRError
from synapse0g.graph.frontend.adapters.state_trace import RecordingState
from synapse0g.graph.frontend.dsl import INPUT_NODE_ID, INPUT_TARGET

try:
    from langgraph.graph import END, START, StateGraph
except ImportError as exc:  # pragma: no cover - exercised only without the extra installed
    raise ImportError("The LangGraph adapter needs LangGraph: pip install 'synapse0g[agents]'") from exc

__all__ = ["CompiledLangGraph", "LangGraphCompileError", "compile_langgraph"]

FRAMEWORK = "langgraph"
OUT_NODE_ID = "n_state"


class LangGraphCompileError(SIRError):
    """A graph shape the adapter does not compile yet. Said plainly, never approximated."""


@dataclass
class CompiledLangGraph:
    builder: Any
    graph: Graph
    implementations: dict[str, Callable[..., Any]]
    inputs: list[str]
    notes: list[str] = field(default_factory=list)

    def run(self, inputs: Mapping[str, Any], *, graph: Graph | None = None, **kw):
        from synapse0g.graph.runtime.scheduler import run_graph_sync

        return run_graph_sync(graph or self.graph, task_input=dict(inputs), implementations=self.implementations, **kw)

    def baseline_measured(self, inputs: Mapping[str, Any]) -> dict[str, Any]:
        """The graph run by LangGraph itself (`compile().invoke`), measured the same way."""
        from synapse0g.graph.runtime import meter

        app = self.builder.compile()
        started = time.perf_counter()
        with meter.measuring() as used:
            state = app.invoke(dict(inputs))
        return {"label": "graph.invoke()", "wall_ms": (time.perf_counter() - started) * 1000.0,
                "provider_calls": used.calls, "tokens_in": used.tokens_in if used.calls else None,
                "tokens_out": used.tokens_out if used.calls else None, "output": dict(state)}


def compile_langgraph(builder: Any, sample: Mapping[str, Any], *, name: str | None = None,
                      nodes: Mapping[str, Any] | None = None) -> CompiledLangGraph:
    """Lift a LangGraph `StateGraph` (or its compiled app) into SIR, tracing declared nodes on `sample`."""
    builder = getattr(builder, "builder", builder)
    if not isinstance(builder, StateGraph):
        raise LangGraphCompileError(
            f"expected a langgraph StateGraph or its compiled app, got {type(builder).__name__}")
    if builder.branches:
        raise LangGraphCompileError(
            f"conditional edges from {', '.join(builder.branches)} are not compiled yet: which node runs next is "
            "decided at run time, which needs a branch region. Only graphs with fixed edges are supported."
        )
    _refuse_reducers(builder)
    order = _order(builder)
    keys = _state_keys(builder, sample)
    declared = {k: _as_effect(k, v) for k, v in (nodes or {}).items()}
    notes: list[str] = []
    unknown = sorted(set(declared) - set(order))
    if unknown:
        notes.append(f"effects declared for nodes this graph does not have, ignored: {', '.join(unknown)}")
    state = dict(sample)
    reads: dict[str, list[str]] = {}
    writes: dict[str, list[str]] = {}
    traced: set[str] = set()
    for node_name in order:
        spec = declared.get(node_name)
        if spec is None or effects.rank(spec["effect"]) > effects.rank(Effect.READ):
            # Never executed while tracing: it reads everything, and writes what it declares it updates,
            # or else everything, so nothing moves around it.
            reads[node_name] = list(keys)
            writes[node_name] = sorted((spec or {}).get("updates") or keys)
            if spec is None:
                notes.append(f"{node_name}: no declared effect, so it is external, never traced, and assumed to read "
                             "and write every state key.")
            continue
        traced.add(node_name)
        recording = RecordingState(state)
        update = _call(builder, node_name, recording)
        reads[node_name] = sorted(keys if recording.read_everything else recording.read & set(keys))
        writes[node_name] = sorted(update)
        state.update(update)

    sir_nodes = [Node(id=INPUT_NODE_ID, kind="transform", target=INPUT_TARGET, outputs=sorted(sample) or ["value"],
                      effect=Effect.PURE, determinism="deterministic")]
    last_writer = {k: (INPUT_NODE_ID, k) for k in sample}
    implementations: dict[str, Callable[..., Any]] = {}
    for node_name in order:
        spec = declared.get(node_name) or {"effect": Effect.EXTERNAL}
        args: dict[str, Any] = {"node": Literal_(value={"framework": FRAMEWORK, "graph": name or "graph",
                                                        "node": node_name})}
        for key in reads[node_name]:
            if key in last_writer:
                src, out = last_writer[key]
                args[f"in_{key}"] = Ref(node=src, output=out)
        outputs = writes[node_name] or ["value"]
        impl_key = f"langgraph.{fingerprint([name, node_name])[:16]}"
        effect = spec["effect"]
        sir_nodes.append(Node(
            id=node_name, kind="tool", target=f"langgraph:{node_name}", args=args, outputs=outputs, effect=effect,
            determinism=spec.get("determinism", "stochastic"),
            staleness_s=spec.get("staleness_s") if effect == Effect.READ else None,
            reads=spec.get("writes", []) if effect == Effect.WRITE else [],
            writes=spec.get("writes", []) if effect == Effect.WRITE else [],
            origin={"framework": FRAMEWORK, "node": node_name},
            meta={"impl": impl_key, "framework": FRAMEWORK, "measured_reads": reads[node_name],
                  "measured_writes": writes[node_name]},
        ))
        implementations[impl_key] = _node_runner(builder, node_name, outputs,
                                                 assumed=node_name not in traced and outputs == sorted(keys))
        for key in writes[node_name]:
            last_writer[key] = (node_name, key)

    final_keys = sorted(last_writer)
    sir_nodes.append(Node(
        id=OUT_NODE_ID, kind="transform", target="langgraph_state", effect=Effect.PURE, determinism="deterministic",
        args={f"in_{k}": Ref(node=last_writer[k][0], output=last_writer[k][1]) for k in final_keys},
        outputs=["state"], meta={"impl": "langgraph.state"},
    ))
    implementations["langgraph.state"] = lambda **kw: {k[3:]: v for k, v in kw.items()}

    digest = fingerprint([n.model_dump(mode="json") for n in sir_nodes])[:10]
    gid = f"g_langgraph_{(name or 'graph').replace(' ', '_')}_{digest}"
    graph = Graph(id=gid, name=name or "graph", nodes=sir_nodes, inputs=dict.fromkeys(sorted(sample), "any"),
                  outputs={"result": Ref(node=OUT_NODE_ID, output="state")},
                  meta={"frontend": "langgraph", "notes": notes, "declared_order": order})
    return CompiledLangGraph(builder=builder, graph=graph.validate_graph(), implementations=implementations,
                             inputs=sorted(sample), notes=notes)


# --------------------------------------------------------------------------- helpers


def _order(builder: Any) -> list[str]:
    """The graph's own node order: a topological order of its fixed edges. A cycle is refused."""
    names = list(builder.nodes)
    succ: dict[str, list[str]] = {n: [] for n in [START, *names, END]}
    indeg = dict.fromkeys(names, 0)
    for src, dst in builder.edges:
        sources = [src] if isinstance(src, str) else list(src)  # a join edge lists several sources
        for s in sources:
            succ.setdefault(s, []).append(dst)
            if dst in indeg and s != START:  # START is not a node: an edge from it orders nothing
                indeg[dst] += 1
    ready = [n for n in names if indeg[n] == 0]
    out: list[str] = []
    while ready:
        n = ready.pop(0)
        out.append(n)
        for d in succ.get(n, []):
            if d in indeg:
                indeg[d] -= 1
                if indeg[d] == 0:
                    ready.append(d)
    if len(out) != len(names):
        raise LangGraphCompileError("the graph has a cycle; loops are not compiled yet")
    return out


def _schema_keys(builder: Any) -> list[str]:
    try:
        return list(get_type_hints(builder.state_schema))
    except Exception:  # noqa: BLE001 - a schema we cannot read: the sample's keys are all we know
        return []


def _state_keys(builder: Any, sample: Mapping[str, Any]) -> list[str]:
    return sorted(set(_schema_keys(builder)) | set(sample))


def _refuse_reducers(builder: Any) -> None:
    channels = getattr(builder, "channels", {}) or {}
    reducing = [k for k, ch in channels.items() if type(ch).__name__ == "BinaryOperatorAggregate"]
    if reducing:
        raise LangGraphCompileError(
            f"state keys {', '.join(sorted(reducing))} have a reducer, so several nodes merge into one value. "
            "That is not compiled yet; only keys that each write replaces are supported."
        )


def _as_effect(node_name: str, value: Any) -> dict:
    spec = {"effect": value} if isinstance(value, str) else dict(value)
    spec["effect"] = Effect(str(spec["effect"]))
    if spec["effect"] == Effect.READ and spec.get("staleness_s") is None:
        raise LangGraphCompileError(f"{node_name} is 'read' and needs staleness_s")
    if spec["effect"] == Effect.WRITE and not spec.get("writes"):
        raise LangGraphCompileError(f"{node_name} is 'write' and needs writes=[...]")
    return spec


def _call(builder: Any, node_name: str, state: Mapping[str, Any]) -> dict:
    update = builder.nodes[node_name].runnable.invoke(state)
    if update is None:
        return {}
    if not isinstance(update, Mapping):
        raise LangGraphCompileError(f"{node_name} returned {type(update).__name__}; only dict updates are supported")
    return dict(update)


def _node_runner(builder: Any, node_name: str, outputs: list[str], *, assumed: bool) -> Callable[..., Any]:
    """`assumed`: an untraced node, taken to write every key. A key it leaves alone keeps its value.

    The state is in the schema's order, as LangGraph gives it, because a node that prints the state
    into a prompt would otherwise send the model different text."""
    schema = _schema_keys(builder)

    def run_node(node: dict, **kw: Any) -> Any:
        given = {k[3:]: v for k, v in kw.items()}
        state = {**{k: given[k] for k in schema if k in given}, **given}
        update = _call(builder, node_name, state)
        if assumed:
            update = {k: update.get(k, state.get(k)) for k in outputs}
        missing = [k for k in outputs if k != "value" and k not in update]
        if missing:
            raise RuntimeError(f"{node_name} did not write {', '.join(missing)} this time, though it did when traced. "
                               "Its writes depend on the input, which this compile cannot see.")
        if outputs == ["value"]:
            return None
        return {k: update[k] for k in outputs} if len(outputs) > 1 else update[outputs[0]]

    return run_node
