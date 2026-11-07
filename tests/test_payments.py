"""Customers pay the agent's 0G compute account directly (depositFor on 0G's Payment Layer); each payment
starts its job. Offline: a fake chain returns receipts and logs in the shape 0G's RPC does."""

from __future__ import annotations

import time

import pytest

pytest.importorskip("langgraph")

from synapse0g import payments, service, spend, upstream  # noqa: E402
from tests.test_paid_run import COST, fake_post  # noqa: E402
from tests.test_spend import CATALOG  # noqa: E402

N = payments.NEURON
VAULT = "0xA3b15Bd2aD18BFB6b5f92D8AA9F444Dd59d1cE32"
AGENT = "0x0C811D28046a77FA27A3CaF089E79DC664CF8178"
CUSTOMER = "0x1111111111111111111111111111111111111111"
TEAM_B = "0x35C98a0033e5DB26d9E31adb0e04bBc3bC74D0dc"
CFG = {"network": {"chain_id": 16661, "rpc": "x", "explorer": "https://chainscan.0g.ai"}, "payment_layer": VAULT,
       "agent_account": AGENT, "quote_minutes": 30, "min_payment_0g": 0.005, "team_wallets": [AGENT, TEAM_B]}


def _log(tx, payer, user, amount, block=100, index=0, address=VAULT):
    pad = lambda a: "0x" + a.lower()[2:].rjust(64, "0")  # noqa: E731
    return {"address": address.lower(), "topics": [payments.DEPOSITED, pad(payer), pad(user)],
            "data": "0x" + hex(amount)[2:].rjust(64, "0"), "blockNumber": hex(block), "transactionHash": tx, "logIndex": hex(index)}


class FakeChain:
    def __init__(self):
        self.receipts, self.logs_by_block, self.vault, self.block = {}, [], 0, 1000

    def _call(self, method, params):
        assert method == "eth_getTransactionReceipt"
        return self.receipts.get(params[0])

    def head(self):
        return self.block

    def logs(self, topics, start, end, address=None):
        return [lg for lg in self.logs_by_block if start <= int(lg["blockNumber"], 16) <= end and lg["topics"][2] == topics[2]]

    def eth_call(self, to, data):
        assert to == VAULT and data.startswith(payments.BALANCE_OF)
        return hex(self.vault)

    def pay(self, tx, payer, amount, user=AGENT, ok=True):
        self.vault += amount if ok and user == AGENT else 0
        self.receipts[tx] = {"status": "0x1" if ok else "0x0", "logs": [_log(tx, payer, user, amount)] if ok else []}
        self.logs_by_block.append(_log(tx, payer, user, amount, block=self.block))


def tx(i):
    return "0x" + f"{i:064x}"


def test_the_transaction_a_customer_sends_is_depositFor_the_agent_account():
    data = payments.calldata(AGENT)
    assert data == "0xaa67c919" + "0" * 24 + AGENT[2:].lower()
    d = payments.decode_deposit(_log(tx(1), CUSTOMER, AGENT, 5 * 10**16, block=7, index=2))
    assert d == {"tx": tx(1), "log_index": 2, "block": 7, "from": CUSTOMER.lower(), "user": AGENT.lower(), "amount": 5 * 10**16}


def test_a_deposit_pays_one_quote_of_its_payer_with_enough_0g_once(tmp_path):
    p = payments.Payments(tmp_path / "p.db", CFG)
    b = {"pay": 4 * 10**16, "need": 3 * 10**16}
    q = p.new_quote(CUSTOMER, {"task": None}, b, {})
    dep = lambda i, payer, amount: {"tx": tx(i), "log_index": 0, "block": 1, "from": payer, "user": AGENT, "amount": amount}  # noqa: E731
    assert p.record(dep(1, TEAM_B, 5 * 10**16)) is None  # someone else's payment does not pay this quote
    assert p.record(dep(2, CUSTOMER, 3 * 10**16)) is None  # too little
    paid = p.record(dep(3, CUSTOMER, 4 * 10**16))
    assert paid["id"] == q["id"] and paid["status"] == "paid" and paid["tx"] == tx(3)
    assert p.record(dep(3, CUSTOMER, 4 * 10**16)) is None  # the same deposit again pays nothing more
    q2 = p.new_quote(CUSTOMER, {}, b, {})
    assert p.record(dep(3, CUSTOMER, 4 * 10**16)) is None and p.quote(q2["id"])["status"] == "awaiting payment"
    labels = {d["tx"]: d["note"] for d in p.deposits()}
    assert labels[tx(1)] == "team wallet" and labels[tx(3)] == "customer"
    with pytest.raises(ValueError, match="not the agent's account"):
        p.record({**dep(9, CUSTOMER, N), "user": CUSTOMER})


def test_an_expired_quote_is_not_paid_but_the_deposit_is_kept(tmp_path):
    p = payments.Payments(tmp_path / "p.db", {**CFG, "quote_minutes": 0})
    q = p.new_quote(CUSTOMER, {}, {"pay": 10**16}, {})
    time.sleep(0.01)
    assert p.quote(q["id"])["status"] == "expired"
    assert p.record({"tx": tx(4), "log_index": 0, "block": 1, "from": CUSTOMER, "user": AGENT, "amount": 10**16}) is None
    assert len(p.deposits()) == 1


@pytest.fixture
def desk(tmp_path, monkeypatch):
    from synapse0g.registry import load_registry

    monkeypatch.setenv("ZG_AGENT_KEY", "sk-agent")
    monkeypatch.setattr(upstream, "_post", fake_post)
    monkeypatch.setattr(service, "ROOT", tmp_path)
    monkeypatch.setattr(service, "_PAID", {})
    chain = FakeChain()
    # the account as 0G sees it: the vault at 80%, as the Router counts it
    guard = spend.Guard(tmp_path / "spend.db", funds=lambda: {"deposit": 0, "credit": 0, "pending_charge": 0,
                                                             "payment_layer": chain.vault}, funds_ttl_s=0)
    monkeypatch.setitem(spend.GUARDS, "zerog-agent", guard)
    q = service.Queue(tmp_path / "service.db")
    d = payments.Desk(payments.Payments(tmp_path / "p.db", CFG), chain=chain, registry=load_registry(catalog=CATALOG),
                      guard=guard, queue=q)
    d.fake = chain
    return d


def _wait(desk, qid, timeout=30):
    end = time.time() + timeout
    while time.time() < end:
        v = desk.view(qid)
        if v["quote"]["status"] not in ("awaiting payment", "paid", "running"):
            return v
        time.sleep(0.1)
    raise AssertionError(desk.view(qid)["quote"])


def test_a_customer_payment_runs_its_job_paid_from_that_deposit(desk):
    q = desk.quote(CUSTOMER)
    assert q["transaction"]["to"] == VAULT and q["transaction"]["data"] == payments.calldata(AGENT)
    assert q["pay"] >= q["breakdown"]["expected"] and q["balances"]["at_quote"]["vault_0g"] == 0
    assert desk.confirm(q["id"], tx(5))["state"] == "pending"  # not mined yet
    desk.fake.pay(tx(5), CUSTOMER, q["pay"])
    out = desk.confirm(q["id"], tx(5))
    assert out["state"] == "paid" and out["quote"]["job_id"]
    v = _wait(desk, q["id"])
    assert v["quote"]["status"] == "done", v["quote"]
    r = v["job"]["result"]
    assert r["passed"] and not r["order_placed"] and all(c["account"] == "zerog-agent" and c["request_id"] for c in r["calls"])
    assert r["billed_0g"] == pytest.approx(len(r["calls"]) * COST / N)
    bal = v["quote"]["balances"]
    assert bal["at_payment"]["vault_0g"] == q["pay"] / N and "after_job" in bal
    assert {e["type"] for e in v["progress"]["events"]} >= {"task", "phase", "call", "node"}
    h = desk.history()["deposits"][0]
    assert h["tx"] == tx(5) and h["job"]["status"] == "done" and h["note"] == "customer"


def test_a_failed_or_wrong_transaction_starts_nothing(desk):
    q = desk.quote(CUSTOMER)
    desk.fake.pay(tx(6), CUSTOMER, q["pay"], ok=False)
    assert desk.confirm(q["id"], tx(6))["state"] == "failed"
    desk.fake.pay(tx(7), CUSTOMER, q["pay"], user=CUSTOMER)
    assert desk.confirm(q["id"], tx(7))["state"] == "not a payment"
    desk.fake.pay(tx(8), CUSTOMER, q["pay"] - 1)
    out = desk.confirm(q["id"], tx(8))
    assert out["state"] == "not matched" and "less than" in out["why"]
    assert desk.view(q["id"])["job"] is None


def test_the_watcher_finds_a_payment_without_its_hash(desk):
    q = desk.quote(CUSTOMER)
    desk.fake.pay(tx(9), CUSTOMER, q["pay"])
    assert desk.watch_once() == 1
    assert _wait(desk, q["id"])["quote"]["status"] == "done"


def test_a_quote_the_key_cannot_cover_is_refused_before_payment(desk):
    desk.guard.read_key_room = lambda: 10**15  # 0.001 0G left on the key
    with pytest.raises(ValueError, match="raise the key's credit limit"):
        desk.quote(CUSTOMER)


def test_a_job_stopped_by_the_key_cap_resumes_on_the_credit_already_paid(desk):
    room = {"left": 10**18}
    desk.guard.read_key_room = lambda: room["left"]
    q = desk.quote(CUSTOMER)
    room["left"] = 3 * 10**14  # the owner lowers the key's limit after the quote: the job must stop, not fail
    desk.fake.pay(tx(11), CUSTOMER, q["pay"])
    desk.confirm(q["id"], tx(11))
    v = _wait(desk, q["id"])
    assert v["quote"]["status"] == "waiting for funds" and "key's cap" in v["quote"]["note"]
    assert any(e["type"] == "paused" for e in v["progress"]["events"])
    with pytest.raises(ValueError, match="raise the key's credit limit"):
        desk.resume(q["id"])
    room["left"] = 10**18  # raised again: resume, with no new payment
    desk.resume(q["id"])
    v = _wait(desk, q["id"])
    assert v["quote"]["status"] == "done" and v["job"]["result"]["passed"]
    assert len(desk.pay.deposits()) == 1  # one payment, one job, run to the end
    import zipfile

    with zipfile.ZipFile(service.ROOT / v["job"]["result"]["package"]) as z:
        names = {n.split("/", 1)[1] for n in z.namelist()}
        code = z.read(next(n for n in z.namelist() if n.endswith("optimized_agent.py"))).decode()
    assert {"optimized_agent.py", "supplier_agent.py", "sir_after.json", "policy.json", "input.json"} <= names
    compile(code, "optimized_agent.py", "exec")  # the generated agent is valid Python
    assert "g.add_edge(START, 'get_quotes')" in code and "protected(original.place_order" in code


def test_a_customers_own_pasted_agent_is_compiled_optimized_and_returned_as_code(desk):
    import json
    import zipfile

    from synapse0g.agents import supplier

    code = supplier.APP_PATH.read_text()
    sample = {"rfq": json.loads(supplier.split("test", 1)[0].question)}
    q = desk.quote(CUSTOMER, code=code, sample=sample, name="my supplier agent")
    assert q["spec"]["agent"] == "custom" and q["spec"]["steps"] == 6 and q["pay"] > 0
    desk.fake.pay(tx(21), CUSTOMER, q["pay"])
    desk.confirm(q["id"], tx(21))
    v = _wait(desk, q["id"], timeout=60)
    assert v["quote"]["status"] == "done", v["quote"]
    r = v["job"]["result"]
    assert r["custom"] and "place_order" in r["flagged"] and r["billed_0g"]
    compiled = next(e for e in v["progress"]["events"] if e["type"] == "compiled")
    assert {"memoize", "parallelize"} <= set(compiled["applied"])
    with zipfile.ZipFile(service.ROOT / r["package"]) as z:
        code_out = z.read(next(n for n in z.namelist() if n.endswith("optimized_agent.py"))).decode()
        assert any(n.endswith("customer_agent.py") for n in z.namelist())
    assert "import customer_agent as original" in code_out and "g.add_edge(START, 'get_quotes')" in code_out
