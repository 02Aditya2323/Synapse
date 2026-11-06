"""`synapse0g ui`: the dashboard and its data, on one local port.

  GET /api/feed     the featured results and the run history (see showcase.py)
  GET /api/health   ok
  GET /api/samples  sample agents to paste into the code inspector
  POST /api/map     {"code": ...} or {"path": ...}: the agent's graph before and after the passes (nothing runs)
  GET /api/observed           runs recorded by synapse0g.wrap, newest first
  GET /api/observed/<run_id>  one of them: its lifted graph, the passes and the measurements
  GET /api/agent              the paid agent: balance, budget mode, queue, quotes
  GET /api/live/tasks?agent=  the live agents, and that agent's held-out tasks
  POST /api/live              {"agent": id, "task": id, "cold": bool}: start a live run (live.py)
  GET /api/live/<id>?since=n  its events from n on, as they happen
  POST /api/pay/quote         {"payer": 0x.., "task": id}: the price of a paid run, and the transaction to send
  POST /api/pay/confirm       {"quote": id, "tx": hash}: check the payment on chain; if it paid, the job starts
  GET /api/pay/<quote>?since= the quote, its payment, balances, job and the job's live progress
  GET /api/pay/history        every deposit to the agent's account, with the quote and job it paid
  GET /api/funding            the funding loop, each stage read from its source (funding.py)
  GET /api/jobs, /api/jobs/<id>   jobs and their results
  POST /api/jobs              {"kind": "inspect"|"study", "spec": {...}}: a quoted job, queued
  anything else     the built dashboard from web/dist, if it has been built
"""

from __future__ import annotations

import json
import mimetypes
import threading
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from synapse0g import showcase
from synapse0g.registry import ROOT

DIST = ROOT / "web" / "dist"


class Handler(BaseHTTPRequestHandler):
    def _send(self, status: int, body: bytes, ctype: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        try:
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass  # the page left before the answer (Back, refresh): nothing to tell it, nothing lost

    def handle_one_request(self):
        try:
            super().handle_one_request()
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _json(self, status: int, data) -> None:
        self._send(status, json.dumps(data, default=str).encode(), "application/json")

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?")[0]
        try:
            if path == "/api/health":
                return self._json(200, {"ok": True})
            if path == "/api/feed":
                return self._json(200, showcase.feed())
            if path == "/api/samples":
                from synapse0g.mapping import samples

                return self._json(200, {"samples": samples()})
            if path == "/api/observed":
                return self._json(200, {"runs": showcase.observed_runs()})
            if path == "/api/agent":
                from synapse0g import service

                agent = service.Agent(service.Queue(), service.Ledger())
                return self._json(200, {**agent.status(), "balance_source": "local ledger (stand-in until the Router account is read)",
                                        "account": _paid_account(),
                                        "quotes": {m: service.estimate("procurement", m) for m in service.SIZES},
                                        "ledger": service.Ledger().entries(20)})
            if path == "/api/live/tasks":
                from urllib.parse import parse_qs, urlparse

                from synapse0g import live

                agent = (parse_qs(urlparse(self.path).query).get("agent") or ["supplier"])[0]
                return self._json(200, {"agent": agent, "agents": live.agents(), "tasks": live.tasks(agent)})
            if path.startswith("/api/live/"):
                from urllib.parse import parse_qs, urlparse

                from synapse0g import live

                run = live.RUNS.get(path.rsplit("/", 1)[-1])
                since = int((parse_qs(urlparse(self.path).query).get("since") or ["0"])[0])
                return self._json(200, run.since(since)) if run else self._json(404, {"error": "no such live run"})
            if path == "/api/pay/history":
                return self._json(200, _desk().history())
            if path.startswith("/api/pay/") and path.endswith("/package"):
                desk = _desk()
                v = desk.view(path.split("/")[3])
                pkg = ((v.get("job") or {}).get("result") or {}).get("package")
                if not pkg:
                    return self._json(404, {"error": "no optimized agent for this payment yet: the job has not finished"})
                body = (ROOT / pkg).read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "application/zip" if pkg.endswith(".zip") else "application/json")
                self.send_header("Content-Disposition", f'attachment; filename="{Path(pkg).name}"')
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return None
            if path.startswith("/api/pay/"):
                from urllib.parse import parse_qs, urlparse

                since = int((parse_qs(urlparse(self.path).query).get("since") or ["0"])[0])
                try:
                    return self._json(200, _desk().view(path.rsplit("/", 1)[-1], since))
                except KeyError:
                    return self._json(404, {"error": "no such quote"})
            if path == "/api/funding":
                from synapse0g import funding, service

                return self._json(200, funding.snapshot(spent=service.inference_receipts()))
            if path == "/api/jobs":
                from synapse0g import service

                return self._json(200, {"jobs": [j.__dict__ for j in reversed(service.Queue().all())]})
            if path.startswith("/api/jobs/"):
                from synapse0g import service

                job = service.Queue().get(path.rsplit("/", 1)[-1])
                return self._json(200, job.__dict__) if job else self._json(404, {"error": "no such job"})
            if path.startswith("/api/observed/"):
                run = showcase.observed_run(path.rsplit("/", 1)[-1])
                return self._json(200, run) if run else self._json(404, {"error": "no such recorded run"})
            if path.startswith("/api/"):
                return self._json(404, {"error": f"no such endpoint: {path}"})
            return self._static(path)
        except Exception as e:  # the dashboard shows this error; it never sees a made-up number
            traceback.print_exc()
            return self._json(500, {"error": f"{type(e).__name__}: {e}"})

    def do_POST(self) -> None:  # noqa: N802
        path = self.path.split("?")[0]
        try:
            length = int(self.headers.get("Content-Length") or 0)
            if length > 1_000_000:
                return self._json(413, {"error": "request too large"})
            body = json.loads(self.rfile.read(length) or b"{}")
            if path == "/api/live":
                from synapse0g import live

                try:
                    run = live.start(body.get("task"), bool(body.get("cold")), body.get("agent") or "supplier")
                except RuntimeError as e:
                    return self._json(409, {"error": str(e)})
                except ValueError as e:
                    return self._json(400, {"error": str(e)})
                return self._json(200, {"id": run.id, "task_id": run.task_id})
            if path == "/api/pay/quote":
                try:
                    return self._json(200, _desk().quote(body.get("payer") or "", body.get("task") or None))
                except ValueError as e:
                    return self._json(400, {"error": str(e)})
            if path == "/api/pay/resume":
                try:
                    return self._json(200, _desk().resume(body.get("quote") or ""))
                except ValueError as e:
                    return self._json(400, {"error": str(e)})
            if path == "/api/pay/confirm":
                tx = body.get("tx") or ""
                if not (tx.startswith("0x") and len(tx) == 66):
                    return self._json(400, {"error": "tx is a transaction hash: 0x followed by 64 hex characters"})
                return self._json(200, _desk().confirm(body.get("quote") or "", tx))
            if path == "/api/jobs":
                from synapse0g import service

                job = service.submit(service.Queue(), body.get("kind", ""), body.get("spec") or {})
                return self._json(201, job.__dict__)
            if path == "/api/map":
                from synapse0g.mapping import inspect_code

                return self._json(200, inspect_code(code=body.get("code"), path=body.get("path"), entry=body.get("entry")))
            return self._json(404, {"error": f"no such endpoint: {path}"})
        except ValueError as e:  # bad input, said plainly
            return self._json(400, {"error": str(e)})
        except Exception as e:
            traceback.print_exc()
            return self._json(500, {"error": f"{type(e).__name__}: {e}"})

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST")
        self.end_headers()

    def _static(self, path: str) -> None:
        if not DIST.exists():
            return self._send(404, b"dashboard not built: cd web && npm install && npm run build", "text/plain")
        target = (DIST / path.lstrip("/")).resolve()
        if not target.is_file() or DIST.resolve() not in target.parents:
            target = DIST / "index.html"  # client-side routes
        ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        self._send(200, target.read_bytes(), ctype)

    def log_message(self, fmt, *args) -> None:
        pass


_DESK: dict = {}
_DESK_LOCK = threading.Lock()


def _desk():
    """The payment desk, made on first use, with its deposit watcher running beside the server."""
    from synapse0g import payments

    with _DESK_LOCK:
        if "desk" not in _DESK:
            desk = _DESK["desk"] = payments.Desk()
            threading.Thread(target=desk.watch_forever, daemon=True).start()
        return _DESK["desk"]


def _paid_account() -> dict:
    """The agent's own 0G account as the spend guard sees it, or why it cannot be read. Never a key."""
    import os

    from synapse0g import spend
    from synapse0g.registry import load_registry

    missing = [k for k in ("ZG_AGENT_KEY", "ZG_MANAGEMENT_KEY") if not os.environ.get(k)]
    if missing:
        return {"connected": False, "why": f"not connected: {', '.join(missing)} not set"}
    try:
        guard = spend.guard_for(load_registry().providers["zerog-agent"])
        from synapse0g.funding import RouterAccount

        cap = spend.key_cap_source(RouterAccount(os.environ["ZG_MANAGEMENT_KEY"]))
        return {"connected": True, **guard.available(), "open_holds": len(guard.open_holds()), "key_cap": cap}
    except Exception as e:  # shown, never hidden
        return {"connected": False, "why": f"{type(e).__name__}: {e}"[:300]}


def serve(port: int = 8000) -> None:
    from synapse0g.registry import load_env

    load_env()  # keys stay on the server: no endpoint returns one

    def warm():  # the payment desk reads 0G's catalog and the chain: start it now, not on a page's first visit
        try:
            _desk()
        except Exception as e:  # shown in the log; the pages say what failed when they ask
            print(f"payments: the desk could not start yet: {type(e).__name__}: {e}")

    threading.Thread(target=warm, daemon=True).start()
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"synapse0g dashboard on http://127.0.0.1:{port}  (data: /api/feed)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
