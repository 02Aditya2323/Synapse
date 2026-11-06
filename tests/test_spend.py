"""Spending from the agent's own 0G account: reservations, caps, overlap, and what happens on failure.

The Router is faked at the HTTP layer (`upstream._post`), so the real `upstream.call`, the real
guard and a real SQLite ledger run."""

from __future__ import annotations

import threading
import time

import pytest

from synapse0g import spend, upstream
from synapse0g.registry import load_registry

N = spend.NEURON
TAGS = ("qwen3.8-flash", "qwen3.7-flash", "qwen/qwen3.8-27b", "openai/gpt-oss-120b", "openai/gpt-oss-20b")
CATALOG = {t: {"pricing_usd": {"prompt": "0.0000002", "completion": "0.0000006"},
               "pricing": {"prompt": str(10**12), "completion": str(4 * 10**12)}} for t in TAGS}


@pytest.fixture
def reg(monkeypatch):
    monkeypatch.setenv("ZG_AGENT_KEY", "sk-test-agent")
    return load_registry(catalog=CATALOG)


@pytest.fixture
def guard(tmp_path, monkeypatch):
    state = {"admit": N // 10, "key_room": None}  # 0.1 0G in the vault-free ledger
    g = spend.Guard(tmp_path / "spend.db",
                    funds=lambda: {"deposit": state["admit"], "credit": 0, "pending_charge": 0, "payment_layer": 0},
                    key_room=lambda: state["key_room"], funds_ttl_s=0)
    g.state = state
    monkeypatch.setitem(spend.GUARDS, "zerog-agent", g)
    return g


def _record():
    return upstream.CallRecord(session="t", step="s", step_label="s", model_id="agent-main", tag="qwen3.8-flash",
                               provider="zerog-agent", started_at=time.time(), latency_s=0, status="pending")


def _reply(cost=3 * 10**14, rid="req-1"):
    return ({"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 10, "completion_tokens": 5},
             "x_0g_trace": {"request_id": rid, "billing": {"currency": "0g", "total_cost": str(cost)}}}, {})


BODY = {"model": "qwen3.8-flash", "messages": [{"role": "user", "content": "hi"}]}


def test_the_agent_never_falls_back_to_the_team_key(monkeypatch):
    monkeypatch.delenv("ZG_AGENT_KEY", raising=False)
    monkeypatch.setenv("0G_API_KEY", "sk-team")
    reg = load_registry(catalog=CATALOG)
    with pytest.raises(KeyError, match="ZG_AGENT_KEY"):
        reg.providers["zerog-agent"].keys()
    assert reg.providers["zerog-agent"].guarded and not reg.providers["zerog"].guarded


def test_a_guarded_call_is_bounded_reserved_and_settled_at_the_billed_charge(reg, guard, monkeypatch):
    sent = {}

    def post(provider, body, timeout, key=None, extra=None):
        sent.update(body=body, extra=extra, key=key)
        return _reply(cost=3 * 10**14, rid="req-a")

    monkeypatch.setattr(upstream, "_post", post)
    rec = _record()
    upstream.call(reg.providers["zerog-agent"], reg.model("agent-main"), dict(BODY), rec)
    assert sent["body"]["max_tokens"] == 2048  # an output cap is always sent
    assert sent["key"] == "sk-test-agent" and sent["extra"]["X-Client-Request-Id"] == rec.hold_id
    assert rec.billed_0g == 3e-4 and rec.request_id == "req-a"
    t = guard.totals()
    assert t["settled"] == 3 * 10**14 and t["reserved"] == 0 and t["uncertain"] == 0


def test_concurrent_reservations_never_promise_the_same_funds(guard):
    guard.state["admit"] = 5 * 10**16  # 0.05 0G
    each = 10**16  # 0.01 0G worst case
    start = threading.Barrier(12)
    won = []

    def one():
        start.wait()
        try:
            won.append(guard.reserve("m", each))
        except spend.LowFunds:
            pass

    threads = [threading.Thread(target=one) for _ in range(12)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    assert len(won) == 5  # exactly what 0.05 0G covers, however the threads interleave
    assert guard.totals()["reserved"] == 5 * each


def test_two_independent_calls_overlap_and_no_more(reg, guard, monkeypatch):
    live, peak, lock = [0], [0], threading.Lock()

    def post(provider, body, timeout, key=None, extra=None):
        with lock:
            live[0] += 1
            peak[0] = max(peak[0], live[0])
        time.sleep(0.25)
        with lock:
            live[0] -= 1
        return _reply(rid=extra["X-Client-Request-Id"])

    monkeypatch.setattr(upstream, "_post", post)
    p, m = reg.providers["zerog-agent"], reg.model("agent-main")
    t0 = time.perf_counter()
    threads = [threading.Thread(target=upstream.call, args=(p, m, dict(BODY), _record())) for _ in range(4)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    wall = time.perf_counter() - t0
    assert peak[0] == 2
    assert 0.45 < wall < 0.9  # four calls of 0.25 s, two at a time: two rounds, not four
    assert guard.totals()["calls_settled"] == 4


def test_the_job_cap_stops_a_job_and_the_key_cap_stops_every_job(reg, guard, monkeypatch):
    monkeypatch.setattr(upstream, "_post", lambda *a, **k: _reply())
    p, m = reg.providers["zerog-agent"], reg.model("agent-main")
    worst = spend.worst_case(m, {**BODY, "max_tokens": 2048})
    with guard.for_job("job-1", cap_neuron=worst + worst // 2):
        upstream.call(p, m, dict(BODY), _record())  # settles at far less than its worst case
        upstream.call(p, m, dict(BODY), _record())
        guard.state["admit"] = N  # plenty in the account; the job's cap is what stops it
        with pytest.raises(spend.CapReached, match="job-1"):
            for _ in range(20):
                upstream.call(p, m, dict(BODY), _record())
    guard.state["key_room"] = worst - 1
    with pytest.raises(spend.CapReached, match="key's cap"):
        upstream.call(p, m, dict(BODY), _record())


def test_low_funds_is_a_typed_refusal_before_anything_is_sent(reg, guard, monkeypatch):
    sent = []
    monkeypatch.setattr(upstream, "_post", lambda *a, **k: sent.append(1) or _reply())
    guard.state["admit"] = 10**16 - 1  # under 0G's 0.01 minimum
    with pytest.raises(spend.LowFunds, match="0.01"):
        upstream.call(reg.providers["zerog-agent"], reg.model("agent-main"), dict(BODY), _record())
    assert not sent


def test_a_timeout_keeps_its_worst_case_held_until_reconciled_and_restarts_keep_it(reg, guard, monkeypatch, tmp_path):
    def post(*a, **k):
        raise upstream.ProviderError("zerog-agent unreachable or sent a broken reply: TimeoutError")

    monkeypatch.setattr(upstream, "_post", post)
    monkeypatch.setattr(upstream.time, "sleep", lambda s: None)
    with pytest.raises(upstream.ProviderError):
        upstream.call(reg.providers["zerog-agent"], reg.model("agent-main"), dict(BODY), _record())
    held = guard.totals()["uncertain"]
    assert held > 0 and len(guard.open_holds()) == 3  # three bounded attempts, each held: any may have been billed

    # a restart: a new guard on the same ledger still holds them, and a hold left 'reserved' becomes uncertain
    stray = guard.reserve("qwen3.8-flash", 10**12)
    again = spend.Guard(guard.db, funds=guard.read_funds)
    assert again.recover() == 1 and again.totals()["uncertain"] == held + 10**12
    again.release(stray, "test")

    # usage history settles each one: by time and model, only when exactly one record fits
    opens = again.open_holds()
    history = [{"request_id": f"req-{i}", "model": "qwen3.8-flash", "at": h["created"] + 1, "cost_neuron": 2 * 10**14}
               for i, h in enumerate(opens[:1])]
    assert again.reconcile(history) == 1
    assert again.totals()["settled"] == 2 * 10**14 and len(again.open_holds()) == 2


def test_refusals_release_spend_errors_stop_and_rate_limits_wait_as_asked(reg, guard, monkeypatch):
    p, m = reg.providers["zerog-agent"], reg.model("agent-main")
    monkeypatch.setattr(upstream, "_post", lambda *a, **k: (_ for _ in ()).throw(
        upstream.ProviderError("HTTP 400", 400, "bad request")))
    with pytest.raises(upstream.ProviderError):
        upstream.call(p, m, dict(BODY), _record())
    assert guard.totals()["released"] == 1 and not guard.open_holds()  # answered with a refusal: not billed

    calls = []
    monkeypatch.setattr(upstream, "_post", lambda *a, **k: calls.append(1) or (_ for _ in ()).throw(
        upstream.ProviderError("HTTP 402", 402, '{"error":{"code":"insufficient_balance"}}')))
    with pytest.raises(spend.LowFunds):
        upstream.call(p, m, dict(BODY), _record())
    assert len(calls) == 1  # no retry can fix an empty account

    waits, n = [], [0]

    def limited(*a, **k):
        n[0] += 1
        if n[0] < 3:
            raise upstream.ProviderError("HTTP 429", 429, '{"error":{"code":"fair_share_exceeded"}}', retry_after=7)
        return _reply(rid="req-late")

    monkeypatch.setattr(upstream, "_post", limited)
    monkeypatch.setattr(upstream.time, "sleep", waits.append)
    rec = _record()
    upstream.call(p, m, dict(BODY), rec)
    assert waits == [7.5, 7.5] and rec.request_id == "req-late" and rec.retries == 2


def test_when_funds_are_tight_a_call_waits_for_the_one_in_flight_instead_of_failing(reg, guard, monkeypatch):
    m = reg.model("agent-main")
    worst = spend.worst_case(m, {**BODY, "max_tokens": 2048})
    guard.state["admit"] = int(worst * 1.5)  # room for one call at its worst, not two
    live, peak, lock = [0], [0], threading.Lock()

    def post(provider, body, timeout, key=None, extra=None):
        with lock:
            live[0] += 1
            peak[0] = max(peak[0], live[0])
        time.sleep(0.1)
        with lock:
            live[0] -= 1
        return _reply(cost=10**12, rid=extra["X-Client-Request-Id"])  # the real charge is tiny

    monkeypatch.setattr(upstream, "_post", post)
    p = reg.providers["zerog-agent"]
    errors = []

    def one():
        try:
            upstream.call(p, m, dict(BODY), _record())
        except Exception as e:  # recorded: the test fails on any
            errors.append(e)

    threads = [threading.Thread(target=one) for _ in range(2)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    assert not errors and not guard.refusals
    assert peak[0] == 1 and guard.totals()["calls_settled"] == 2  # one after the other: slower, not stopped


def test_a_key_with_no_limit_on_0g_is_held_to_synapse0gs_own_cap(monkeypatch):
    class Account:
        def api_keys(self):
            return [{"name": "synapse-agent", "status": "active", "credit_limit": None, "used": "0.1"}]

    monkeypatch.setattr(spend, "SYNAPSE_KEY_CAP", 3 * 10**17)
    assert spend.key_room(Account()) == 2 * 10**17
    assert spend.key_cap_source(Account())["by"] == "synapse0g"
    monkeypatch.setattr(spend, "SYNAPSE_KEY_CAP", 0)
    with pytest.raises(spend.CapReached, match="no credit limit"):
        spend.key_room(Account())
