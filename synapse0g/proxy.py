"""An OpenAI-compatible endpoint that optimizes every model call passing through it.

Any agent that speaks the OpenAI chat API (LangGraph, CrewAI, the OpenAI SDK, raw HTTP) uses it
by changing two settings: `base_url` to this server and `api_key` to a session token. The real
provider keys stay in this process; the agent never sees them.

The session token says which run a call belongs to and which policy applies, so a benchmark can
run many tasks at once and still attribute every token to the right task.
"""

from __future__ import annotations

import json
import threading
import time
import traceback
import uuid
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from synapse0g import effects, upstream
from synapse0g.optimizer import Policy, old_tool_chars, prepare, request_hash, step_of
from synapse0g.registry import Registry


@dataclass
class Session:
    id: str
    policy: Policy
    records: list[upstream.CallRecord] = field(default_factory=list)
    reuse_cache: dict[str, dict] = field(default_factory=dict)
    step_labels: dict[str, str] = field(default_factory=dict)
    tool_effects: dict[str, tuple[str, str]] = field(default_factory=dict)


class Engine:
    def __init__(self, registry: Registry, labels: dict[str, str] | None = None):
        self.registry = registry
        self.labels = labels or {}
        self.sessions: dict[str, Session] = {}
        self._lock = threading.Lock()

    def open(self, policy: Policy | None = None, session_id: str | None = None) -> Session:
        s = Session(session_id or "run-" + uuid.uuid4().hex[:12], policy or Policy.empty())
        with self._lock:
            self.sessions[s.id] = s
        return s

    def close(self, session_id: str) -> Session:
        with self._lock:
            return self.sessions.pop(session_id)

    def complete(self, session: Session, body: dict) -> dict:
        if body.get("stream"):
            raise ValueError("streaming responses are not supported yet; call without stream=true")
        if body.get("model") not in self.registry.models:
            raise KeyError(f"unknown model {body.get('model')!r}; use one of {', '.join(self.registry.models)}")
        step, label = step_of(body)
        session.step_labels[step] = label
        session.tool_effects.update(effects.tool_effects(body.get("tools"), self.labels))
        sp = session.policy.for_step(step)
        sent, model, applied = prepare(body, sp, self.registry)
        key = request_hash(sent)
        record = upstream.CallRecord(
            session=session.id,
            step=step,
            step_label=label,
            model_id=model.id,
            tag=model.tag,
            provider=model.provider,
            started_at=time.time(),
            latency_s=0.0,
            status="pending",
            techniques=applied,
            request_hash=key,
            old_tool_chars=old_tool_chars(body.get("messages") or []),
        )
        if sp.reuse and key in session.reuse_cache:
            record.status = "reused"
            record.techniques = applied + ["reuse"]
            record.tokens_in = record.tokens_out = 0
            record.listed_cost_usd = 0.0
            session.records.append(record)
            return session.reuse_cache[key]
        provider = self.registry.providers[model.provider]
        try:
            data = upstream.call(provider, model, sent, record)
        finally:
            session.records.append(record)
        if sp.reuse and self._reusable(session, data):
            session.reuse_cache[key] = data
        return data

    def _reusable(self, session: Session, data: dict) -> bool:
        """Only a response that asks for nothing, or only for read tools, may be served again."""
        message = ((data.get("choices") or [{}])[0]).get("message") or {}
        for c in message.get("tool_calls") or []:
            name = (c.get("function") or {}).get("name", "?")
            effect, _ = session.tool_effects.get(name, ("irreversible", "unknown"))
            if effect not in ("pure", "read"):
                return False
        return True


def _handler(engine: Engine):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):  # quiet: records carry everything worth seeing
            pass

        def _send(self, status: int, payload: dict) -> None:
            raw = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def _session(self) -> Session | None:
            token = (self.headers.get("Authorization") or "").removeprefix("Bearer ").strip()
            return engine.sessions.get(token)

        def do_GET(self):
            if self.path.rstrip("/").endswith("/models"):
                data = [{"id": m, "object": "model", "owned_by": "synapse0g"} for m in engine.registry.models]
                return self._send(200, {"object": "list", "data": data})
            return self._send(404, {"error": {"message": "not found"}})

        def do_POST(self):
            if not self.path.rstrip("/").endswith("/chat/completions"):
                return self._send(404, {"error": {"message": f"no route {self.path}"}})
            session = self._session()
            if session is None:
                return self._send(401, {"error": {"message": "unknown session token", "type": "auth"}})
            length = int(self.headers.get("Content-Length") or 0)
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
                return self._send(200, engine.complete(session, body))
            except upstream.BudgetExceeded as e:
                return self._send(402, {"error": {"message": str(e), "type": "budget"}})
            except upstream.ProviderError as e:
                return self._send(502, {"error": {"message": str(e), "type": "provider"}})
            except (KeyError, ValueError) as e:
                return self._send(400, {"error": {"message": str(e), "type": "invalid_request"}})
            except Exception as e:  # never drop the connection silently: the agent gets an error and we print it
                traceback.print_exc()
                return self._send(500, {"error": {"message": f"synapse0g internal error: {type(e).__name__}: {e}", "type": "internal"}})

    return Handler


class Proxy:
    """Run the endpoint on a local port in a background thread."""

    def __init__(self, engine: Engine, host: str = "127.0.0.1", port: int = 0):
        self.engine = engine
        self.server = ThreadingHTTPServer((host, port), _handler(engine))
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def base_url(self) -> str:
        host, port = self.server.server_address[:2]
        return f"http://{host}:{port}/v1"

    def __enter__(self) -> Proxy:
        self.thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self.server.shutdown()
        self.server.server_close()
