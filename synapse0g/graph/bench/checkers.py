"""Pass/fail checkers. Plain code: no model decides what counts as a failure.

    qa_f1       SQuAD-normalised word-overlap F1 >= 0.6 against the gold answer
    math_exact  the final number equals the gold number exactly
    ops_state   the database the agent left equals the correct one
    review_file the finding names the file with the planted bug, and no other file

`checker` is the default equivalence relation (docs/CORRECTNESS.md §3.1), so these
same functions measure accuracy and judge the optimized workflow. Call them through
`check(name, answer, expected)`.
"""

from __future__ import annotations

import re
import string
from collections import Counter
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel

QA_F1_THRESHOLD = 0.6

_ARTICLES = re.compile(r"\b(a|an|the)\b")
_PUNCT = str.maketrans("", "", string.punctuation)
_NUMBER = re.compile(r"-?\d[\d,]*\.?\d*|-?\.\d+")


class Outcome(BaseModel):
    success: bool
    score: float
    final_answer: Any = None
    expected: Any = None
    checker: str


def normalize_answer(text: Any) -> str:
    """SQuAD normalisation: lowercase, drop punctuation, drop articles, squeeze spaces."""
    s = "" if text is None else str(text)
    s = s.lower().translate(_PUNCT)
    s = _ARTICLES.sub(" ", s)
    return " ".join(s.split())


def f1_score(prediction: Any, gold: Any) -> float:
    pred, ref = normalize_answer(prediction).split(), normalize_answer(gold).split()
    if not pred or not ref:
        return float(pred == ref)
    common = sum((Counter(pred) & Counter(ref)).values())
    if common == 0:
        return 0.0
    precision, recall = common / len(pred), common / len(ref)
    return 2 * precision * recall / (precision + recall)


def _gold(expected: Any) -> Any:
    return expected.get("answer") if isinstance(expected, dict) else expected


def check_qa(answer: Any, expected: Any) -> Outcome:
    gold = _gold(expected)
    score = f1_score(answer, gold)
    # yes/no questions: "yes, they are" must not pass on partial overlap, nor "no" match "yes"
    if normalize_answer(gold) in ("yes", "no"):
        words = normalize_answer(answer).split()
        score = float(bool(words) and words[0] == normalize_answer(gold))
    return Outcome(success=score >= QA_F1_THRESHOLD, score=round(score, 4), final_answer=answer, expected=gold,
                   checker="qa_f1")


def parse_number(value: Any) -> float | None:
    """The last number in the text ("So she has $1,250.00 left." -> 1250.0)."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    for raw in reversed(_NUMBER.findall(str(value))):
        cleaned = raw.replace(",", "").rstrip(".")
        try:
            return float(cleaned)
        except ValueError:
            continue
    return None


def check_math(answer: Any, expected: Any) -> Outcome:
    gold = parse_number(_gold(expected))
    got = parse_number(answer)
    ok = gold is not None and got is not None and abs(got - gold) < 1e-6
    return Outcome(success=ok, score=float(ok), final_answer=answer, expected=_gold(expected), checker="math_exact")


def check_ops(answer: Any, expected: Any) -> Outcome:
    """Pass when the database the agent left equals the correct one."""
    db = answer.get("db") if isinstance(answer, dict) else None
    got = {k: db.get(k) for k in ("orders", "stock", "refunds")} if isinstance(db, dict) else None
    ok = got is not None and got == expected
    return Outcome(success=ok, score=float(ok), final_answer=answer, expected=expected, checker="ops_state")


def check_review(answer: Any, expected: Any) -> Outcome:
    """Naming every file must not pass, so the other files in the task count against it."""
    words = {w.strip(".").lower() for w in re.findall(r"[\w.]+", str(answer or ""))}
    named = [f for f in expected["files"] if f.lower() in words]
    ok = named == [expected["answer"]]
    return Outcome(success=ok, score=float(ok), final_answer=answer, expected=expected["answer"],
                   checker="review_file")


CHECKERS: dict[str, Callable[[Any, Any], Outcome]] = {
    "qa_f1": check_qa,
    "math_exact": check_math,
    "ops_state": check_ops,
    "review_file": check_review,
}


def check(name: str, answer: Any, expected: Any) -> Outcome:
    if name not in CHECKERS:
        raise KeyError(f"no checker {name!r}. Known: {', '.join(sorted(CHECKERS))}")
    return CHECKERS[name](answer, expected)
