"""`synapse0g ui`: the dashboard and its data, on one local port.

  GET /api/feed     the featured results and the run history (see showcase.py)
  GET /api/health   ok
  GET /api/samples  sample agents to paste into the code inspector
  POST /api/map     {"code": ...} or {"path": ...}: the agent's graph before and after the passes (nothing runs)
  GET /api/observed           runs recorded by synapse0g.wrap, newest first
  GET /api/observed/<run_id>  one of them: its lifted graph, the passes and the measurements
  anything else     the built dashboard from web/dist, if it has been built
"""

from __future__ import annotations

import json
import mimetypes
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from synapse0g import showcase
from synapse0g.registry import ROOT

DIST = ROOT / "web" / "dist"


class Handler(BaseHTTPRequestHandler):
    def _send(self, status: int, body: bytes, ctype: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

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


def serve(port: int = 8000) -> None:
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"synapse0g dashboard on http://127.0.0.1:{port}  (data: /api/feed)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
