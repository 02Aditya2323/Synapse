"""The procurement study, offline: a fake model that reads documents perfectly, the real agent,
compiler, runtime, tuner and firewall."""

from __future__ import annotations

import json
import re
import time

import pytest

from synapse0g import proxy, upstream
from synapse0g.agents import procurement, procurement_desk
from synapse0g.registry import load_registry
from synapse0g.tasks import CHECKERS

FACTS = {r["id"]: r for r in procurement_desk.index()}
DELAY = 0.05


def _answer(system: str, user: str) -> str:
    if system.startswith("You turn a purchase request"):
        sid = next(sid for sid, r in FACTS.items() if (procurement_desk.DATA / sid / "request.txt").read_text() in user)
        r = FACTS[sid]
        mins = {k: need for k, need, _, _ in procurement_desk.SPECS[r["category"]]}
        return json.dumps({"category": r["category"], "quantity": r["quantity"], "max_unit_price_before_gst": r["max_unit_price"],
                           "needed_within_days": r["needed_within_days"], "min_specs": mins})
    if system.startswith("You read one supplier quotation"):
        name = re.search(r"File: (\S+)", user).group(1)
        q = next(q for r in FACTS.values() for q in r["facts"] if q["file"] == name and q["quote_no"] in user)
        return json.dumps({"supplier": q["supplier"], "item": q["item"], "refurbished": q["refurbished"], "specs": q["specs"],
                           "quantity_available": q["quantity_available"], "unit_price": q["unit_price"],
                           "prices_include_gst": q["gst_included"], "shipping": q["shipping"], "delivery_days": q["delivery_days"],
                           "payment_terms": q["payment_terms"],
                           "advance_percent": None if q["payment_terms"] is None else procurement_desk.advance_share(q["payment_terms"]) * 100,
                           "valid_until": q["valid_until"], "sources": {}})
    chosen = re.search(r'"supplier": "([^"]+)", "landed_total"', user)
    return f"Recommend {chosen.group(1) if chosen else 'nothing'}."


def fake_call(provider, model, body, record, **kw):
    time.sleep(DELAY)
    text = _answer(body["messages"][0]["content"], body["messages"][-1]["content"])
    record.status, record.latency_s, record.tokens_in, record.tokens_out = "ok", DELAY, 100, 20
    record.listed_cost_usd = model.cost(100, 20)
    return {"choices": [{"message": {"role": "assistant", "content": text}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 20}}


@pytest.fixture(autouse=True)
def _fake(monkeypatch):
    monkeypatch.setattr(upstream, "call", fake_call)
    monkeypatch.setattr(proxy.upstream, "call", fake_call)


def test_the_policy_in_code_reaches_the_right_outcome_on_every_scenario():
    """With perfect reading, the agent's own rules agree with the generator's: the task set is sound."""
    ex = procurement.example()
    check = CHECKERS["procurement"]
    for task in procurement.load_procurement():
        sid = json.loads(task.question)["scenario"]
        r = FACTS[sid]
        reqs = json.loads(_answer("You turn a purchase request", (procurement_desk.DATA / sid / "request.txt").read_text()))
        docs = ex.load_documents(procurement.inputs(task)["quotation_paths"])
        offers = [dict(json.loads(_answer("You read one supplier quotation", f"File: {d['file']}\n{d['text']}")), file=d["file"]) for d in docs]
        evaluation = ex.check_offers(reqs, offers)
        draft = {"decision": evaluation["decision"], "justification": f"Recommend {evaluation['decision']['supplier']}."}
        assert check(task, json.dumps(draft)), (sid, evaluation["decision"], r["answer"])


def test_the_demo_request_picks_the_gst_inclusive_offer():
    ex = procurement.example()
    folder = procurement_desk.DATA / "PR-DEMO"
    assert FACTS["PR-DEMO"]["answer"] == {"status": "selected", "supplier": "TechNova Distributors", "landed_total": 195600.0}
    assert {p.suffix for p in folder.glob("quotation_*")} == {".pdf", ".xlsx", ".txt"}
    assert "11,400" in ex.load_documents([str(folder / "quotation_1_shree.pdf")])[0]["text"]


def test_the_whole_study_runs_offline(tmp_path):
    from synapse0g import study

    doc = study.run(procurement.spec(3, 3), load_registry(), model_id="zg-main", workers=3, max_trials=1, out=tmp_path,
                    log=lambda m: None)
    assert doc["graph_stage"]["verdict"]["passes"] == [3, 3, 3]
    runs = doc["runs"]
    for r in runs["test as written 1"].values():
        assert len({rec["session"] for rec in r["records"]}) == 1  # each task's calls in its own session
    assert all(r["passed"] for label in ("test as written 1", "test optimized", "test hand-parallel (thread pool)")
               for r in runs[label].values())
    assert {"parallelize"} <= set(doc["compile"]["applied"])


def test_numbers_written_as_text_are_read_or_sent_for_clarification():
    ex = procurement.example()
    reqs = {"category": "monitor", "quantity": 2, "max_unit_price_before_gst": 12000, "needed_within_days": 7, "min_specs": {}}
    base = {"supplier": "A", "file": "a.pdf", "quantity_available": "20 units", "delivery_days": "5 days",
            "valid_until": "2026-12-01", "payment_terms": "net 30", "advance_percent": 0, "prices_include_gst": False}
    free = ex.check_offers(reqs, [dict(base, unit_price="₹10,000", shipping="Free delivery")])
    assert free["decision"] == {"status": "selected", "supplier": "A", "landed_total": 23600.0}
    unclear = ex.check_offers(reqs, [dict(base, unit_price="on request", shipping=None)])
    assert unclear["decision"]["status"] == "needs_clarification"
