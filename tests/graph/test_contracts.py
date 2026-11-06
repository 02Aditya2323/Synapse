import pytest

from synapse0g.graph.core.sir import Graph, Literal_, Node, Ref
from synapse0g.graph.correctness.splits import split_for_task


def dependencies(g: Graph) -> dict:
    """Local helper: returns {node_id: set_of_predecessor_ids} for the whole graph."""
    return {n.id: g.predecessors(n.id) for n in g.nodes}


def node(id, **kwargs):
    return Node(id=id, kind="transform", target="identity", effect=kwargs.pop("effect", "pure"), **kwargs)


def test_graph_checks_references_and_combined_cycles():
    with pytest.raises(ValueError, match="unresolved|unknown node"):
        Graph(id="g", name="g", nodes=[node("a", args={"x": Ref(node="missing")})]).validate_graph()
    with pytest.raises(ValueError):
        Graph(id="g", name="g", nodes=[node("a", args={"x": Ref(node="b")}), node("b", args={"x": Ref(node="a")})]).validate_graph()
    # Aryan's Graph allows forward data refs (topo order resolves them); validate only unresolved refs and cycles
    g = Graph(id="g", name="g", nodes=[node("a", effect="external", args={"x": Ref(node="b")}), node("b")])
    g.validate_graph()  # Should NOT raise: b satisfies a's data dep


def test_literal_objects_are_not_confused_with_refs():
    g = Graph(id="g", name="g", nodes=[node("a", args={"x": Literal_(value={"node": "not-a-ref"})})])
    assert dependencies(g) == {"a": set()}
    assert Graph.model_validate_json(g.model_dump_json()) == g


def test_protected_calls_barrier_reads_and_writes():
    # Two external/irreversible nodes must keep program order via effect edges.
    g = Graph(id="g", name="g", nodes=[node("a", effect="external"), node("pay", effect="irreversible"), node("b", effect="external")])
    deps = dependencies(g)
    assert "a" in deps["pay"] and "pay" in deps["b"]
    # write-after-read: a write barriers a preceding read on the same key
    g2 = Graph(id="g2", name="g2", nodes=[
        node("r", effect="read", reads=["cart"]),
        node("w", effect="write", writes=["cart"]),
    ])
    d2 = dependencies(g2)
    assert "r" in d2["w"]


def test_split_is_stable_and_matches_ported_hash_buckets():
    import hashlib

    for i in range(100):
        task = f"task-{i}"
        bucket = int(hashlib.sha1(task.encode()).hexdigest()[:8], 16) % 100
        assert split_for_task(task) == ("train" if bucket < 60 else "calib" if bucket < 80 else "test")
