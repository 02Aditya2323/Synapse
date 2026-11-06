"""SYNAPSE — a compiler and runtime for AI agent workflows.

Lift a workflow into SIR (an effect-typed IR with explicit data dependencies), profile
it with measured numbers, apply optimization passes chosen from that profile, and prove
the optimized version still behaves before shipping it.

    from synapse0g.graph import workflow, node, Effect

    @node(kind="retrieval", effect=Effect.READ, staleness_s=3600)
    def search(query: str) -> list[dict]: ...

    @workflow
    def research(question: str) -> str: ...

Calling `research("...")` builds a `Graph` and executes nothing. See docs/IR_SPEC.md.
"""

from __future__ import annotations

__version__ = "0.3.7"

from synapse0g.graph.connect import wrap  # noqa: E402
from synapse0g.graph.core.sir import (
    SIR_VERSION,
    Determinism,
    Effect,
    Graph,
    Literal_,
    Node,
    NodeKind,
    NodeProfile,
    NoiseBand,
    Profile,
    Ref,
    Region,
)
from synapse0g.graph.frontend.dsl import agent, model, node, tool, workflow

__all__ = [
    "wrap",
    "SIR_VERSION",
    "Determinism",
    "Effect",
    "Graph",
    "Literal_",
    "Node",
    "NodeKind",
    "NodeProfile",
    "NoiseBand",
    "Profile",
    "Ref",
    "Region",
    "__version__",
    "agent",
    "model",
    "node",
    "tool",
    "workflow",
]
