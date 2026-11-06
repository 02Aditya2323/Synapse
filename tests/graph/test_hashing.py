"""Canonical hashing for stable workflow and node identities.

What these tests are really protecting: if the same logical call stops hashing
identically, CSE, memoization and the cross-run cache all silently stop working. The
failure is a missing optimization, not a crash, so nothing shouts.
"""

from __future__ import annotations

from synapse0g.graph.core.hashing import canonical, fingerprint, node_hash, state_hash
from synapse0g.graph.core.sir import Effect, Literal_, Node, Ref


def test_canonical_is_order_independent_for_dicts():
    assert canonical({"a": 1, "b": 2}) == canonical({"b": 2, "a": 1})


def test_canonical_keeps_unicode_rather_than_escaping_it():
    assert "ü" in canonical({"name": "Müller"})


def test_canonical_is_stable_for_sets_and_bytes():
    assert canonical({1, 2, 3}) == canonical({3, 2, 1})
    assert canonical(b"\x00\xff") == '"00ff"'


def test_canonical_handles_pydantic_models():
    assert canonical(Ref(node="n1", output="docs")) == '{"node":"n1","output":"docs"}'


def test_fingerprint_is_sha256_hex():
    digest = fingerprint({"a": 1})
    assert len(digest) == 64 and int(digest, 16) >= 0


def test_fingerprint_differs_on_different_content():
    assert fingerprint({"a": 1}) != fingerprint({"a": 2})


def _price_node(node_id: str = "p1", **kw) -> Node:
    return Node(
        id=node_id,
        kind="tool",
        target="get_price",
        effect=Effect.READ,
        staleness_s=60,
        args={"symbol": Literal_(value="INFY"), "request_id": Literal_(value="req-1")},
        outputs=["price"],
        **kw,
    )


def test_node_hash_ignores_node_id_so_duplicates_collapse():
    """The whole point: two different nodes doing the same work hash the same. This is
    the `get_price("INFY")` twice case from the recorded corpus."""
    resolved = {"symbol": "INFY", "request_id": "req-1"}
    assert node_hash(_price_node("p1"), resolved) == node_hash(_price_node("p2"), resolved)


def test_node_hash_changes_with_the_resolved_value_not_the_ref():
    node = Node(
        id="n1", kind="tool", target="get_price", effect=Effect.READ, staleness_s=60,
        args={"symbol": Ref(node="n0", output="sym")}, outputs=["price"],
    )
    assert node_hash(node, {"symbol": "INFY"}) != node_hash(node, {"symbol": "TCS"})


def test_node_hash_separates_targets_kinds_and_effects():
    resolved = {"symbol": "INFY", "request_id": "req-1"}
    base = node_hash(_price_node(), resolved)
    other_target = _price_node().model_copy(update={"target": "get_quote"})
    other_effect = _price_node().model_copy(update={"effect": Effect.PURE, "staleness_s": None})
    assert node_hash(other_target, resolved) != base
    assert node_hash(other_effect, resolved) != base


def test_cache_key_fields_narrows_the_key():
    """`request_id` must not enter the key, or two identical price lookups never
    collapse. Narrowing is explicit precisely because getting it wrong serves one
    call's answer to a different call."""
    node = _price_node().model_copy(update={"cache_key_fields": ["symbol"]})
    a = node_hash(node, {"symbol": "INFY", "request_id": "req-1"})
    b = node_hash(node, {"symbol": "INFY", "request_id": "req-2"})
    assert a == b
    assert a != node_hash(node, {"symbol": "TCS", "request_id": "req-1"})


def test_node_hash_says_which_field_is_missing_rather_than_hashing_a_hole():
    node = _price_node()
    try:
        node_hash(node, {"symbol": "INFY"})
    except KeyError as exc:
        assert "request_id" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("a missing cache-key field must raise, never hash silently")


def test_state_hash_detects_a_fixpoint():
    """`loop_cutoff` cuts when two successive iterations hash the same."""
    assert state_hash({"notes": ["a"]}) == state_hash({"notes": ["a"]})
    assert state_hash({"notes": ["a"]}) != state_hash({"notes": ["a", "b"]})
