"""Spending from the agent's own 0G account, safely, with calls running side by side.

Every call on a guarded provider (models.yaml `guarded: true`) goes through `Guard.call`:

1. it waits for a slot (two calls at once by default), then
2. reserves its worst-case cost, atomically, against three limits at once:
   - the job's cap (what this job may spend),
   - the key's cap (the Router key's own limit, as 0G echoes it),
   - the account: what 0G would admit the request against, less what other calls have reserved
     and not yet settled. 0G refuses a request it cannot cover by at least `min_cost`;
3. sends it, then settles the reservation at what 0G says it charged (`x_0g_trace.billing`).

A call whose outcome is unknown (a timeout, a dropped connection, a 5xx, or a reply with no
charge) is not released: its worst case stays held as `uncertain` until `reconcile` matches it
against the account's usage history. A call 0G answered with a refusal (4xx) is released: it was
not billed. Reservations live in SQLite, so they survive a restart, and the database's write lock
makes them atomic across threads and processes.

Worst case = request bytes x input price + max_tokens x output price: one token per byte sent is an
upper bound on input tokens, and the output cannot exceed max_tokens, which every guarded call
carries (the provider's `max_tokens` is set when the call names none).

Errors are typed, so the paid agent slows down or pauses rather than crashing:
`LowFunds` (the account cannot cover the call), `CapReached` (the job or key cap is spent).
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
import uuid
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from synapse0g.registry import ROOT

NEURON = 10**18
DB = ROOT / "data" / "spend.db"
SYNAPSE_KEY_CAP = int(float(os.environ.get("SYNAPSE0G_AGENT_CAP_0G", "0.3")) * 10**18)  # used when the key has no 0G limit
MIN_COST = 10**16  # 0.01 0G: the least 0G admits a request against (Router reference, mainnet)
VAULT_RATIO = 0.8  # the share of the Payment Layer vault 0G counts toward admission (mainnet)


class LowFunds(RuntimeError):
    """The account cannot cover this call now. Wait for credit; nothing is wrong with the job."""


class CapReached(RuntimeError):
    """The job's cap or the key's cap is spent."""


@dataclass
class Hold:
    id: str
    job: str | None
    model: str
    max_neuron: int


def worst_case(model, body: dict) -> int:
    """The most this request can cost, in neuron. Refuses a request it cannot bound."""
    if model.neuron_in is None or model.neuron_out is None:
        raise CapReached(f"{model.id} has no 0G price in the catalog, so its cost cannot be bounded")
    max_out = body.get("max_tokens") or body.get("max_completion_tokens")
    if not max_out:
        raise CapReached("a guarded call must name max_tokens")
    return len(json.dumps(body).encode()) * model.neuron_in + int(max_out) * model.neuron_out


def admission(funds: dict, vault_ratio: float = VAULT_RATIO) -> int:
    """What 0G admits requests against, from /v1/account/funds: the Router ledger plus a share of the vault."""
    router = funds["deposit"] + funds["credit"] - funds["pending_charge"]
    return router + int(funds["payment_layer"] * vault_ratio)


class Guard:
    def __init__(self, db: Path = DB, *, funds: Callable[[], dict] | None = None,
                 key_room: Callable[[], int | None] | None = None, concurrency: int | None = None,
                 min_cost: int = MIN_COST, funds_ttl_s: float = 10.0):
        self.db = Path(db)
        self.db.parent.mkdir(parents=True, exist_ok=True)
        self.read_funds, self.read_key_room = funds, key_room
        self.min_cost, self.funds_ttl_s = min_cost, funds_ttl_s
        self.slots = threading.BoundedSemaphore(concurrency or int(os.environ.get("SYNAPSE0G_AGENT_CONCURRENCY", "2")))
        self._lock = threading.Lock()
        self._funds: tuple[float, int] | None = None  # (read at, admission figure)
        self.job: str | None = None
        self.job_cap: int | None = None
        self.refusals: list[tuple[str | None, str]] = []  # (job, why): what made a job stop, for its status
        self._flight = threading.Condition()
        self._inflight = 0  # this guard's calls sent and not yet settled
        with self._conn() as c:
            c.execute("""create table if not exists holds (
                id text primary key, job text, model text, max_neuron integer, state text,
                actual_neuron integer, request_id text, created real, settled real, note text)""")

    def _conn(self):
        c = sqlite3.connect(self.db, timeout=30, isolation_level=None)
        c.execute("pragma journal_mode=wal")
        return c

    # ---- the job this guard spends for ---------------------------------------------------------
    @contextmanager
    def for_job(self, job: str, cap_neuron: int):
        self.job, self.job_cap = job, cap_neuron
        try:
            yield self
        finally:
            self.job, self.job_cap = None, None

    # ---- reading what is available -------------------------------------------------------------
    def _admission(self) -> int | None:
        if self.read_funds is None:
            return None
        now = time.time()
        if self._funds is None or now - self._funds[0] > self.funds_ttl_s:
            self._funds = (now, admission(self.read_funds()))
        return self._funds[1]

    def totals(self, job: str | None = None) -> dict:
        with self._conn() as c:
            q = "select state, coalesce(sum(max_neuron),0), coalesce(sum(actual_neuron),0), count(*) from holds"
            rows = c.execute(q + (" where job = ?" if job else "") + " group by state", (job,) if job else ()).fetchall()
        out = {s: {"held": h, "actual": a, "n": n} for s, h, a, n in rows}
        return {"reserved": out.get("reserved", {}).get("held", 0), "uncertain": out.get("uncertain", {}).get("held", 0),
                "settled": out.get("settled", {}).get("actual", 0), "calls_settled": out.get("settled", {}).get("n", 0),
                "released": out.get("released", {}).get("n", 0)}

    # ---- the reservation ----------------------------------------------------------------------
    def reserve(self, model: str, max_neuron: int) -> Hold:
        admit = self._admission()  # read outside the write lock: it is a network call
        key_room = self.read_key_room() if self.read_key_room else None
        with self._lock, self._conn() as c:
            c.execute("begin immediate")  # the write lock: no other reservation is decided meanwhile
            try:
                held = c.execute("select coalesce(sum(max_neuron),0) from holds where state in ('reserved','uncertain')").fetchone()[0]
                since = self._funds[0] if self._funds else 0
                # settled after the funds were read: 0G may not show them yet, so they are taken off too
                recent = c.execute("select coalesce(sum(actual_neuron),0) from holds where state='settled' and settled > ?",
                                   (since,)).fetchone()[0]
                if self.job is not None and self.job_cap is not None:
                    used = c.execute("""select coalesce(sum(case when state='settled' then actual_neuron else max_neuron end),0)
                                        from holds where job = ? and state in ('reserved','uncertain','settled')""",
                                     (self.job,)).fetchone()[0]
                    if used + max_neuron > self.job_cap:
                        raise CapReached(f"job {self.job}: {(used + max_neuron) / NEURON:.6f} 0G would pass its cap of "
                                         f"{self.job_cap / NEURON:.6f} 0G")
                if key_room is not None and held + max_neuron > key_room:
                    raise CapReached(f"the key's cap: {key_room / NEURON:.6f} 0G left on the Router key, {held / NEURON:.6f} held, "
                                     f"this call may cost {max_neuron / NEURON:.6f}")
                if admit is not None:
                    free = admit - held - recent
                    need = max(max_neuron, self.min_cost)
                    if free < need:
                        raise LowFunds(f"the account can cover {max(free, 0) / NEURON:.6f} 0G now; this call needs "
                                       f"{need / NEURON:.6f} (0G admits nothing under {self.min_cost / NEURON:.2f})")
                hold = Hold("hold-" + uuid.uuid4().hex[:12], self.job, model, max_neuron)
                c.execute("insert into holds values (?,?,?,?, 'reserved', null, null, ?, null, '')",
                          (hold.id, hold.job, model, max_neuron, time.time()))
                c.execute("commit")
                return hold
            except BaseException:
                c.execute("rollback")
                raise

    def settle(self, hold: Hold, actual_neuron: int, request_id: str | None) -> None:
        self._set(hold.id, "settled", actual_neuron, request_id, "billed")

    def release(self, hold: Hold, why: str) -> None:
        self._set(hold.id, "released", 0, None, why)

    def uncertain(self, hold: Hold, why: str, request_id: str | None = None) -> None:
        self._set(hold.id, "uncertain", None, request_id, why)

    def _set(self, hold_id, state, actual, request_id, note):
        with self._conn() as c:
            c.execute("update holds set state=?, actual_neuron=?, request_id=coalesce(?, request_id), settled=?, note=? where id=?",
                      (state, actual, request_id, time.time(), note, hold_id))

    def open_holds(self) -> list[dict]:
        with self._conn() as c:
            c.row_factory = sqlite3.Row
            return [dict(r) for r in c.execute("select * from holds where state in ('reserved','uncertain') order by created")]

    def holds_of(self, job: str) -> list[dict]:
        """Every call a job reserved, in order: what it held, what 0G charged, the request id. The ledger's own
        record, kept across restarts."""
        with self._conn() as c:
            c.row_factory = sqlite3.Row
            return [dict(r) for r in c.execute("select * from holds where job = ? order by created", (job,))]

    def recover(self) -> int:
        """After a restart, a reservation still `reserved` belongs to a call that may have been sent:
        it becomes uncertain, held until reconciled. Returns how many."""
        with self._conn() as c:
            return c.execute("update holds set state='uncertain', note='open at restart' where state='reserved'").rowcount

    def reconcile(self, history: list[dict]) -> int:
        """Settle uncertain holds from the account's usage history: by request id where the hold has one,
        else the one unmatched record of the same model within the hold's time window. Returns how many."""
        with self._conn() as c:
            known = {r for (r,) in c.execute("select request_id from holds where request_id is not null and state='settled'")}
        free = [h for h in history if h.get("request_id") and h["request_id"] not in known]
        done = 0
        for hold in [h for h in self.open_holds() if h["state"] == "uncertain"]:
            match = next((h for h in free if h["request_id"] == hold["request_id"]), None) if hold["request_id"] else None
            if match is None:
                near = [h for h in free if h.get("model") in (None, hold["model"])
                        and h.get("at") is not None and hold["created"] - 5 <= h["at"] <= hold["created"] + 600]
                match = near[0] if len(near) == 1 else None  # two candidates: leave it held, never guess
            if match is not None:
                free.remove(match)
                self._set(hold["id"], "settled", int(match["cost_neuron"]), match["request_id"], "reconciled from usage history")
                done += 1
        return done

    # ---- one call, start to finish -------------------------------------------------------------
    def available(self) -> dict:
        """The account as the guard sees it, in 0G: what 0G would admit against, less what is held."""
        admit = self._admission()
        t = self.totals()
        held = t["reserved"] + t["uncertain"]
        return {"admission_0g": None if admit is None else admit / NEURON, "held_0g": held / NEURON,
                "free_0g": None if admit is None else max(admit - held, 0) / NEURON, "min_cost_0g": self.min_cost / NEURON,
                "spent_0g": t["settled"] / NEURON, "calls": t["calls_settled"]}

    def _reserve_or_wait(self, model: str, max_neuron: int) -> Hold:
        """Reserve; if funds or a cap refuse while this guard's other calls are still in flight, wait for
        them to settle and try again: settling frees what they held beyond their real cost. The job slows
        down instead of stopping. Only a refusal with nothing in flight stops it."""
        while True:
            try:
                hold = self.reserve(model, max_neuron)
            except (LowFunds, CapReached) as e:
                with self._flight:
                    if self._inflight == 0:
                        self.refusals.append((self.job, str(e)))
                        raise
                    self._flight.wait(timeout=120)
                continue
            with self._flight:
                self._inflight += 1
            return hold

    def _landed(self) -> None:
        with self._flight:
            self._inflight -= 1
            self._funds = None  # read the account again: what it holds has changed
            self._flight.notify_all()

    def call(self, model, body: dict, send: Callable[[dict], tuple[dict, dict]]) -> tuple[dict, dict, Hold]:
        """Reserve, send, settle. `send(headers)` posts the request and returns (reply, headers);
        it raises upstream.ProviderError on failure."""
        with self.slots:
            hold = self._reserve_or_wait(model.tag, worst_case(model, body))
            try:
                return self._send(hold, send)
            finally:
                self._landed()

    def _send(self, hold: Hold, send) -> tuple[dict, dict, Hold]:
        from synapse0g.upstream import ProviderError

        try:
            data, headers = send({"X-Client-Request-Id": hold.id})
        except ProviderError as e:
            if e.status is not None and 400 <= e.status < 500:
                self.release(hold, f"refused by 0G, not billed: HTTP {e.status}")
            else:
                self.uncertain(hold, f"outcome unknown: {e}"[:300])
            raise
        except BaseException as e:
            self.uncertain(hold, f"outcome unknown: {type(e).__name__}"[:300])
            raise
        trace = data.get("x_0g_trace") or {}
        billing = trace.get("billing") or {}
        request_id = trace.get("request_id") or headers.get("X-Request-Id") or headers.get("X-Request-ID")
        if billing.get("currency") == "0g" and billing.get("total_cost") is not None:
            self.settle(hold, int(billing["total_cost"]), request_id)
        else:
            self.uncertain(hold, "0G sent no charge in 0G with the reply", request_id)
        return data, headers, hold


GUARDS: dict[str, Guard] = {}
_guards_lock = threading.Lock()


def guard_for(provider) -> Guard | None:
    """The guard a guarded provider's calls go through. Built on first use from the account's own
    management key (ZG_MANAGEMENT_KEY, read-only): with no way to read the account, it refuses."""
    if not getattr(provider, "guarded", False):
        return None
    with _guards_lock:
        if provider.name not in GUARDS:
            GUARDS[provider.name] = _account_guard()
        return GUARDS[provider.name]


def _account_guard() -> Guard:
    from synapse0g.funding import RouterAccount

    key = os.environ.get("ZG_MANAGEMENT_KEY")
    if not key:
        raise LowFunds("the agent's account cannot be read: set ZG_MANAGEMENT_KEY (account:read, keys:read) in .env")
    account = RouterAccount(key)
    guard = Guard(funds=account.funds, key_room=lambda: key_room(account))
    guard.recover()
    return guard


def key_room(account, name: str | None = None) -> int | None:
    """What the agent's Router key may still spend in its current period, from 0G's own echo of it."""
    name = name or os.environ.get("ZG_AGENT_KEY_NAME", "synapse-agent")
    keys = [k for k in account.api_keys() if k.get("name") == name and k.get("status") in (None, "active")]
    if len(keys) != 1:
        raise CapReached(f"expected one active Router key named {name!r}, found {len(keys)}")
    k = keys[0]
    from synapse0g.funding import _neuron

    if k.get("credit_limit") in (None, ""):
        # no limit on 0G's side: synapse0g holds the key to its own cap, counted against what 0G says the key
        # has used. Weaker than 0G's own wall (it binds only this server), so 0G's limit wins when one is set.
        own = SYNAPSE_KEY_CAP
        if own <= 0:
            raise CapReached(f"the key {name!r} has no credit limit and SYNAPSE0G_AGENT_CAP_0G is 0: set one of them")
        return own - _neuron(k.get("used"))
    return _neuron(k["credit_limit"]) - _neuron(k.get("used"))


def key_cap_source(account, name: str | None = None) -> dict:
    """Who enforces the agent key's cap, for the page: 0G (the key's credit limit) or synapse0g (its own)."""
    name = name or os.environ.get("ZG_AGENT_KEY_NAME", "synapse-agent")
    from synapse0g.funding import _neuron

    k = next((k for k in account.api_keys() if k.get("name") == name and k.get("status") in (None, "active")), None)
    if k is None:
        return {"by": None, "why": f"no active key named {name!r}"}
    used = _neuron(k.get("used")) / NEURON
    if k.get("credit_limit") in (None, ""):
        return {"by": "synapse0g", "cap_0g": SYNAPSE_KEY_CAP / NEURON, "used_0g": used,
                "why": "the key has no limit on 0G's side, so synapse0g holds it to its own cap"}
    return {"by": "0G", "cap_0g": _neuron(k["credit_limit"]) / NEURON, "used_0g": used, "why": "the key's own credit limit"}
