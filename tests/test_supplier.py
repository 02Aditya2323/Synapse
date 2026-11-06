"""The supplier-check study, offline: a fake provider, the real app, compiler, runtime, tuner and firewall."""

from __future__ import annotations

import json
import re
import time
from argparse import Namespace

import pytest

pytest.importorskip("langgraph")

from synapse0g import proxy, upstream  # noqa: E402
from synapse0g.agents import supplier, supplier_desk  # noqa: E402
from synapse0g.registry import load_registry  # noqa: E402
from synapse0g.tasks import CHECKERS  # noqa: E402

DELAY = 0.05


def fake_call(provider, model, body, record, **kw):
    """Right answers, after a fixed delay, with made-up token counts (never reported)."""
    time.sleep(DELAY)
    system, user = body["messages"][0]["content"], body["messages"][-1]["content"]
    text = "ok"
    if system.startswith("Purchasing decision"):
        rid = re.search(r"RFQ-\d+", user).group(0)
        rfq = supplier_desk._RFQS[rid]
        text = json.dumps({"supplier_id": supplier_desk.right_answer(rfq), "quantity": rfq["quantity"], "total_cost": 1})
    thinking = body.get("enable_thinking") is not False
    record.status, record.latency_s = "ok", DELAY
    record.tokens_in, record.tokens_out = 100, 50 if thinking else 10
    record.reasoning_tokens = 40 if thinking else 0
    record.listed_cost_usd = model.cost(record.tokens_in, record.tokens_out)
    return {"choices": [{"message": {"role": "assistant", "content": text}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 100, "completion_tokens": record.tokens_out}}


@pytest.fixture(autouse=True)
def _fake(monkeypatch):
    monkeypatch.setattr(upstream, "call", fake_call)
    monkeypatch.setattr(proxy.upstream, "call", fake_call)


def test_every_marketplace_request_has_a_checkable_answer():
    tasks = supplier.load_supplier()
    assert len(tasks) == 60
    assert sum(t.gold == "none" for t in tasks) >= 5
    t = next(t for t in tasks if t.gold != "none")
    qty = json.loads(t.question)["quantity"]
    check = CHECKERS["supplier"]
    assert check(t, json.dumps({"supplier_id": t.gold, "quantity": qty}))
    assert not check(t, json.dumps({"supplier_id": t.gold, "quantity": qty + 1}))
    assert not check(t, json.dumps({"supplier_id": None}))


def test_compile_finds_the_parallel_steps_and_refuses_the_order():
    import synapse0g

    ex = supplier.example()
    rfq = supplier_desk.request("RFQ-003")
    app = synapse0g.compile(ex.builder, sample={"rfq": rfq}, effects=ex.EFFECTS, name="supplier_check")
    assert {"memoize", "parallelize"} <= set(app.applied)
    assert app.after.meta["max_concurrency"] == 4
    assert "refused  memoize on place_order" in app.explain()
    # the optimized graph gives LangGraph's own answer, and never places the order unasked
    assert app.invoke({"rfq": rfq})["order"] == ex.builder.compile().invoke({"rfq": rfq})["order"]
    assert [r.stubbed for r in app.last.node_runs if r.effect == "irreversible"] == [True]


def test_the_whole_study_runs_and_attributes_every_call_to_its_task(tmp_path, capsys):
    from synapse0g.supplier_bench import run

    args = Namespace(model="zg-main", calib=3, test=3, workers=3, min_gain=0.03, max_trials=2, out=str(tmp_path))
    doc = run(args, load_registry())
    assert doc["graph_stage"]["verdict"]["passes"] == [3, 3, 3]
    runs = doc["runs"]
    # as written: five model calls per task, each in that task's own session
    for task_run in runs["test as written 1"].values():
        assert len(task_run["records"]) == 5 and len({r["session"] for r in task_run["records"]}) == 1
    # optimized: the policy checklist is reused, so a task makes four calls once the cache is warm
    assert min(len(r["records"]) for r in runs["test optimized"].values()) == 4
    assert all(r["passed"] for r in runs["test optimized"].values())
    assert not any(r["extra"]["order_executed"] for r in runs["test optimized"].values())
    assert all(r["extra"]["order_executed"] or json.loads(r["answer"])["supplier_id"] is None
               for r in runs["test as written 1"].values())
    assert doc["evaluation"]["hand_parallel"]["passed"] == 3
