"""A paid run job, offline: the real compiled supplier app, proxy, spend guard and SQLite ledger, with
0G faked at the HTTP layer. Checks that every call is paid from the agent's own account and billed,
that two calls overlap, that the order is never placed, and that low funds make the job wait."""

from __future__ import annotations

import json
import re
import threading
import time

import pytest

pytest.importorskip("langgraph")

from synapse0g import service, spend, upstream  # noqa: E402
from synapse0g.agents import supplier_desk  # noqa: E402
from synapse0g.registry import load_registry  # noqa: E402
from tests.test_spend import CATALOG  # noqa: E402

N = spend.NEURON
COST = 2 * 10**14  # what the fake 0G bills per call: 0.0002 0G


def fake_post(provider, body, timeout, key=None, extra=None):
    assert provider.name == "zerog-agent" and key == "sk-agent"  # never the team key
    time.sleep(0.15)
    system, user = body["messages"][0]["content"], body["messages"][-1]["content"]
    text = "ok"
    if system.startswith("Purchasing decision"):
        rfq = supplier_desk._RFQS[re.search(r"RFQ-\d+", user).group(0)]
        text = json.dumps({"supplier_id": supplier_desk.right_answer(rfq), "quantity": rfq["quantity"], "total_cost": 1})
    rid = "req-" + extra["X-Client-Request-Id"]
    return ({"choices": [{"message": {"role": "assistant", "content": text}, "finish_reason": "stop"}],
             "usage": {"prompt_tokens": 100, "completion_tokens": 10},
             "x_0g_trace": {"request_id": rid, "billing": {"currency": "0g", "total_cost": str(COST)}}}, {})


@pytest.fixture
def agent(tmp_path, monkeypatch):
    monkeypatch.setenv("ZG_AGENT_KEY", "sk-agent")
    monkeypatch.setenv("0G_API_KEY", "sk-team")
    monkeypatch.setattr(upstream, "_post", fake_post)
    monkeypatch.setattr(service, "ROOT", tmp_path)
    monkeypatch.setattr(service, "_PAID", {})
    funds = {"deposit": 0, "credit": 0, "pending_charge": 0, "payment_layer": N // 10}  # 0.1 0G in the vault
    guard = spend.Guard(tmp_path / "spend.db", funds=lambda: dict(funds), funds_ttl_s=0)
    guard.funds = funds
    monkeypatch.setitem(spend.GUARDS, "zerog-agent", guard)
    q = service.Queue(tmp_path / "service.db")
    return service.Agent(q, service.Ledger(tmp_path / "service.db"), registry=load_registry(catalog=CATALOG), guard=guard)


def test_a_paid_run_bills_every_call_to_the_agent_and_overlaps_two(agent):
    job = service.submit(agent.queue, "run", {"cap_0g": 0.05})
    done = agent.step()
    assert done.id == job.id and done.status == "done", done.note
    r = done.result
    assert r["passed"] and not r["order_placed"] and r["compiled_now"]
    run = [c for c in r["calls"] if not c["compile"]]
    assert run and all(c["account"] == "zerog-agent" and c["request_id"] and c["billed_0g"] == COST / N for c in r["calls"])
    assert r["billed_0g"] == pytest.approx(len(r["calls"]) * COST / N)
    assert r["peak_overlap"] == 2  # the four lookups run side by side, two at a time
    assert agent.guard.totals(job.id)["settled"] == len(r["calls"]) * COST


def test_low_funds_make_a_paid_job_wait_not_fail(agent):
    agent.guard.funds["payment_layer"] = int(0.011 * N)  # 0.0088 0G admissible: under 0G's 0.01 minimum
    job = service.submit(agent.queue, "run", {})
    agent.step()
    job = agent.queue.get(job.id)
    assert job.status == "waiting" and "0.01" in job.note and job.result is None


def test_the_job_cap_stops_a_paid_run_partway(agent):
    job = service.submit(agent.queue, "run", {"cap_0g": 0.004})
    agent.step()
    job = agent.queue.get(job.id)
    assert job.status == "waiting" and "cap" in job.note
    assert agent.guard.totals(job.id)["settled"] + agent.guard.totals(job.id)["reserved"] <= 0.004 * N
    assert not threading.active_count() > 50
