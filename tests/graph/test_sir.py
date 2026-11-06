"""The IR contract: derived edges and validation (docs/IR_SPEC.md sections 3 and 7).

The distinction these tests protect is the entire point of the product. A data edge
constrains two nodes and can be broken by parallelism; an effect edge can never be
broken. If `effect_edges()` is wrong, `parallelize` is unsafe and no amount of
downstream checking makes it safe again.
"""

from __future__ import annotations

import pytest

from synapse0g.graph.core.sir import SIR_VERSION, Effect, Graph, Literal_, Node, Ref, Region, SIRError

from .conftest import n


def test_version_is_pinned():
    """A bump here is a deliberate act with a migration note, regenerated fixtures and
    a message to the team. It is not something that drifts."""
    assert SIR_VERSION == 1


def test_node_effect_defaults_to_external():
    """The restrictive default (docs/IR_SPEC.md section 4.3). A frontend that cannot
    determine a tool's effect produces a node nothing will optimize -- slow and correct.
    The opposite default is fast and occasionally catastrophic."""
    assert Node(id="x", kind="tool", target="unknown_tool").effect == Effect.EXTERNAL


# ----------------------------------------------------------------- derived edges


def test_data_edges_come_from_refs(fanout: Graph):
    edges = {(e.src, e.dst) for e in fanout.data_edges()}
    assert ("n0", "n1") in edges and ("n1", "n2") in edges and ("n2", "n3") in edges


def test_the_fanout_shape_has_no_effect_edges(fanout: Graph):
    """The claim that makes the headline win provable: n1 and n2 are map regions with
    nothing ordering their instances. The absence of an effect edge is a fact about the
    graph, not an assumption about the workload."""
    assert fanout.effect_edges() == []


def test_write_after_read_on_the_same_key_gets_an_effect_edge():
    g = Graph(
        id="g", name="state",
        nodes=[
            n("r", effect=Effect.READ, staleness_s=1, reads=["cart"], outputs=["value"]),
            n("w", effect=Effect.WRITE, writes=["cart"], outputs=["value"]),
        ],
        outputs={"out": Ref(node="w", output="value")},
    )
    edges = g.effect_edges()
    assert len(edges) == 1
    assert (edges[0].src, edges[0].dst) == ("r", "w")
    assert "write-after-read" in edges[0].reason and "cart" in edges[0].reason


def test_disjoint_writes_get_no_effect_edge():
    g = Graph(
        id="g", name="state",
        nodes=[
            n("a", effect=Effect.WRITE, writes=["cart"], outputs=["value"]),
            n("b", effect=Effect.WRITE, writes=["notes"], outputs=["value"]),
        ],
        outputs={"out": Ref(node="b", output="value")},
    )
    assert g.effect_edges() == []


def test_side_effecting_nodes_are_ordered_against_each_other(shop_ops: Graph):
    """`send_email` and `place_order` share no data and no state key, yet their order is
    fixed: observable side effects keep program order, always."""
    pairs = {(e.src, e.dst) for e in shop_ops.effect_edges()}
    assert ("notify", "order") in pairs


def test_effect_edges_make_a_side_effecting_node_depend_on_its_predecessor(shop_ops: Graph):
    assert "notify" in shop_ops.predecessors("order")
    assert "notify" not in shop_ops.predecessors("order", effects=False)


def test_topo_order_respects_effect_edges(shop_ops: Graph):
    order = shop_ops.topo_order()
    assert order.index("notify") < order.index("order")


def test_a_cycle_is_reported_not_returned():
    g = Graph(
        id="g", name="cyclic",
        nodes=[
            n("a", args={"b": Ref(node="b", output="value")}, outputs=["value"]),
            n("b", args={"a": Ref(node="a", output="value")}, outputs=["value"]),
        ],
    )
    with pytest.raises(SIRError, match="cycle"):
        g.topo_order()


# ------------------------------------------------------------------- reachability


def test_reachable_from_outputs_finds_the_live_cone(dead_code: Graph):
    live = dead_code.reachable_from_outputs()
    assert live == {"a", "used"}
    assert "dead" not in live and "logged" not in live


# -------------------------------------------------------------------- validation


def test_a_ref_to_an_unknown_node_is_an_error():
    g = Graph(id="g", name="w", nodes=[n("a", args={"x": Ref(node="ghost", output="value")})])
    with pytest.raises(SIRError, match="unknown node 'ghost'"):
        g.validate_graph()


def test_a_ref_to_an_undeclared_output_is_an_error():
    g = Graph(
        id="g", name="w",
        nodes=[n("a", outputs=["value"]), n("b", args={"x": Ref(node="a", output="nope")})],
    )
    with pytest.raises(SIRError, match="wants output 'nope'"):
        g.validate_graph()


def test_duplicate_node_ids_are_an_error():
    g = Graph(id="g", name="w", nodes=[n("a"), n("a")])
    with pytest.raises(SIRError, match="duplicate node id"):
        g.validate_graph()


def test_a_write_node_must_say_which_keys_it_writes():
    g = Graph(id="g", name="w", nodes=[n("a", effect=Effect.WRITE)])
    with pytest.raises(SIRError, match="declares no writes"):
        g.validate_graph()


def test_a_pure_node_may_not_declare_reads_or_writes():
    g = Graph(id="g", name="w", nodes=[n("a", effect=Effect.PURE, reads=["cart"])])
    with pytest.raises(SIRError, match="is 'pure' but declares"):
        g.validate_graph()


def test_staleness_belongs_only_to_read_nodes():
    g = Graph(id="g", name="w", nodes=[n("a", effect=Effect.PURE, staleness_s=60)])
    with pytest.raises(SIRError, match="staleness_s"):
        g.validate_graph()


def test_cache_key_fields_must_name_real_arguments():
    g = Graph(id="g", name="w", nodes=[n("a", args={"x": Literal_(value=1)}, cache_key_fields=["y"])])
    with pytest.raises(SIRError, match="non-arguments"):
        g.validate_graph()


def test_a_node_in_two_regions_is_an_error():
    g = Graph(
        id="g", name="w",
        nodes=[n("a", region="r1")],
        regions=[Region(id="r1", kind="map", body=["a"]), Region(id="r2", kind="map", body=["a"])],
    )
    with pytest.raises(SIRError, match="is in both region"):
        g.validate_graph()


def test_a_loop_region_without_a_condition_is_an_error():
    g = Graph(id="g", name="w", nodes=[n("a")], regions=[Region(id="r1", kind="loop", body=["a"])])
    with pytest.raises(SIRError, match="no continuation condition"):
        g.validate_graph()


def test_an_unresolvable_output_is_an_error():
    g = Graph(id="g", name="w", nodes=[n("a")], outputs={"out": Ref(node="ghost", output="value")})
    with pytest.raises(SIRError, match="output 'out' refers to unknown node"):
        g.validate_graph()


def test_a_future_sir_version_is_refused():
    g = Graph(id="g", name="w", sir_version=SIR_VERSION + 1, nodes=[n("a")])
    with pytest.raises(SIRError, match="SIR_VERSION"):
        g.validate_graph()


def test_validation_reports_every_problem_at_once():
    """A pass author fixing one error at a time is a pass author who re-runs the suite
    six times."""
    g = Graph(id="g", name="w", nodes=[n("a", effect=Effect.WRITE), n("a")])
    with pytest.raises(SIRError) as exc:
        g.validate_graph()
    assert "duplicate node id" in str(exc.value) and "declares no writes" in str(exc.value)


# ----------------------------------------------------------------- transformation


def test_rewrite_refs_redirects_args_outputs_and_regions(duplicate_tool_calls: Graph):
    g = duplicate_tool_calls.rewrite_refs({"p2": "p1"}).without(["p2"]).validate_graph()
    assert not g.has("p2")
    assert g.node("size").args["price"] == Ref(node="p1", output="price")


def test_copy_with_does_not_mutate_the_original(fanout_with_input: Graph):
    before = len(fanout_with_input.nodes)
    fanout_with_input.without(["n3"])
    assert len(fanout_with_input.nodes) == before


def test_to_ui_carries_the_derived_edges(fanout_with_input: Graph):
    body = fanout_with_input.to_ui()
    assert body["edges"]["data"][0]["from"] == "n0"
    assert body["edges"]["effect"] == []
    assert body["n_nodes"] == 4


def test_summary_is_the_list_row(fanout_with_input: Graph):
    row = fanout_with_input.summary()
    assert set(row) == {"id", "name", "sir_version", "n_nodes", "derived_from", "pipeline", "created_at"}
