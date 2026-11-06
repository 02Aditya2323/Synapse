"""What the benchmark workflows share: declarations and the turn chain.

A model node's implementation returns its prompt and the runtime makes the call, so tokens
are measured in one place. Many declarations may share one model id.

SYNAPSE_BENCH_MODEL picks the model; the default is agent-zg, qwen3.8-flash on 0G.
"""

from __future__ import annotations

import os
from typing import Any

from synapse0g.graph.core.sir import Effect, Graph, Ref, Region
from synapse0g.graph.frontend.dsl import Handle, model, node

MODEL_ID = os.environ.get("SYNAPSE_BENCH_MODEL", "agent-zg")


def declare(target: str, **kw):
    """`@node`, declared under its target. The DSL keys a declaration on the function's
    name, and three workflows here each have a `plan_prompt`."""

    def wrap(fn):
        fn.__name__ = target.replace(".", "_")
        return node(target=target, **kw)(fn)

    return wrap


def prompt(name: str):
    """A model call: the function builds the prompt."""

    def wrap(fn):
        fn.__name__ = name.replace(".", "_")
        return model(target=MODEL_ID)(fn)

    return wrap


def transform(target: str, outputs: list[str] | None = None):
    """A pure, deterministic step: prompt building and parsing."""
    return declare(target, kind="transform", effect=Effect.PURE, outputs=outputs)


def last_given(*states: Any) -> Any:
    """The latest turn that ran: a turn inside a branch not taken leaves None."""
    return next((s for s in reversed(states) if s is not None), None)


def _node_id(value: Handle) -> str:
    return value.ref.node


def guard(graph: Graph, turns: list[tuple[list, Ref]]) -> Graph:
    """Put each turn's nodes in a `branch` region on its condition.

    SIR has no loop-carried values, since a `loop` body cannot read its own previous
    output, so an agent loop compiles to a bounded chain of turns and a turn runs only
    if the one before it asked to go on. The DSL cannot trace a branch, so the regions
    are attached here, after compiling.
    """
    nodes = {n.id: n for n in graph.nodes}
    for i, (handles, condition) in enumerate(turns):
        region = Region(id=f"r_turn{i + 1}", kind="branch", condition=condition,
                        body=[_node_id(h) for h in handles])
        graph.regions.append(region)
        for node_id in region.body:
            nodes[node_id].region = region.id
    return graph.validate_graph()
