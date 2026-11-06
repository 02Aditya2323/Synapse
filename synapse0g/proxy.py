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
from dataclasses import dataclass, field, replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from synapse0g import effects, spend, upstream
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
    # model ids the agent asks for -> the ids that pay for them (e.g. zg-main -> agent-main: the same
    # model, billed to the agent's own account). Empty: every call is paid as asked.
    pay_from: dict[str, str] = field(default_factory=dict)
    # a compile's trace, kept on disk: the same request is answered from it, at no cost, so compiling the
    # same app again (after a restart) pays nothing. Only for tracing, where the answer is used to learn
    # what each step reads and writes; never for a run whose answers are the product.
    replay_path: str | None = None
    replay: dict | None = None


class Engine:
    def __init__(self, registry: Registry, labels: dict[str, str] | None = None):
        self.registry = registry
        self.labels = labels or {}
        self.sessions: dict[str, Session] = {}
        self._lock = threading.Lock()

    def open(self, policy: Policy | None = None, session_id: str | None = None, pay_from: dict[str, str] | None = None,
             replay_path: str | None = None) -> Session:
        policy = policy or Policy.empty()
        if pay_from:
            for asked, paid in pay_from.items():
                if self.registry.model(asked).tag != self.registry.model(paid).tag:
                    raise ValueError(f"{asked} -> {paid} would change the model, not only who pays for it")
            policy = Policy({k: replace(sp, route_to=pay_from.get(sp.route_to, sp.route_to)) for k, sp in policy.steps.items()})
        s = Session(session_id or "run-" + uuid.uuid4().hex[:12], policy, pay_from=dict(pay_from or {}))
        if replay_path:
            s.replay_path = str(replay_path)
            path = Path(replay_path)
            s.replay = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
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
        # the step is named by the call as the agent wrote it, before it is billed to another account, so a
        # policy found on one account applies on another
        step, label = step_of(body)
        if session.pay_from.get(body["model"]):
            body = {**body, "model": session.pay_from[body["model"]]}
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
        if session.replay is not None and key in session.replay:
            kept = session.replay[key]
            record.status = "replayed"
            record.techniques = applied + ["replayed trace"]
            record.tokens_in = record.tokens_out = 0
            record.listed_cost_usd = 0.0
            record.request_id = kept.get("request_id")
            session.records.append(record)
            return kept["data"]
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
        if session.replay is not None:
            session.replay[key] = {"data": data, "request_id": record.request_id, "at": time.time()}
            with self._lock:
                path = Path(session.replay_path)
                path.parent.mkdir(parents=True, exist_ok=True)
                tmp = path.with_suffix(f".{uuid.uuid4().hex[:6]}.tmp")
                tmp.write_text(json.dumps(session.replay), encoding="utf-8")
                tmp.replace(path)
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
            except spend.LowFunds as e:  # the agent's account cannot cover the call now: not a crash
                return self._send(402, {"error": {"message": str(e), "type": "low_funds"}})
            except spend.CapReached as e:
                return self._send(402, {"error": {"message": str(e), "type": "spend_cap"}})
            except upstream.ProviderError as e:
                return self._send(502, {"error": {"message": str(e), "type": "provider"}})
            except (KeyError, ValueError) as e:
                return self._send(400, {"error": {"message": str(e), "type": "invalid_request"}})
            except Exception as e:  # never drop the connection silently: the agent gets an error and we print it
                traceback.print_exc()
                return self._send(500, {"error": {"message": f"synapse0g internal error: {type(e).__name__}: {e}", "type": "internal"}})

    return Handler


class _Server(ThreadingHTTPServer):
    # The default listen backlog is 5. An agent with parallel steps, run over several tasks at
    # once, opens far more connections than that, and the extra ones were refused: crashes that
    # were ours, not the provider's.
    request_queue_size = 256
    daemon_threads = True


class Proxy:
    """Run the endpoint on a local port in a background thread."""

    def __init__(self, engine: Engine, host: str = "127.0.0.1", port: int = 0):
        self.engine = engine
        self.server = _Server((host, port), _handler(engine))
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
