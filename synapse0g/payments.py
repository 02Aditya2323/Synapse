"""Customers pay synapse0g straight into its 0G compute account, and each payment starts its job.

    quote  ->  the customer pays: depositFor(agent account) on 0G's Payment Layer, from their wallet
           ->  synapse0g sees the Deposited event (by the tx hash the page sends, or by watching)
           ->  matches it to the quote: same payer, enough 0G, not expired, never used before
           ->  the job runs, paid from that balance, capped, with every call's request id and charge

No contract of ours, no wallet of ours in between, and no private key on the server: the customer's
own transaction puts the 0G where the agent's Router key spends it. `payments.yaml` names the
Payment Layer and the agent account; both were checked against the contract's verified source.

The price is what the job needs to be admitted and to run, not a guess at value:
expected cost (measured in earlier runs, in 0G) + room for two calls in flight at their worst case,
less what the account already holds, divided by the share of the vault 0G counts (0.8). Everything
it is made of is shown with the quote.
"""

from __future__ import annotations

import json
import math
import sqlite3
import threading
import time
import uuid
from pathlib import Path

import yaml

from synapse0g.registry import ROOT

NEURON = 10**18
DB = ROOT / "data" / "payments.db"
CONFIG = ROOT / "payments.yaml"
DEPOSITED = "0x8752a472e571a816aea92eec8dae9baf628e840f4929fbcc2d155e6233ff68a7"  # Deposited(address,address,uint256)
DEPOSIT_FOR = "0xaa67c919"  # depositFor(address)
BALANCE_OF = "0x70a08231"  # balanceOf(address)
CHUNK = 5_000


def load_config(path: Path = CONFIG) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _pad(address: str) -> str:
    return address.lower().removeprefix("0x").rjust(64, "0")


def calldata(account: str) -> str:
    """The data of the customer's transaction: depositFor(account)."""
    return DEPOSIT_FOR + _pad(account)


def decode_deposit(log: dict) -> dict:
    return {"tx": log["transactionHash"], "log_index": int(log.get("logIndex", "0x0"), 16), "block": int(log["blockNumber"], 16),
            "from": "0x" + log["topics"][1][-40:], "user": "0x" + log["topics"][2][-40:], "amount": int(log["data"][2:66], 16)}


def vault_balance(chain, cfg: dict) -> int:
    """The agent account's balance in the Payment Layer, in neuron, read from the contract."""
    return int(chain.eth_call(cfg["payment_layer"], BALANCE_OF + _pad(cfg["agent_account"])), 16)


# ---- the price ---------------------------------------------------------------------------------
def measured_run_cost(registry, agent: str = "supplier") -> dict:
    """What one optimized run costs in 0G: each held-out optimized run of the agent's latest study,
    its measured tokens at 0G's own per-token price. Also the largest prompt seen, for the worst case."""
    paths = sorted((ROOT / "reports").glob(f"{agent}_zg-main_*.json"))
    if not paths:
        raise ValueError(f"no study of {agent} to price a run from")
    doc = json.loads(paths[-1].read_text(encoding="utf-8"))
    by_tag = {m.tag: m for m in registry.models.values() if m.provider == "zerog"}
    costs, largest = [], 0
    for task in doc["runs"].get("test optimized", {}).values():
        total = 0
        for r in task["records"]:
            if r.get("status") != "ok":
                continue
            m = by_tag.get(r.get("tag"))
            if m is None or m.neuron_in is None or r.get("tokens_in") is None:
                raise ValueError(f"cannot price a call in {paths[-1].name}: {r.get('tag')}")
            total += r["tokens_in"] * m.neuron_in + (r.get("tokens_out") or 0) * m.neuron_out
            largest = max(largest, r["tokens_in"])
        costs.append(total)
    if not costs:
        raise ValueError(f"{paths[-1].name} has no optimized runs")
    return {"report": paths[-1].name, "runs": len(costs), "mean": sum(costs) // len(costs), "max": max(costs),
            "largest_prompt_tokens": largest}


def price(registry, *, free_neuron: int, compiled: bool, concurrency: int = 2, vault_ratio: float = 0.8,
          min_cost: int = 10**16, min_payment: int = 5 * 10**15) -> dict:
    """What a paid supplier run must be paid, and why, in neuron."""
    from synapse0g import spend

    run = measured_run_cost(registry)
    model = registry.model("agent-main")
    max_out = registry.providers[model.provider].max_tokens or 2048
    # one token per 4 bytes of prompt, as measured, plus the JSON around it; the guard reserves the exact
    # bytes of each real request, so this only sizes the quote
    worst_call = spend.worst_case(model, {"messages": [{"content": "x" * (run["largest_prompt_tokens"] * 4 + 1500)}],
                                          "max_tokens": max_out})
    # compiling traces the app once, step by step, with real calls: about what one run as written costs
    compile_cost = 0 if compiled else _as_written_cost(registry)
    expected = run["max"] + compile_cost
    room = max(concurrency * worst_call, min_cost)
    need = room + expected  # admitted at the peak, with the job's spend still to come
    shortfall = max(need - free_neuron, 0)
    pay = max(math.ceil(shortfall / vault_ratio / 10**15) * 10**15, expected, min_payment)
    return {"pay": pay, "expected": expected, "run_max": run["max"], "run_mean": run["mean"], "compile": compile_cost,
            "worst_call": worst_call, "room": room, "need": need, "free_now": free_neuron, "vault_ratio": vault_ratio,
            "from_report": run["report"], "runs_measured": run["runs"]}


def _as_written_cost(registry) -> int:
    paths = sorted((ROOT / "reports").glob("supplier_zg-main_*.json"))
    doc = json.loads(paths[-1].read_text(encoding="utf-8"))
    by_tag = {m.tag: m for m in registry.models.values() if m.provider == "zerog"}
    worst = 0
    for task in doc["runs"].get("test as written 1", {}).values():
        worst = max(worst, sum(r["tokens_in"] * by_tag[r["tag"]].neuron_in + (r.get("tokens_out") or 0) * by_tag[r["tag"]].neuron_out
                               for r in task["records"] if r.get("status") == "ok"))
    return worst


# ---- quotes, deposits and their match ----------------------------------------------------------
class Payments:
    def __init__(self, db: Path = DB, cfg: dict | None = None):
        self.db, self.cfg = Path(db), cfg or load_config()
        self.db.parent.mkdir(parents=True, exist_ok=True)
        self.team = {w.lower() for w in self.cfg.get("team_wallets") or []}
        self._lock = threading.Lock()
        with self._conn() as c:
            c.execute("""create table if not exists quotes (id text primary key, created real, expires real, payer text,
                         spec text, breakdown text, pay integer, status text, job_id text, tx text, balances text, note text)""")
            c.execute("""create table if not exists deposits (tx text, log_index integer, block integer, payer text, account text,
                         amount integer, seen real, quote_id text, note text, primary key (tx, log_index))""")
            c.execute("create table if not exists cursor (k text primary key, v integer)")

    def _conn(self):
        c = sqlite3.connect(self.db, timeout=30, isolation_level=None)
        c.row_factory = sqlite3.Row
        return c

    # quotes
    def new_quote(self, payer: str, spec: dict, breakdown: dict, balances: dict) -> dict:
        if not (isinstance(payer, str) and payer.startswith("0x") and len(payer) == 42):
            raise ValueError("the payer is the wallet address that will pay, 0x followed by 40 hex characters")
        q = {"id": "q-" + uuid.uuid4().hex[:10], "created": time.time(), "expires": time.time() + 60 * self.cfg["quote_minutes"],
             "payer": payer.lower(), "spec": spec, "breakdown": breakdown, "pay": breakdown["pay"], "status": "awaiting payment",
             "job_id": None, "tx": None, "balances": {"at_quote": balances}, "note": ""}
        with self._conn() as c:
            c.execute("insert into quotes values (?,?,?,?,?,?,?,?,?,?,?,?)",
                      (q["id"], q["created"], q["expires"], q["payer"], json.dumps(spec), json.dumps(breakdown), q["pay"],
                       q["status"], None, None, json.dumps(q["balances"]), ""))
        return self.quote(q["id"])

    def quote(self, quote_id: str) -> dict | None:
        with self._conn() as c:
            r = c.execute("select * from quotes where id = ?", (quote_id,)).fetchone()
        return self._row(r) if r else None

    def quotes(self, n: int = 50) -> list[dict]:
        with self._conn() as c:
            return [self._row(r) for r in c.execute("select * from quotes order by created desc limit ?", (n,))]

    def _row(self, r) -> dict:
        q = dict(r)
        q["spec"], q["breakdown"], q["balances"] = json.loads(q["spec"]), json.loads(q["breakdown"]), json.loads(q["balances"] or "{}")
        if q["status"] == "awaiting payment" and time.time() > q["expires"]:
            q["status"] = "expired"
        return q

    def update(self, quote_id: str, **fields) -> None:
        sets = ", ".join(f"{k} = ?" for k in fields)
        values = [json.dumps(v) if k == "balances" else v for k, v in fields.items()]
        with self._conn() as c:
            c.execute(f"update quotes set {sets} where id = ?", (*values, quote_id))

    def add_balance(self, quote_id: str, when: str, balances: dict) -> None:
        q = self.quote(quote_id)
        self.update(quote_id, balances={**q["balances"], when: balances})

    # deposits
    def record(self, dep: dict, quote_id: str | None = None) -> dict | None:
        """Keep a deposit to the agent account (once), and match it to a quote. Returns the quote it paid.

        Matching: the given quote, or else the oldest open quote of the same payer; the deposit must be
        from that payer, for the agent account, at least the quoted amount, and arrive before the quote
        expires. A deposit pays one quote at most, and a quote is paid once."""
        if dep["user"].lower() != self.cfg["agent_account"].lower():
            raise ValueError(f"that deposit is for {dep['user']}, not the agent's account {self.cfg['agent_account']}")
        with self._lock, self._conn() as c:
            c.execute("begin immediate")
            try:
                known = c.execute("select quote_id from deposits where tx = ? and log_index = ?", (dep["tx"], dep["log_index"])).fetchone()
                if known is None:
                    note = "team wallet" if dep["from"].lower() in self.team else "customer"
                    c.execute("insert into deposits values (?,?,?,?,?,?,?,?,?)", (dep["tx"], dep["log_index"], dep["block"],
                              dep["from"].lower(), dep["user"].lower(), dep["amount"], time.time(), None, note))
                elif known["quote_id"]:
                    c.execute("commit")
                    return None  # already paid a quote: never twice
                now = time.time()
                rows = c.execute("select * from quotes where status = 'awaiting payment' and payer = ? and expires >= ? "
                                 "and pay <= ? order by created", (dep["from"].lower(), now, dep["amount"])).fetchall()
                pick = next((r for r in rows if quote_id is None or r["id"] == quote_id), None)
                if pick is None:
                    c.execute("commit")
                    return None
                c.execute("update quotes set status = 'paid', tx = ? where id = ?", (dep["tx"], pick["id"]))
                c.execute("update deposits set quote_id = ? where tx = ? and log_index = ?", (pick["id"], dep["tx"], dep["log_index"]))
                c.execute("commit")
            except BaseException:
                c.execute("rollback")
                raise
        return self.quote(pick["id"])

    def deposits(self, n: int = 100) -> list[dict]:
        with self._conn() as c:
            return [dict(r) for r in c.execute("select * from deposits order by block desc, log_index desc limit ?", (n,))]

    def confirm(self, chain, quote_id: str, tx: str) -> dict:
        """Check the customer's transaction on chain and, if it paid this quote, mark it paid."""
        q = self.quote(quote_id)
        if q is None:
            raise ValueError(f"no quote {quote_id}")
        receipt = chain._call("eth_getTransactionReceipt", [tx])
        if receipt is None:
            return {"state": "pending", "quote": q}  # not mined yet: ask again
        if int(receipt["status"], 16) != 1:
            self.update(quote_id, note=f"transaction {tx} failed on chain")
            return {"state": "failed", "quote": self.quote(quote_id)}
        deps = [decode_deposit(log) for log in receipt["logs"]
                if log["address"].lower() == self.cfg["payment_layer"].lower() and log["topics"][0] == DEPOSITED]
        mine = [d for d in deps if d["user"].lower() == self.cfg["agent_account"].lower()]
        if not mine:
            return {"state": "not a payment", "quote": q,
                    "why": "the transaction made no deposit to the agent's account on the Payment Layer"}
        paid = None
        for d in mine:
            paid = self.record(d, quote_id) or paid
        q = self.quote(quote_id)
        if paid is None and q["status"] != "paid":
            d = mine[0]
            why = (f"paid by {d['from']}, quoted for {q['payer']}" if d["from"].lower() != q["payer"]
                   else f"{d['amount'] / NEURON:.6f} 0G is less than the quoted {q['pay'] / NEURON:.6f}" if d["amount"] < q["pay"]
                   else "the quote expired before the payment" if q["status"] == "expired" else "already used")
            return {"state": "not matched", "quote": q, "why": why}
        return {"state": "paid", "quote": q}

    def scan(self, chain, start: int | None = None) -> list[dict]:
        """Read new deposits to the agent account since the last scan; match each. Returns the quotes paid."""
        head = chain.head()
        with self._conn() as c:
            row = c.execute("select v from cursor where k = 'block'").fetchone()
        frm = row["v"] + 1 if row else (start if start is not None else head - 2_000)
        paid = []
        user = "0x" + _pad(self.cfg["agent_account"])
        b = frm
        while b <= head:
            to = min(b + CHUNK - 1, head)
            for log in chain.logs([DEPOSITED, None, user], b, to, address=self.cfg["payment_layer"]):
                q = self.record(decode_deposit(log))
                if q:
                    paid.append(q)
            with self._conn() as c:
                c.execute("insert or replace into cursor values ('block', ?)", (to,))
            b = to + 1
        return paid


# ---- the desk: quote, confirm, run, and the evidence at each step -------------------------------
class Desk:
    """Ties a payment to its job. One per server process: it owns the job worker and the deposit watcher."""

    def __init__(self, payments: Payments | None = None, *, chain=None, registry=None, guard=None, queue=None, agent=None):
        from synapse0g import service, spend
        from synapse0g.funding import Chain
        from synapse0g.registry import load_registry

        self.pay = payments or Payments()
        self.chain = chain or Chain(self.pay.cfg["network"]["rpc"])
        self.registry = registry or load_registry()
        self.guard = guard or spend.guard_for(self.registry.providers["zerog-agent"])
        self.queue = queue or service.Queue()
        self.agent = agent or service.Agent(self.queue, service.Ledger(), registry=self.registry, guard=self.guard)
        self._work = threading.Lock()

    def balances(self) -> dict:
        """The agent account, read now: its Payment Layer balance (from the contract) and what 0G admits."""
        out = {"at": time.time()}
        try:
            out["vault_0g"] = vault_balance(self.chain, self.pay.cfg) / NEURON
        except Exception as e:  # shown, never guessed
            out["vault_error"] = f"{type(e).__name__}: {e}"[:200]
        try:
            self.guard._funds = None  # a fresh read, not the cached one
            out.update(self.guard.available())
        except Exception as e:
            out["router_error"] = f"{type(e).__name__}: {e}"[:200]
        return out

    def quote(self, payer: str, task: str | None = None) -> dict:
        from synapse0g import service

        now = self.balances()
        free = int((now.get("free_0g") or 0) * NEURON)
        b = price(self.registry, free_neuron=free, compiled="app" in service._PAID)
        cap = max(b["need"], b["pay"])
        # the key's own limit must cover the job, or the job would stop part way: refuse before payment
        room = self.guard.read_key_room() if self.guard.read_key_room else None
        b["key_room"] = room
        if room is not None and room < b["need"]:
            raise ValueError(f"the agent's Router key has {room / NEURON:.4f} 0G of its limit left and this job needs up to "
                             f"{b['need'] / NEURON:.4f}: raise the key's credit limit on pc.0g.ai before paying")
        spec = {"agent": "supplier", "task": task, "cap_0g": cap / NEURON}
        q = self.pay.new_quote(payer, spec, b, now)
        q["transaction"] = self._transaction(q)
        return q

    def _transaction(self, q: dict) -> dict:
        """The customer's transaction, exactly: to the Payment Layer, depositFor(agent account), the quoted value."""
        return {"to": self.pay.cfg["payment_layer"], "value_wei": str(q["pay"]), "data": calldata(self.pay.cfg["agent_account"]),
                "chain_id": self.pay.cfg["network"]["chain_id"]}

    def confirm(self, quote_id: str, tx: str) -> dict:
        out = self.pay.confirm(self.chain, quote_id, tx)
        if out["state"] == "paid":
            self._paid(out["quote"])
            out["quote"] = self.pay.quote(quote_id)
        return out

    def _worst_call(self) -> int:
        from synapse0g import service

        return price(self.registry, free_neuron=0, compiled="app" in service._PAID)["worst_call"]

    def resume(self, quote_id: str) -> dict:
        """Run a paid job again that stopped for funds or a cap, on the credit already in the account.
        No new payment: its cap still counts what it spent before."""
        q = self.pay.quote(quote_id)
        if q is None:
            raise ValueError(f"no quote {quote_id}")
        if q["status"] not in ("waiting for funds", "failed") or not q["job_id"]:
            raise ValueError(f"quote {quote_id} is {q['status']}: only a paid job that stopped can be resumed")
        room = self.guard.read_key_room() if self.guard.read_key_room else None
        if room is not None and room < self._worst_call():
            raise ValueError(f"the agent's Router key has {room / NEURON:.4f} 0G of its limit left, less than one call can "
                             "cost: raise the key's credit limit on pc.0g.ai, then resume")
        self.pay.update(quote_id, status="running", note="resumed on the credit already paid")
        threading.Thread(target=self._run, args=(quote_id, q["job_id"]), daemon=True).start()
        return self.pay.quote(quote_id)

    def watch_once(self) -> int:
        paid = self.pay.scan(self.chain)
        for q in paid:
            self._paid(q)
        return len(paid)

    def _paid(self, q: dict) -> None:
        from synapse0g import service

        if q.get("job_id"):
            return
        self.pay.add_balance(q["id"], "at_payment", self.balances())
        job = service.submit(self.queue, "run", {**q["spec"], "quote": q["id"], "payment_tx": q["tx"]})
        self.pay.update(q["id"], job_id=job.id, status="running")
        threading.Thread(target=self._run, args=(q["id"], job.id), daemon=True).start()

    def _run(self, quote_id: str, job_id: str) -> None:
        with self._work:  # one paid job at a time; each job runs two calls at a time
            job = self.queue.get(job_id)
            done = self.agent._run(job, "paid")
            self.pay.update(quote_id, status={"done": "done", "waiting": "waiting for funds"}.get(done.status, done.status),
                            note=done.note or "")
            self.pay.add_balance(quote_id, "after_job", self.balances())
            self._keep_progress(job_id)

    def _keep_progress(self, job_id: str) -> None:
        """A job's live events, kept on disk once it stops, so the page shows them after a restart."""
        from synapse0g import service

        run = service.PROGRESS.get(job_id)
        if run is not None:
            folder = self.pay.db.parent / "progress"
            folder.mkdir(parents=True, exist_ok=True)
            (folder / f"{job_id}.json").write_text(json.dumps(run.events, default=str), encoding="utf-8")

    def view(self, quote_id: str, since: int = 0) -> dict:
        from synapse0g import service

        q = self.pay.quote(quote_id)
        if q is None:
            raise KeyError(quote_id)
        job = self.queue.get(q["job_id"]) if q["job_id"] else None
        progress = service.PROGRESS.get(q["job_id"]) if q["job_id"] else None
        if progress is not None:
            events = progress.since(since)
        else:
            kept = self.pay.db.parent / "progress" / f"{q['job_id']}.json"
            events = {"events": json.loads(kept.read_text(encoding="utf-8"))[since:], "done": True} if q["job_id"] and kept.exists() else None
        holds = self.guard.holds_of(q["job_id"]) if q["job_id"] else []
        return {"quote": q, "job": job.__dict__ if job else None, "progress": events, "transaction": self._transaction(q),
                "ledger": [{"hold": h["id"], "model": h["model"], "state": h["state"], "request_id": h["request_id"],
                            "reserved_0g": h["max_neuron"] / NEURON, "charged_0g": None if h["actual_neuron"] is None else h["actual_neuron"] / NEURON,
                            "at": h["created"], "note": h["note"]} for h in holds],
                "explorer": self.pay.cfg["network"]["explorer"]}

    def history(self) -> dict:
        quotes = {q["id"]: q for q in self.pay.quotes(100)}
        rows = []
        for d in self.pay.deposits(100):
            q = quotes.get(d["quote_id"]) if d["quote_id"] else None
            job = self.queue.get(q["job_id"]) if q and q["job_id"] else None
            res = (job.result or {}) if job else {}
            rows.append({**d, "amount_0g": d["amount"] / NEURON, "quote": q and {k: q[k] for k in ("id", "status", "pay", "job_id")},
                         "job": job and {"id": job.id, "status": job.status, "billed_0g": res.get("billed_0g"),
                                         "calls": len(res.get("calls") or []), "passed": res.get("passed")}})
        return {"deposits": rows, "quotes": list(quotes.values())[:30], "account": self.pay.cfg["agent_account"],
                "payment_layer": self.pay.cfg["payment_layer"], "explorer": self.pay.cfg["network"]["explorer"]}

    def watch_forever(self, every_s: float = 15.0) -> None:
        while True:
            try:
                self.watch_once()
            except Exception as e:  # a failed read is printed and tried again; payments are never dropped
                print(f"payments: watch failed, retrying: {type(e).__name__}: {e}")
            time.sleep(every_s)
