"""The drop-in client. Change one import and every model call goes through synapse0g.

    # from openai import OpenAI
    from synapse0g.openai import OpenAI

    client = OpenAI()      # as before; `model=` is a models.yaml id, such as "zg-main"

Each call goes to the synapse0g endpoint of the run it belongs to. Inside `synapse0g.compile(...)`
runs, that is the run's own session, so its calls get the run's per-step policy and are measured
per task, even when tasks and steps run at the same time. Outside any run, calls go to a local
endpoint that applies nothing and only records (`observed()`), so an app keeps working unchanged.

The real provider keys stay in the endpoint; the client only ever holds a session token.
"""

from __future__ import annotations

import contextlib
import contextvars
import threading
from collections.abc import Iterator
from typing import Any

import openai as _openai

_route: contextvars.ContextVar[tuple[str, str] | None] = contextvars.ContextVar("synapse0g_route", default=None)
_observer: dict[str, Any] = {}
_observer_lock = threading.Lock()


@contextlib.contextmanager
def routed(base_url: str, token: str) -> Iterator[None]:
    """Send this context's calls to `base_url` with session `token`. Threads started with a copy
    of the context (asyncio.to_thread, LangGraph's own pool) inherit it."""
    reset = _route.set((base_url, token))
    try:
        yield
    finally:
        _route.reset(reset)


def _observing_route() -> tuple[str, str]:
    """A local endpoint with no policy, started on first use: the app runs as it always did."""
    with _observer_lock:
        if not _observer:
            from synapse0g.proxy import Engine, Proxy
            from synapse0g.registry import load_env, load_registry

            load_env()
            engine = Engine(load_registry())
            proxy = Proxy(engine).__enter__()
            _observer.update(engine=engine, proxy=proxy, session=engine.open(session_id="observed"))
        return _observer["proxy"].base_url, _observer["session"].id


def observed() -> list[dict]:
    """Calls made outside any synapse0g run, with what each one measured."""
    with _observer_lock:
        session = _observer.get("session")
        return [r.as_dict() for r in session.records] if session else []


class _Completions:
    def __init__(self, owner: OpenAI):
        self._owner = owner

    def create(self, **kwargs: Any) -> Any:
        return self._owner._client().chat.completions.create(**kwargs)


class _Chat:
    def __init__(self, owner: OpenAI):
        self.completions = _Completions(owner)


class OpenAI:  # it stands in for the class of this name
    """`openai.OpenAI`, with base_url and api_key chosen per call by the run it is in."""

    def __init__(self, *, timeout: float = 300.0, **_ignored: Any):
        # base_url and api_key are ignored on purpose: the run decides where a call goes
        self._timeout = timeout
        self._clients: dict[tuple[str, str], _openai.OpenAI] = {}
        self._lock = threading.Lock()
        self.chat = _Chat(self)

    def _client(self) -> _openai.OpenAI:
        route = _route.get() or _observing_route()
        with self._lock:
            client = self._clients.get(route)
            if client is None:
                client = _openai.OpenAI(base_url=route[0], api_key=route[1], max_retries=0, timeout=self._timeout)
                self._clients[route] = client
            return client
