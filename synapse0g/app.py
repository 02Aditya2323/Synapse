"""`synapse0g.compile`: an existing agent, compiled into a graph and optimized.

    import synapse0g
    app = synapse0g.compile(builder, sample={"rfq": rfq}, effects=EFFECTS)   # a LangGraph StateGraph
    state = app.invoke({"rfq": rfq})                                          # as before

    app = synapse0g.compile(handle_request, effects=EFFECTS)                 # a plain Python function
    result = app.invoke(request=..., documents=...)                          # as calling it

A plain function is read, not traced: its own statements become the graph, each one a call to a
function in the same file, and each function's effect is inferred from what its body calls (see
`graph.frontend.automap`), then overridden by `effects`. A list comprehension over a step,
`[extract(d) for d in documents]`, becomes a map region whose items may run side by side. A loop,
branch or computed value in the entry function is refused with its line number, never guessed.

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

import hashlib
import inspect
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
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
            allow_irreversible: bool = False, on_event=None) -> Execution:
        """One execution, measured. `optimized=False` runs the compiled graph unchanged, one step at a time.
        `on_event` receives the runtime's events as they happen (node_start, node_end, cache_hit, ...)."""
        graph = self.after if optimized else self.before
        execution = run_graph_sync(graph, task_input=dict(inputs), implementations=self.compiled.implementations,
                                   mode=mode, allow_irreversible=allow_irreversible,
                                   cache=self.cache if optimized else None, on_event=on_event)
        self.last = execution
        return execution

    def invoke(self, inputs: dict | None = None, *, allow_irreversible: bool = False, **kwargs):
        """What the app returns as written (`builder.compile().invoke(inputs)`, or calling the
        function), computed by the optimized graph."""
        execution = self.run({**(inputs or {}), **kwargs}, allow_irreversible=allow_irreversible)
        if execution.crashed:
            raise RunFailed(execution)
        return execution.outputs["result"]

    def explain(self) -> str:
        steps = [n for n in self.before.nodes if n.target not in ("workflow_input", "langgraph_state")]
        order = self.before.meta.get("declared_order") or [n.target for n in steps]
        lines = [f"{self.before.name}: {len(steps)} steps, as written: " + " -> ".join(order)]
        evidence = self.before.meta.get("evidence") or {}
        for node in steps:
            if node.target in evidence:
                deps = sorted({ref.node for ref in node.args.values() if hasattr(ref, "node")} - {"n_in"})
                names = [self.before.node(d).target for d in deps]
                region = " (once per item, a map)" if node.region else ""
                lines.append(f"  {node.target:<24} {node.effect:<13} waits for {', '.join(names) or 'only the input'}{region}"
                             f"  [{evidence[node.target][0] if evidence[node.target] else 'calls nothing outside the process'}]")
                continue
            if node.meta.get("framework") or node.meta.get("measured_reads") is not None:
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


def compile(target: Any, *, sample: dict | None = None, effects: dict | None = None, name: str | None = None,  # noqa: A001
            passes: list[str] | None = None, cache_dir: str | Path | None = None, cache_scope: str | None = None) -> App:
    """Compile a LangGraph `StateGraph` (or its compiled app), or a plain Python function, and
    optimize it. See the module doc.

    `cache_scope` says whose cache this is (a team, a customer): a cached step is only ever served
    to the same scope. The source of the agent is part of the scope too, so changing a prompt or
    a model in the code starts a fresh cache rather than serving answers from the old one."""
    if inspect.isfunction(target):
        compiled, graph, source = _compile_function(target, effects or {}, name)
    else:
        if sample is None:
            raise ValueError("a LangGraph app needs `sample`: one input to trace its steps' reads and writes")
        compiled = compile_langgraph(target, sample, name=name or getattr(target, "name", None) or "app", nodes=effects)
        graph = compiled.graph
        source = _source_of(compiled.implementations)
    after, results = run_pipeline(graph, Profile.empty(graph.id), passes)
    store = Store(Path(cache_dir) if cache_dir else Path(tempfile.mkdtemp(prefix="synapse0g-cache-")))
    scope = hashlib.sha256(f"{cache_scope or 'default'}\n{source}".encode()).hexdigest()[:16]
    return App(compiled, graph, after, results, Cache(store, scope=scope))


def _source_of(implementations: dict) -> str:
    texts = []
    for fn in implementations.values():
        try:
            texts.append(inspect.getsource(inspect.getmodule(fn)))
        except (OSError, TypeError):
            continue
    return "\n".join(sorted(set(texts)))


def _compile_function(fn: Any, effects: dict, name: str | None):
    from synapse0g.graph.frontend import automap

    path = Path(inspect.getsourcefile(fn) or "")
    funcs = automap.scan_python(path)
    entry = funcs.get(fn.__name__)
    if entry is None:
        raise ValueError(f"{fn.__name__} must be a top-level function in {path.name}")
    unknown = sorted(set(effects) - set(funcs))
    if unknown:
        raise ValueError(f"effects declared for functions not in {path.name}: {', '.join(unknown)}")
    for step, spec in effects.items():
        spec = {"effect": spec} if isinstance(spec, str) else dict(spec)
        funcs[step].declared = spec
        funcs[step].evidence.insert(0, f"declared {spec['effect']} by the developer")
    try:
        graph = automap.build_graph(entry, funcs)
    except automap.Unmappable as exc:
        raise ValueError(f"cannot compile {fn.__name__}: {exc}") from None
    order = [n.target for n in graph.nodes if n.target in funcs]  # the entry's statements, in the order written
    graph = graph.copy_with(name=name or fn.__name__,
                            meta={**graph.meta, "frontend": "python", "declared_order": order,
                                  "evidence": {s: funcs[s].evidence for s in order}})
    # the steps run as the developer's own functions, from the module already imported
    module = fn.__globals__
    implementations = {n.target: module[n.target] for n in graph.nodes if n.target in module and callable(module[n.target])}
    compiled = SimpleNamespace(implementations=implementations, graph=graph)
    return compiled, graph.validate_graph(), path.read_text(encoding="utf-8")
