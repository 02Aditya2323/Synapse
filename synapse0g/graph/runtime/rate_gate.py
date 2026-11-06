"""Wait before a provider says no, rather than after.

Set SYNAPSE_RPM_<PROVIDER> and SYNAPSE_TPM_<PROVIDER> to the limits for all your keys together. Off
when they are unset. Waiting up front avoids a failed call and its back-off, but the wait is real
time, so it is counted and reported instead of hidden in a slower run.
"""

from __future__ import annotations

import os
import threading
import time
from collections import deque


class RateGate:
    def __init__(self, rpm: int | None, tpm: int | None, *, clock=time.monotonic, sleep=time.sleep):
        self.rpm, self.tpm = rpm, tpm
        self.clock, self.sleep = clock, sleep
        self.requests: deque[float] = deque()
        self.tokens: deque[tuple[float, int]] = deque()
        self.waited_s = 0.0
        self.lock = threading.Lock()

    def _trim(self, now: float) -> None:
        while self.requests and now - self.requests[0] >= 60:
            self.requests.popleft()
        while self.tokens and now - self.tokens[0][0] >= 60:
            self.tokens.popleft()

    def _delay(self, now: float) -> float:
        self._trim(now)
        delays = [0.0]
        if self.rpm and len(self.requests) >= self.rpm:
            delays.append(60 - (now - self.requests[0]))
        if self.tpm and self.tokens and sum(t for _, t in self.tokens) >= self.tpm:
            delays.append(60 - (now - self.tokens[0][0]))
        return max(delays)

    def before_call(self) -> float:
        waited = 0.0
        while True:
            with self.lock:
                now = self.clock()
                delay = self._delay(now)
                if delay <= 0:
                    self.requests.append(now)
                    self.waited_s += waited
                    return waited
            self.sleep(delay)
            waited += delay

    def after_call(self, tokens: int | None) -> None:
        if tokens:
            with self.lock:
                self.tokens.append((self.clock(), tokens))


_gates: dict[str, RateGate | None] = {}


def gate_for(provider: str) -> RateGate | None:
    if provider not in _gates:
        rpm = os.environ.get(f"SYNAPSE_RPM_{provider.upper()}")
        tpm = os.environ.get(f"SYNAPSE_TPM_{provider.upper()}")
        _gates[provider] = RateGate(int(rpm) if rpm else None, int(tpm) if tpm else None) if rpm or tpm else None
    return _gates[provider]


def total_waited_s() -> float:
    return sum(g.waited_s for g in _gates.values() if g is not None)
