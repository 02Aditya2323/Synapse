"""The local endpoint takes many connections at once: parallel steps over parallel tasks."""

from __future__ import annotations

import threading
import time

from openai import OpenAI

from synapse0g import proxy
from synapse0g.registry import load_registry


def test_sixty_four_simultaneous_calls_all_get_through(monkeypatch):
    def fake(provider, model, body, record, **kw):
        time.sleep(0.5)
        record.status, record.tokens_in, record.tokens_out = "ok", 1, 1
        return {"choices": [{"message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1}}

    monkeypatch.setattr(proxy.upstream, "call", fake)
    engine = proxy.Engine(load_registry())
    errors: list[str] = []
    with proxy.Proxy(engine) as px:
        session = engine.open()

        def call():
            try:
                OpenAI(base_url=px.base_url, api_key=session.id, max_retries=0).chat.completions.create(
                    model="zg-main", messages=[{"role": "user", "content": "hi"}])
            except Exception as e:  # noqa: BLE001 - counted, and the assert below shows them
                errors.append(type(e).__name__)

        threads = [threading.Thread(target=call) for _ in range(64)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
    assert errors == []
    assert len(session.records) == 64
