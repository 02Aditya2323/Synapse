"""The supplier-check agent under test: three ways to run the same app on the same tasks.

- `as_written`: the developer's LangGraph app (`examples/supplier_agent.py`), run by LangGraph
  itself, steps one after another as written. Its order goes to the test marketplace.
- `compiled`: the same app after `synapse0g.compile`: the graph passes' result, run by the
  synapse0g runtime. Its irreversible step is skipped, as in every test run, and the order it
  would have placed is what gets checked.
- `hand_parallel`: the same node functions, wired by hand with LangGraph's own fan-out, the way a
  careful developer could write it. The framework-native comparison: what LangGraph alone gets
  you when someone already knows which steps are independent.

All three call the model through `synapse0g.openai.OpenAI`, so each call lands in its task's own
session and gets that session's per-step policy.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import time
from functools import cache

from synapse0g.agents import supplier_desk as desk
from synapse0g.agents.langgraph_agents import AgentResult
from synapse0g.openai import routed
from synapse0g.registry import ROOT
from synapse0g.tasks import Task, split_of

APP_PATH = ROOT / "examples" / "supplier_agent.py"


@cache
@cache
def example():
    spec = importlib.util.spec_from_file_location("supplier_agent", APP_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules["supplier_agent"] = module  # its steps can be found by module name: a compiled app can be saved
    spec.loader.exec_module(module)
    return module


def load_supplier() -> list[Task]:
    return [Task(f"supplier-{rid}", "supplier", json.dumps(desk.request(rid)), desk.right_answer(desk._RFQS[rid]) or "none",
                 (rid,)) for rid in desk.rfq_ids()]


def split(split_name: str, n: int) -> list[Task]:
    return [t for t in load_supplier() if split_of(t.id) == split_name][:n]


@cache
def _as_written_app():
    return example().builder.compile()


@cache
def _hand_parallel_app():
    from langgraph.graph import END, START, StateGraph

    ex = example()
    g = StateGraph(ex.State)
    for f in (ex.load_policy, ex.get_quotes, ex.screen_reviews, ex.check_certificates, ex.decide, ex.place_order):
        g.add_node(f.__name__, f)
    lookups = ["load_policy", "get_quotes", "screen_reviews", "check_certificates"]
    for name in lookups:
        g.add_edge(START, name)
    g.add_edge(lookups, "decide")
    g.add_edge("decide", "place_order")
    g.add_edge("place_order", END)
    return g.compile()


def _result(state: dict | None, error: str | None, t0: float, ran_order: bool) -> AgentResult:
    wall = time.perf_counter() - t0
    if error:
        return AgentResult("", error, wall, 0, {})
    order = state.get("order") or {}
    placed = 1 if order.get("supplier_id") else 0
    return AgentResult(json.dumps(order), None, wall, 0,
                       {"protected_actions": placed, "order_executed": bool(placed and ran_order)})


def _langgraph_agent(app_factory):
    def run(task: Task, base_url: str, token: str, model: str) -> AgentResult:
        t0 = time.perf_counter()
        try:
            with routed(base_url, token):
                state = app_factory().invoke({"rfq": json.loads(task.question)})
            return _result(state, None, t0, ran_order=True)
        except Exception as e:  # a crash is recorded as a crashed task, never scored as a wrong answer
            return _result(None, f"{type(e).__name__}: {e}"[:500], t0, False)

    return run


as_written = _langgraph_agent(_as_written_app)
hand_parallel = _langgraph_agent(_hand_parallel_app)


def compiled_agent(app):
    """The synapse0g-compiled app as an agent. Runs in BENCH mode: the order is never placed."""
    from synapse0g.graph.runtime.executor import ExecutionMode

    def run(task: Task, base_url: str, token: str, model: str) -> AgentResult:
        t0 = time.perf_counter()
        try:
            with routed(base_url, token):
                execution = app.run({"rfq": json.loads(task.question)}, mode=ExecutionMode.BENCH)
            if execution.crashed:
                return _result(None, execution.error or "crashed", t0, False)
            res = _result(execution.outputs["result"], None, t0, ran_order=False)
            res.extra["cache_hits"] = execution.cache_hits
            return res
        except Exception as e:  # recorded, never swallowed
            return _result(None, f"{type(e).__name__}: {e}"[:500], t0, False)

    return run
