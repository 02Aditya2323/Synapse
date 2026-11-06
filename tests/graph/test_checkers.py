"""The checkers are the default equivalence relation, so a loose one approves regressions."""

import pytest

from synapse0g.graph.bench.checkers import check, f1_score, parse_number


@pytest.mark.parametrize("answer, passed", [
    ("Chief of Protocol", True),
    ("the Chief of Protocol.", True),
    ("Chief", False),                          # F1 0.5 is under the 0.6 bar
    ("Ambassador to Ghana", False),
    ("", False),
    (None, False),
])
def test_qa_f1(answer, passed):
    assert check("qa_f1", answer, {"answer": "Chief of Protocol"}).success is passed


@pytest.mark.parametrize("answer, passed", [
    ("yes", True), ("Yes, they are.", True), ("no", False), ("not sure, yes", False), ("", False),
])
def test_yes_no_reads_the_first_word_only(answer, passed):
    assert check("qa_f1", answer, {"answer": "yes"}).success is passed


def test_f1_ignores_case_articles_and_punctuation():
    assert f1_score("The Eiffel Tower!", "eiffel tower") == 1.0


@pytest.mark.parametrize("text, number", [
    ("So she has $1,250.00 left.", 1250.0), ("18", 18.0), ("first 3 then 4.", 4.0), ("-7", -7.0), ("none", None),
])
def test_parse_number_takes_the_last_number(text, number):
    assert parse_number(text) == number


def test_math_is_exact():
    assert check("math_exact", "The answer is 18 dollars", "18").success
    assert not check("math_exact", "The answer is 19", "18").success
    assert not check("math_exact", "I do not know", "18").success


def test_ops_compares_the_final_database():
    state = {"orders": {"O1": {"status": "cancelled"}}, "stock": {"mug": 3}, "refunds": []}
    assert check("ops_state", {"reply": "done", "db": {**state, "prices": {"mug": 8}}}, state).success
    wrong = {**state, "refunds": [{"order_id": "O1", "amount": 1}]}
    assert not check("ops_state", {"reply": "done", "db": wrong}, state).success
    assert not check("ops_state", "done", state).success


def test_unknown_checker_is_loud():
    with pytest.raises(KeyError, match="no checker"):
        check("vibes", "a", "b")
