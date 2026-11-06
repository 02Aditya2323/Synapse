"""`synapse0g.compile`: an existing LangGraph app, compiled into a graph and optimized.

    import synapse0g
    app = synapse0g.compile(builder, sample={"rfq": rfq}, effects=EFFECTS)   # was: builder.compile()
    state = app.invoke({"rfq": rfq})                                          # as before

What happens, with nothing else in the app changed:

1. Every step declared `pure` or `read` runs once on `sample`, and the state keys it really reads
   and writes are recorded. Edges come from that data flow, not from the order the app was
   written in. An undeclared step is treated as touching the outside world: never traced, never
   moved, assumed to read and write everything.
2. The graph passes run with their preconditions: independent steps run at the same time, a
   pure step with the same inputs is computed once and reused, and so on. Each pass that declines
   says why. `irreversible` steps are never reordered, cached, repeated or run speculatively.
3. `invoke` runs the optimized graph. An irreversible step runs only with `allow_irreversible=True`;
   otherwise it is skipped and reported, never run silently.

Whether the optimized app gives the same answers is not assumed: `synapse0g bench --agent supplier`
measures both on held-out tasks, and the firewall decides.
"""

from __future__ import annotations

import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from synapse0g.graph.core.sir import Graph, Profile
from synapse0g.graph.core.store import Store
from synapse0g.graph.frontend.adapters.langgraph import CompiledLangGraph, compile_langgraph
from synapse0g.graph.passes.base import PassResult, run_pipeline
from synapse0g.graph.runtime.cache import Cache
from synapse0g.graph.runtime.executor import Execution, ExecutionMode
from synapse0g.graph.runtime.scheduler import run_graph_sync


class RunFailed(RuntimeError):
    def __init__(self, execution: Execution):
        super().__init__(execution.error or "the run crashed")
        self.execution = execution


@dataclass
class App:
    compiled: CompiledLangGraph
    before: Graph
    after: Graph
    passes: list[PassResult]
    cache: Cache = field(repr=False)
    last: Execution | None = None

    @property
    def applied(self) -> list[str]:
        return [p.pass_name for p in self.passes if p.applied]

    def run(self, inputs: dict, *, optimized: bool = True, mode: ExecutionMode = ExecutionMode.LIVE,
            allow_irreversible: bool = False) -> Execution:
        """One execution, measured. `optimized=False` runs the compiled graph unchanged, one step at a time."""
        graph = self.after if optimized else self.before
        execution = run_graph_sync(graph, task_input=dict(inputs), implementations=self.compiled.implementations,
                                   mode=mode, allow_irreversible=allow_irreversible,
                                   cache=self.cache if optimized else None)
        self.last = execution
        return execution

    def invoke(self, inputs: dict, *, allow_irreversible: bool = False) -> dict:
        """What `builder.compile().invoke(inputs)` returns, from the optimized graph."""
        execution = self.run(inputs, allow_irreversible=allow_irreversible)
        if execution.crashed:
            raise RunFailed(execution)
        return execution.outputs["result"]

    def explain(self) -> str:
        lines = [f"{self.before.name}: {len(self.before.nodes) - 2} steps, as written: "
                 + " -> ".join(self.before.meta.get("declared_order", []))]
        for node in self.before.nodes:
            if node.meta.get("framework"):
                deps = sorted({ref.node for ref in node.args.values() if hasattr(ref, "node")} - {"n_in"})
                lines.append(f"  {node.id:<20} {node.effect:<13} reads {', '.join(node.meta['measured_reads']) or '-'}"
                             f"; waits for {', '.join(deps) or 'nothing'}")
        for p in self.passes:
            if p.applied:
                for t in p.transformations:
                    lines.append(f"  applied  {p.pass_name}: {t.description}")
        for p in self.passes:
            for f in p.findings:
                node = next((n for n in self.before.nodes if n.id == f.node), None)
                if node is not None and node.effect == "irreversible":
                    lines.append(f"  refused  {p.pass_name} on {f.node}: {f.message}")
        notes = self.before.meta.get("notes") or []
        lines += [f"  note     {n}" for n in notes]
        return "\n".join(lines)


def compile(builder: Any, *, sample: dict, effects: dict | None = None, name: str | None = None,  # noqa: A001
            passes: list[str] | None = None, cache_dir: str | Path | None = None) -> App:
    """Compile a LangGraph `StateGraph` (or its compiled app) and optimize it. See the module doc."""
    compiled = compile_langgraph(builder, sample, name=name or getattr(builder, "name", None) or "app", nodes=effects)
    after, results = run_pipeline(compiled.graph, Profile.empty(compiled.graph.id), passes)
    store = Store(Path(cache_dir) if cache_dir else Path(tempfile.mkdtemp(prefix="synapse0g-cache-")))
    return App(compiled, compiled.graph, after, results, Cache(store))
