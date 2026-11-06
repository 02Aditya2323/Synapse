"""The LangGraph adapter: edges come from measured state reads and writes, undeclared nodes are never
run while tracing, and the compiled graph gives LangGraph's own answer."""

from __future__ import annotations

import operator
from typing import Annotated, TypedDict

import pytest

pytest.importorskip("langgraph")

from langgraph.graph import END, START, StateGraph  # noqa: E402

from synapse0g.graph.core.sir import Effect, Profile  # noqa: E402
from synapse0g.graph.frontend.adapters.langgraph import LangGraphCompileError, compile_langgraph  # noqa: E402
from synapse0g.graph.passes.base import run_pipeline  # noqa: E402
from synapse0g.graph.runtime.executor import ExecutionMode  # noqa: E402
from tests.graph import langgraph_example as ex  # noqa: E402

SAMPLE = {"topic": "bees"}


@pytest.fixture(autouse=True)
def _fake():
    ex.install_fake_model(latency_s=0.1)


def _deps(graph, node_id):
    return sorted({ref.node for ref in graph.node(node_id).args.values() if hasattr(ref, "node")})


def test_a_habitual_chain_compiles_to_its_real_data_flow():
    graph = compile_langgraph(ex.graph(), SAMPLE, nodes=ex.NODES).graph
    assert _deps(graph, "risks") == ["n_in"] and _deps(graph, "history") == ["n_in"]
    assert _deps(graph, "write") == ["facts", "history", "n_in", "risks"]
    assert graph.node("risks").meta["measured_reads"] == ["topic"]
    assert graph.node("risks").meta["measured_writes"] == ["risks"]


def test_a_node_that_prints_the_whole_state_keeps_all_of_it():
    """`f"{state}"` reads every key. If the trace missed it, the node would get an empty state at run
    time, and the firewall could not see it: both compiled graphs would lose the same input."""

    class State(TypedDict, total=False):
        topic: str
        facts: str
        summary: str

    def facts(state):
        return {"facts": f"facts about {state['topic']}"}

    def summary(state):
        return {"summary": f"summary of {state}"}

    builder = StateGraph(State)
    builder.add_node("facts", facts)
    builder.add_node("summary", summary)
    builder.add_edge(START, "facts")
    builder.add_edge("facts", "summary")
    builder.add_edge("summary", END)
    program = compile_langgraph(builder, SAMPLE, nodes={"facts": "pure", "summary": "pure"})
    assert _deps(program.graph, "summary") == ["facts", "n_in"]
    run = program.run(SAMPLE)
    assert run.status == "done", run.error
    assert run.outputs["result"] == program.baseline_measured(SAMPLE)["output"]


def test_the_optimized_graph_matches_invoke_and_overlaps_independent_nodes():
    program = compile_langgraph(ex.graph(), SAMPLE, nodes=ex.NODES)
    baseline = program.baseline_measured(SAMPLE)
    optimized, results = run_pipeline(program.graph, Profile.empty(program.graph.id))
    assert "parallelize" in [r.pass_name for r in results if r.applied]
    run = program.run(SAMPLE, graph=optimized)
    assert run.status == "done", run.error
    assert run.outputs["result"] == baseline["output"]
    assert run.provider_calls == baseline["provider_calls"] == 4
    spans = {r.node_id: (r.started_ms, r.started_ms + r.latency_ms) for r in run.node_runs}
    firsts = [spans[n] for n in ("facts", "risks", "history")]
    assert max(s for s, _ in firsts) < min(e for _, e in firsts), "independent nodes did not overlap"


def test_an_undeclared_node_is_external_never_traced_and_pins_its_place():
    calls = []
    builder = ex.publishing_graph()
    original = builder.nodes["publish"].runnable

    builder.nodes["publish"].runnable.func = lambda state: calls.append(1) or {"published": "x"}
    undeclared = {k: v for k, v in ex.NODES.items() if k != "publish"}
    program = compile_langgraph(builder, SAMPLE, nodes=undeclared)
    assert calls == [], "a node with no declared effect ran while tracing"
    node = program.graph.node("publish")
    assert node.effect == Effect.EXTERNAL and set(node.meta["measured_reads"]) >= {"article", "topic"}
    assert any("publish" in note for note in program.notes)
    run = program.run(SAMPLE, mode=ExecutionMode.SEARCH)
    assert run.status == "done" and calls == [] and next(r for r in run.node_runs if r.node_id == "publish").stubbed
    del original


def test_unsupported_shapes_are_refused_plainly():
    class S(TypedDict, total=False):
        topic: str
        log: Annotated[list, operator.add]

    g = StateGraph(S)
    g.add_node("a", lambda s: {"log": ["a"]})
    g.add_edge(START, "a")
    g.add_edge("a", END)
    with pytest.raises(LangGraphCompileError, match="reducer"):
        compile_langgraph(g, SAMPLE, nodes={"a": "pure"})

    g = ex.graph()
    g.add_conditional_edges("write", lambda s: END)
    with pytest.raises(LangGraphCompileError, match="conditional"):
        compile_langgraph(g, SAMPLE, nodes=ex.NODES)


def test_a_read_node_needs_staleness():
    with pytest.raises(LangGraphCompileError, match="staleness_s"):
        compile_langgraph(ex.graph(), SAMPLE, nodes={**ex.NODES, "facts": "read"})



def test_a_declared_external_node_marks_only_what_it_updates():
    program = compile_langgraph(ex.publishing_graph(), SAMPLE, nodes=ex.NODES)
    assert program.graph.node("publish").outputs == ["published"]
    run = program.run(SAMPLE, mode=ExecutionMode.SEARCH)
    state = run.outputs["result"]
    assert state["published"]["__stubbed__"] is True and "<facts:" in state["article"]
