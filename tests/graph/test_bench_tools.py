"""Workflow benchmark tools. Each one is called the way a workflow calls it (CLAUDE.md rule 12)."""

import copy

import pytest

from synapse0g.graph.bench.tools import shop
from synapse0g.graph.bench.tools.calculator import calculator
from synapse0g.graph.bench.tools.parsing import clip, field, fields
from synapse0g.graph.bench.tools.search import content_terms, search

PARAGRAPHS = [
    {"title": "Ed Wood", "text": "Edward Davis Wood Jr. was an American filmmaker."},
    {"title": "Scott Derrickson", "text": "Scott Derrickson is an American director of horror films."},
    {"title": "Woodwind", "text": "A family of musical instruments."},
]


def test_search_ranks_the_matching_paragraph_first():
    hits = search("Scott Derrickson director", PARAGRAPHS, k=2)
    assert hits[0]["title"] == "Scott Derrickson"
    assert all(set(h) == {"id", "title", "text", "score"} for h in hits)


def test_search_exclude_brings_new_paragraphs():
    assert "Scott Derrickson" not in [h["title"] for h in search("American", PARAGRAPHS, k=3, exclude=[1])]


def test_search_returns_nothing_for_a_query_of_stopwords_or_no_overlap():
    assert search("who is the", PARAGRAPHS) == []
    assert search("quantum chromodynamics", PARAGRAPHS) == []


def test_content_terms_drop_stopwords_and_repeats():
    assert content_terms("Who is the director of the the Director?") == ["director"]


@pytest.mark.parametrize("expression, value", [
    ("(48 / 2) * 3", 72), ("$1,200 x 3 = 3600", 3600), ("20% * 50", 10), ("7 / 2", 3.5), ("2 ^ 3", 8),
])
def test_calculator(expression, value):
    assert calculator(expression) == value


@pytest.mark.parametrize("expression", ["__import__('os').system('x')", "9**9**9", "", "two plus two"])
def test_calculator_refuses_what_is_not_arithmetic(expression):
    with pytest.raises(ValueError):
        calculator(expression)


def test_field_reads_labelled_lines_through_markdown():
    assert field("**ANSWER:** Paris", "ANSWER") == "Paris"
    assert field("chatter\nANSWER: one\nANSWER: two", "ANSWER") == "two"
    assert field("ANSWER: one\nANSWER: two", "ANSWER", first=True) == "one"
    assert field("nothing here", "ANSWER") is None


def test_fields_reads_every_labelled_line():
    assert fields("1. SEARCH: a\n- SEARCH: b\nSEARCH:\nother\n**SEARCH:** c", "SEARCH") == ["a", "b", "c"]


def test_clip():
    assert clip("a  b", 10) == "a b"
    assert clip("x" * 20, 10) == "xxxxxxx..."


@pytest.fixture
def db():
    return {"orders": {"O1": {"customer": "Asha", "item": "mug", "qty": 2, "status": "placed", "paid": 16},
                       "O2": {"customer": "Chen", "item": "mug", "qty": 1, "status": "delivered", "paid": 8}},
            "stock": {"mug": 5}, "prices": {"mug": 8}, "refunds": []}


def test_tools_never_mutate_their_input(db):
    before = copy.deepcopy(db)
    shop.cancel_order(db, "O1")
    shop.refund_order(db, "O2", 8)
    shop.update_quantity(db, "O1", 3)
    assert db == before


def test_cancel_returns_stock_and_refuses_a_second_time(db):
    after = shop.cancel_order(db, "O1")["db"]
    assert after["stock"]["mug"] == 7 and after["orders"]["O1"]["status"] == "cancelled"
    assert shop.cancel_order(after, "O1")["ok"] is False


def test_refund_is_capped_at_what_is_left(db):
    assert shop.refund_order(db, "O2", 9)["ok"] is False
    part = shop.refund_order(db, "O2", 3)["db"]
    assert part["orders"]["O2"]["status"] == "partially_refunded"
    assert shop.refund_order(part, "O2", 6)["ok"] is False
    assert shop.refund_order(part, "O2", 5)["db"]["orders"]["O2"]["status"] == "refunded"


def test_update_quantity_moves_stock_and_paid(db):
    after = shop.update_quantity(db, "O1", 4)["db"]
    assert after["stock"]["mug"] == 3 and after["orders"]["O1"]["paid"] == 32
    assert shop.update_quantity(db, "O1", 100)["ok"] is False


def test_call_tool_refuses_a_made_up_tool_or_argument(db):
    assert shop.call_tool("drop_tables", db)["ok"] is False
    assert shop.call_tool("get_order", db, nonsense=1)["ok"] is False
    assert shop.call_tool("get_order", db, order_id="O1")["order"]["customer"] == "Asha"
