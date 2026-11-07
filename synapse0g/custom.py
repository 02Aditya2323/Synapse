"""A customer's own agent: pasted or uploaded code, compiled, optimized, run and returned as code.

The code is saved as `customer_agent.py` in its job's folder and imported. Its model client is pointed
at synapse0g: a module-level `openai.OpenAI` class or client is swapped for synapse0g's drop-in client,
so every call goes through the proxy and is paid from the optimizer's own 0G account. Model names may
be models.yaml ids ("zg-main") or 0G tags ("qwen3.8-flash").

What gets optimized is found in the code: a LangGraph `StateGraph` (any module-level builder, or a
compiled app's builder), else a plain function named by `entry`. Effects come from the module's own
`EFFECTS` table, as the SDK asks; steps it does not list get the effect read from their code.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

MODULE = "customer_agent"


def save(code: str, folder: Path) -> Path:
    if not code or not code.strip():
        raise ValueError("paste or upload your agent's code")
    if len(code) > 200_000:
        raise ValueError("the code is over 200,000 characters")
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{MODULE}.py"
    path.write_text(code, encoding="utf-8")
    return path


def load(path: Path):
    """Import the customer's module with its model client pointed at synapse0g."""
    import openai as _openai

    from synapse0g import openai as ours

    os.environ.setdefault("OPENAI_API_KEY", "synapse0g")  # their client may insist on a key at import; ours ignores it
    for name in [m for m in sys.modules if m == MODULE]:
        del sys.modules[name]
    sys.path.insert(0, str(path.parent))
    try:
        spec = importlib.util.spec_from_file_location(MODULE, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[MODULE] = module
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(path.parent))
    for k, v in list(vars(module).items()):
        if v is _openai.OpenAI:
            setattr(module, k, ours.OpenAI)
        elif isinstance(v, _openai.OpenAI):
            setattr(module, k, ours.OpenAI())
        else:
            _route_langchain(v)
    return module


class _Routed:
    """Stands in for a client: every attribute is read from the client of the run this call is in."""

    def __init__(self, get):
        self._get = get

    def __getattr__(self, name):
        return getattr(self._get(), name)


def _route_langchain(v) -> None:
    """A module-level LangChain ChatOpenAI keeps its own HTTP client: point it at synapse0g's routed client,
    so its calls are traced, optimized and paid like any other."""
    try:
        from langchain_openai import ChatOpenAI
    except ImportError:
        return
    if not isinstance(v, ChatOpenAI):
        return
    from synapse0g import openai as ours

    shim = ours.OpenAI()
    v.__dict__["root_client"] = _Routed(lambda: shim._client())
    v.__dict__["client"] = _Routed(lambda: shim._client().chat.completions)


def find(module, entry: str | None = None) -> dict:
    """The thing to optimize: a LangGraph builder, or a plain function."""
    from langgraph.graph import StateGraph

    if entry and callable(getattr(module, entry, None)) and not isinstance(getattr(module, entry), StateGraph):
        return {"kind": "function", "name": entry, "target": getattr(module, entry)}
    graphs = [(k, v) for k, v in vars(module).items() if isinstance(v, StateGraph)]
    graphs += [(k, v.builder) for k, v in vars(module).items() if isinstance(getattr(v, "builder", None), StateGraph)]
    if entry:
        graphs = [g for g in graphs if g[0] == entry] or graphs
    if graphs:
        name, builder = sorted(graphs, key=lambda g: g[0] != "builder")[0]
        return {"kind": "langgraph", "name": name, "target": builder}
    raise ValueError("no LangGraph StateGraph found in the code: define one at module level (for example `builder = build()`), "
                     "or name the function to optimize as the entry")


def effects_of(module, path: Path, node_names: list[str]) -> tuple[dict, dict]:
    """The module's EFFECTS, and for steps it leaves out, the effect read from each step's code."""
    from synapse0g.graph.frontend import automap

    declared = dict(getattr(module, "EFFECTS", {}) or {})
    inferred = {}
    funcs = automap.scan_python(path)
    for n in node_names:
        if n not in declared and n in funcs:
            inferred[n] = funcs[n].effect
    return {**inferred, **declared}, {n: "declared in EFFECTS" if n in declared else "read from its code" for n in node_names}


def step_count(module, path: Path, entry: str | None = None) -> int:
    t = find(module, entry)
    if t["kind"] == "langgraph" and getattr(t["target"], "branches", None):
        # checked before any payment: a graph that picks its next step at run time cannot be compiled yet
        routers = ", ".join(sorted(t["target"].branches))
        raise ValueError(f"your graph chooses its next step at run time (conditional edges from {routers}); synapse0g compiles "
                         "graphs with fixed edges for now, so this agent cannot be optimized yet. Nothing was charged.")
    if t["kind"] == "langgraph":
        return len(t["target"].nodes)
    from synapse0g.graph.frontend import automap

    return max(len(automap.scan(path, t["name"]).functions), 1)
