"""The safety predicates. CLAUDE.md rule 3: effects are law.

These are the tests that stand between us and an agent re-sending an email or
re-placing an order. They are deliberately boring and deliberately exhaustive: the
permission table in docs/IR_SPEC.md section 4.1 is reproduced here row by row, because
the cost of a wrong cell is not a slow benchmark.
"""

from __future__ import annotations

import pytest

from synapse0g.graph.core import effects
from synapse0g.graph.core.sir import Effect, Graph

from .conftest import n

PERMISSIVE = [Effect.PURE, Effect.READ]
SIDE_EFFECTING = [Effect.EXTERNAL, Effect.IRREVERSIBLE]


def _node(effect: Effect, **kw):
    if effect == Effect.READ:
        kw.setdefault("staleness_s", 3600)
    if effect == Effect.WRITE:
        kw.setdefault("writes", ["cart"])
    kw.setdefault("target", "some_tool")
    return n("x", kind="tool", effect=effect, **kw)


# ------------------------------------------------------------------- the lattice


def test_the_lattice_is_ordered_most_permissive_first():
    assert effects.ORDER == (
        Effect.PURE, Effect.READ, Effect.WRITE, Effect.EXTERNAL, Effect.IRREVERSIBLE,
    )


def test_at_most_compares_by_restrictiveness():
    assert effects.at_most(Effect.PURE, Effect.READ)
    assert effects.at_most(Effect.READ, Effect.READ)
    assert not effects.at_most(Effect.WRITE, Effect.READ)
    assert not effects.at_most(Effect.IRREVERSIBLE, Effect.EXTERNAL)


def test_join_takes_the_least_permissive():
    assert effects.join(Effect.PURE, Effect.EXTERNAL) == Effect.EXTERNAL
    assert effects.join(Effect.READ, Effect.PURE) == Effect.READ


# --------------------------------------------------------------- reuse (cse, memoize)


@pytest.mark.parametrize("effect", SIDE_EFFECTING + [Effect.WRITE])
def test_nothing_above_read_is_ever_reused(effect: Effect):
    finding = effects.may_reuse(_node(effect))
    assert finding is not None
    assert finding.precondition == "effect<=read"


def test_a_pure_deterministic_node_may_be_reused():
    assert effects.may_reuse(_node(Effect.PURE)) is None


def test_a_read_node_without_staleness_may_not_be_reused():
    """There is no default staleness. "How stale may this observation be?" is the
    author's decision, and refusing to guess is what keeps us from serving yesterday's
    search results (docs/PASSES.md section 2)."""
    finding = effects.may_reuse(_node(Effect.READ, staleness_s=None))
    assert finding is not None and "staleness_s" in finding.precondition


def test_an_unseeded_stochastic_node_may_not_be_reused():
    """pure + stochastic is the common creative model call. It touches nothing, and
    collapsing two samples is still wrong: it changes the output distribution."""
    finding = effects.may_reuse(_node(Effect.PURE, determinism="stochastic"))
    assert finding is not None and finding.precondition == "determinism!=stochastic"


def test_a_seeded_node_may_be_reused():
    assert effects.may_reuse(_node(Effect.PURE, determinism="seeded")) is None


def test_the_irreversible_message_says_why_in_words_a_judge_understands():
    finding = effects.may_reuse(_node(Effect.IRREVERSIBLE, target="place_order"))
    assert finding is not None
    assert "place_order" in finding.message
    assert "never cached" in finding.message


# ------------------------------------------------------------ reorder / parallelize


@pytest.mark.parametrize("effect", SIDE_EFFECTING)
def test_side_effecting_nodes_are_never_reordered(effect: Effect):
    assert effects.may_reorder(_node(effect)) is not None


@pytest.mark.parametrize("effect", PERMISSIVE + [Effect.WRITE])
def test_everything_at_or_below_write_may_be_reordered(effect: Effect):
    assert effects.may_reorder(_node(effect)) is None


def test_two_writes_to_the_same_key_may_not_run_concurrently():
    a = n("a", effect=Effect.WRITE, writes=["cart"])
    b = n("b", effect=Effect.WRITE, writes=["cart"])
    finding = effects.may_run_concurrently(a, b)
    assert finding is not None and finding.precondition == "disjoint writes"
    assert "cart" in finding.message


def test_two_writes_to_different_keys_may_run_concurrently():
    a = n("a", effect=Effect.WRITE, writes=["cart"])
    b = n("b", effect=Effect.WRITE, writes=["notes"])
    assert effects.may_run_concurrently(a, b) is None


def test_a_read_of_a_key_another_node_writes_may_not_overlap_it():
    w = n("w", effect=Effect.WRITE, writes=["cart"])
    r = n("r", effect=Effect.READ, staleness_s=1, reads=["cart"])
    assert effects.may_run_concurrently(w, r) is not None
    assert effects.may_run_concurrently(r, w) is not None


def test_two_pure_nodes_may_always_run_concurrently():
    assert effects.may_run_concurrently(n("a"), n("b")) is None


# --------------------------------------------------- speculate / batch / eliminate


@pytest.mark.parametrize("effect", [Effect.WRITE] + SIDE_EFFECTING)
def test_only_pure_and_read_may_be_speculated_or_batched(effect: Effect):
    assert effects.may_speculate(_node(effect)) is not None
    assert effects.may_batch(_node(effect)) is not None


@pytest.mark.parametrize("effect", PERMISSIVE)
def test_pure_and_read_may_be_speculated_and_batched(effect: Effect):
    assert effects.may_speculate(_node(effect)) is None
    assert effects.may_batch(_node(effect)) is None


def test_an_unused_email_is_not_dead_code():
    """docs/PASSES.md section 7: a node that sends an email is never dead, however
    unused its return value is."""
    finding = effects.may_eliminate(_node(Effect.EXTERNAL, target="send_email"))
    assert finding is not None and "the point" in finding.message


def test_a_dead_pure_node_may_be_eliminated():
    assert effects.may_eliminate(_node(Effect.PURE)) is None


def test_a_write_node_may_be_eliminated_when_dead():
    """Internal state nobody reads afterwards is genuinely dead -- the mutation is not
    observable outside the workflow."""
    assert effects.may_eliminate(_node(Effect.WRITE)) is None


# ----------------------------------------------------- execution during the search


def test_irreversible_nodes_are_never_executed_during_search():
    finding = effects.may_execute_during_search(_node(Effect.IRREVERSIBLE, target="place_order"))
    assert finding is not None
    assert "regardless of what the caller asked for" in finding.message


def test_external_nodes_may_run_during_search_because_they_are_stubbed():
    """The permission table says `external` is "stubbed" in equivalence runs, not
    refused: the executor substitutes it. Only `irreversible` is an outright never."""
    assert effects.may_execute_during_search(_node(Effect.EXTERNAL)) is None


# ----------------------------------------- the universal invariant, checked on graphs


def test_check_preserved_accepts_an_honest_pass(shop_ops: Graph):
    """Dropping a dead *pure* node leaves the side-effect order untouched."""
    after = shop_ops.copy_with(nodes=list(shop_ops.nodes))
    assert effects.check_preserved(shop_ops, after) == []


def test_check_preserved_catches_a_removed_side_effect(shop_ops: Graph):
    after = shop_ops.rewrite_refs({"notify": "cart"}).without(["notify"])
    findings = effects.check_preserved(shop_ops, after)
    assert findings and findings[0].node == "notify"
    assert "never elided" in findings[0].message


def test_check_preserved_catches_a_reordered_side_effect(shop_ops: Graph):
    """Swapping `send_email` and `place_order` is caught here even if the pass that did
    it insisted it was safe. Defence in depth -- this runs independently of the pass."""
    nodes = list(shop_ops.nodes)
    i, j = shop_ops.index("notify"), shop_ops.index("order")
    nodes[i], nodes[j] = nodes[j], nodes[i]
    findings = effects.check_preserved(shop_ops, shop_ops.copy_with(nodes=nodes))
    assert findings and "keep their relative order" in findings[0].message


def test_check_preserved_catches_a_cached_side_effect(shop_ops: Graph):
    nodes = [node.model_copy(deep=True) for node in shop_ops.nodes]
    nodes[shop_ops.index("order")].meta = {"memoized": True}
    findings = effects.check_preserved(shop_ops, shop_ops.copy_with(nodes=nodes))
    assert findings and "cached or speculated" in findings[0].message


def test_side_effect_order_is_program_order(shop_ops: Graph):
    assert effects.side_effect_order(shop_ops) == ["notify", "order"]


def test_findings_can_be_tagged_with_the_pass_that_produced_them():
    finding = effects.may_reuse(_node(Effect.IRREVERSIBLE)).tagged("memoize")
    assert finding.pass_name == "memoize"


def test_a_finding_names_a_node_the_ui_can_highlight(shop_ops: Graph):
    finding = effects.may_reuse(shop_ops.node("order"))
    assert finding is not None and shop_ops.has(finding.node)
