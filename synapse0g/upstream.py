"""Calls to the real provider, and what each one measured.

Every number in a `CallRecord` comes from the call itself: wall-clock latency, the token counts
the provider returned, and, on 0G, the charge 0G reports for that request
(`x_0g_trace.billing.total_cost_usd_micro`). Nothing is filled in when the provider is silent.
"""

from __future__ import annotations

import http.client
import json
import os
import re
import threading
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field

from synapse0g.registry import USER_AGENT, Model, Provider


class ProviderError(RuntimeError):
    def __init__(self, message: str, status: int | None = None, body: str = "", retry_after: float | None = None):
        super().__init__(message)
        self.status = status
        self.body = body
        self.retry_after = retry_after


class BudgetExceeded(RuntimeError):
    pass


class Budget:
    """A hard cap on spend, checked before every paid call."""

    def __init__(self, cap_usd: float):
        self.cap_usd = cap_usd
        self.spent_usd = 0.0
        self.calls = 0
        self._lock = threading.Lock()

    def check(self) -> None:
        with self._lock:
            if self.spent_usd >= self.cap_usd:
                raise BudgetExceeded(
                    f"spend cap reached: ${self.spent_usd:.4f} of ${self.cap_usd:.2f}. Raise SYNAPSE0G_SPEND_CAP_USD to continue."
                )

    def add(self, usd: float | None) -> None:
        with self._lock:
            self.calls += 1
            self.spent_usd += usd or 0.0


BUDGET = Budget(float(os.environ.get("SYNAPSE0G_SPEND_CAP_USD", "4.0")))


@dataclass
class CallRecord:
    session: str
    step: str
    step_label: str
    model_id: str
    tag: str
    provider: str
    started_at: float
    latency_s: float
    status: str  # ok | reused | error
    tokens_in: int | None = None
    tokens_out: int | None = None
    reasoning_tokens: int | None = None
    cached_tokens: int | None = None
    listed_cost_usd: float | None = None  # measured tokens x the provider's listed price
    billed_usd: float | None = None  # what the provider says it charged (0G only)
    billed_0g: float | None = None  # the same charge in 0G, when the account bills in 0G
    hold_id: str | None = None
    sent_at: float | None = None  # when the last attempt was sent (guarded calls; started_at includes queueing)  # the spending reservation this call went through (guarded providers)
    request_id: str | None = None
    provider_address: str | None = None
    finish_reason: str | None = None
    retries: int = 0
    waited_s: float = 0.0
    techniques: list[str] = field(default_factory=list)
    request_hash: str = ""
    old_tool_chars: int = 0  # characters in tool results older than the last two (compaction can trim)
    tool_calls: list[str] = field(default_factory=list)
    error: str | None = None

    @property
    def cost_usd(self) -> float | None:
        """The cost we report: 0G's own charge when it gave one, else listed price x tokens."""
        return self.billed_usd if self.billed_usd is not None else self.listed_cost_usd

    def as_dict(self) -> dict:
        d = asdict(self)
        d["cost_usd"] = self.cost_usd
        return d


def _retry_after(headers, text: str) -> float | None:
    """How long the provider asked us to wait: the Retry-After header, or Groq's 'try again in 1.5s'."""
    value = headers.get("Retry-After") if headers else None
    if value:
        try:
            return float(value)
        except ValueError:
            pass
    m = re.search(r"try again in (?:(\d+)m)?([\d.]+)(ms|s)", text or "")
    if m:
        minutes = int(m.group(1) or 0)
        amount = float(m.group(2)) / (1000 if m.group(3) == "ms" else 1)
        return minutes * 60 + amount
    return None


# 0G's answers that more retrying cannot fix: no funds (402), a cap (403), the key's own limit (429)
_SPEND_CODES = ("insufficient_balance", "pay_no_balance", "pay_insufficient_spendable", "pay_debt_threshold",
                "pay_account_cap", "pay_service_cap", "credit_limit_exceeded")


def _spend_refusal(e: ProviderError) -> bool:
    return e.status == 402 or (e.status in (403, 429) and any(code in (e.body or "") for code in _SPEND_CODES))


_turn: dict[str, int] = {}
_turn_lock = threading.Lock()


def _next_key(provider: Provider) -> tuple[int, str]:
    """Round-robin over the provider's keys, so load spreads before any key is throttled."""
    keys = provider.keys()
    with _turn_lock:
        i = _turn.get(provider.name, 0) % len(keys)
        _turn[provider.name] = i + 1
    return i, keys[i]


def _post(provider: Provider, body: dict, timeout: float, key: str | None = None, extra: dict | None = None) -> tuple[dict, dict]:
    req = urllib.request.Request(
        provider.base_url + "/chat/completions",
        data=json.dumps(body).encode(),
        headers={
            "Authorization": "Bearer " + (key or provider.key()),
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
            **(extra or {}),
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.load(r), dict(r.headers)
    except urllib.error.HTTPError as e:
        text = e.read().decode("utf-8", "replace")[:600]
        raise ProviderError(f"{provider.name} HTTP {e.code}: {text}", e.code, text, _retry_after(e.headers, text)) from None
    except (urllib.error.URLError, TimeoutError, ConnectionError, http.client.HTTPException, json.JSONDecodeError) as e:
        # includes a reply cut off mid-transfer (IncompleteRead) or one that is not JSON
        raise ProviderError(f"{provider.name} unreachable or sent a broken reply: {type(e).__name__}: {e}") from None


def call(provider: Provider, model: Model, body: dict, record: CallRecord, *, attempts: int = 5, rate_limit_attempts: int = 12) -> dict:
    """Send one chat request. Retries 429 and 5xx with back-off; the wait is recorded, not hidden.

    A 429 waits as long as the provider asks (up to a minute) and is retried more patiently than a
    server error: being throttled is not a broken agent, and must not turn into a crashed task.
    """
    BUDGET.check()
    from synapse0g import spend

    guard = spend.guard_for(provider)
    if guard is not None:
        # the agent's own account: a bounded output, few retries, and every attempt reserved first
        if not (body.get("max_tokens") or body.get("max_completion_tokens")):
            body = {**body, "max_tokens": provider.max_tokens or 2048}
        attempts, rate_limit_attempts = min(attempts, 3), min(rate_limit_attempts, 4)
    timeout = float(os.environ.get("SYNAPSE0G_TIMEOUT_S", "120"))
    t0 = time.perf_counter()
    attempt = 0
    while True:
        try:
            _, key = _next_key(provider)
            if guard is None:
                data, headers = _post(provider, body, timeout, key)
            else:
                def send(extra, k=key):
                    record.sent_at = time.time()  # after the slot and the reservation: when the request left
                    return _post(provider, body, timeout, k, extra)

                data, headers, hold = guard.call(model, body, send)
                record.hold_id = hold.id
            break
        except (spend.LowFunds, spend.CapReached) as e:
            record.latency_s = time.perf_counter() - t0
            record.status, record.error = "error", f"{type(e).__name__}: {e}"
            raise
        except ProviderError as e:
            if guard is not None and _spend_refusal(e):
                record.latency_s = time.perf_counter() - t0
                record.status, record.error = "error", str(e)
                raise (spend.CapReached if e.status in (403, 429) else spend.LowFunds)(str(e)) from None
            limit = rate_limit_attempts if e.status == 429 else attempts
            retryable = e.status in (429, 500, 502, 503, 504) or e.status is None
            if not retryable or attempt >= limit - 1:
                record.latency_s = time.perf_counter() - t0
                record.status = "error"
                record.error = str(e)
                raise
            if e.status == 429 and len(provider.keys()) > 1 and attempt % len(provider.keys()) < len(provider.keys()) - 1:
                wait = 0.0  # another key may have room: try it before waiting
            elif e.status == 429 and e.retry_after is not None:
                wait = min(e.retry_after + 0.5, 60.0)
            else:
                wait = min(2**attempt, 20)
            attempt += 1
            record.retries += 1
            record.waited_s += wait
            time.sleep(wait)
    record.latency_s = time.perf_counter() - t0
    record.status = "ok"

    usage = data.get("usage") or {}
    record.tokens_in = usage.get("prompt_tokens")
    record.tokens_out = usage.get("completion_tokens")
    record.reasoning_tokens = (usage.get("completion_tokens_details") or {}).get("reasoning_tokens")
    record.cached_tokens = (usage.get("prompt_tokens_details") or {}).get("cached_tokens")
    record.listed_cost_usd = model.cost(record.tokens_in, record.tokens_out)
    trace = data.get("x_0g_trace") or {}
    billing = trace.get("billing") or {}
    if billing.get("total_cost_usd_micro") is not None:
        record.billed_usd = int(billing["total_cost_usd_micro"]) / 1e6
    if billing.get("currency") == "0g" and billing.get("total_cost") is not None:
        record.billed_0g = int(billing["total_cost"]) / 1e18
    record.request_id = trace.get("request_id") or headers.get("X-Request-Id") or data.get("id")
    record.provider_address = trace.get("provider")
    choice = (data.get("choices") or [{}])[0]
    record.finish_reason = choice.get("finish_reason")
    record.tool_calls = [
        (c.get("function") or {}).get("name", "?") for c in (choice.get("message") or {}).get("tool_calls") or []
    ]
    BUDGET.add(record.cost_usd)
    return data
