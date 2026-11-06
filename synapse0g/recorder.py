"""`synapse0g.wrap(client)`: one line, and an existing app's model calls become a graph.

    from openai import OpenAI
    import synapse0g
    client = synapse0g.wrap(OpenAI(), workflow="support_desk")   # the only line that changes

Calls go to the client's own provider with its own key, exactly as before. Each one is measured
(wall-clock time, the tokens the provider reported) and kept in this process's memory. At exit, or
on `client.synapse0g_flush()`, the calls are lifted into a workflow graph: a call depends on an
earlier one when that call's output appears in its request. The graph passes then run on it, and a
report is saved in `reports/observed/`, where the dashboard shows it.

What is saved is the graph and the measurements, never prompt or answer text. Dependencies are
inferred from text, because a wrapper cannot see the code between the calls, and the report says
so. To apply the changes it finds, compile the app (`synapse0g.compile`).
"""

from __future__ import annotations

import atexit
import hashlib
import inspect
import json
import secrets
import sys
import threading
import time
from pathlib import Path
from typing import Any

from synapse0g.registry import ROOT

OBSERVED = ROOT / "reports" / "observed"
# Passes that are sound on a recorded trace. Not dce: a wrapper cannot see which answers the app
# used, so no call may be called unused. Not downgrade: the calls went to the app's own provider.
TRACE_PASSES = ("cse", "memoize", "parallelize")


def _key(model: str, messages: list) -> str:
    return hashlib.sha256(json.dumps([model, messages], sort_keys=True, default=str).encode()).hexdigest()[:16]


def report_of(workflow: str, run_id: str, calls: list[dict]) -> dict:
    """The saved report: graph before and after the passes, the measurements, and the waste found."""
    from synapse0g.graph.core.sir import Profile
    from synapse0g.graph.frontend.importers._common import build_steps, model_step
    from synapse0g.graph.frontend.lift import Trace, lift
    from synapse0g.graph.passes.base import run_pipeline

    raw = []
    for c in calls:
        step = model_step(c["model"], c["messages"], c["output"], latency_ms=c["latency_ms"],
                          tokens_in=c["tokens_in"], tokens_out=c["tokens_out"])
        # what makes a repeat safe to merge: temperature and seed, as the app sent them
        step["request"].update({k: c["params"][k] for k in ("temperature", "seed") if k in c["params"]})
        raw.append(step)
    graph = lift(Trace(run_id=run_id, workflow=workflow, steps=build_steps(raw)))
    # no prompt text is kept, so a call is named by its place in the run
    graph = graph.copy_with(nodes=[n.model_copy(update={"meta": {**n.meta, "label": f"call {i + 1}"}}) if n.kind == "model" else n
                                   for i, n in enumerate(graph.nodes)])
    after, results = run_pipeline(graph, Profile.empty(graph.id), list(TRACE_PASSES))
    seen: dict[str, int] = {}
    for c in calls:
        k = _key(c["model"], c["messages"])
        seen[k] = seen.get(k, 0) + 1
    passes = [{k: v for k, v in r.model_dump(mode="json").items() if k not in ("graph", "before", "after")} for r in results]
    return {
        "kind": "observed",
        "workflow": workflow,
        "run_id": run_id,
        "saved_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "calls": [{"model": c["model"], "latency_ms": round(c["latency_ms"], 1), "tokens_in": c["tokens_in"],
                   "tokens_out": c["tokens_out"], "request_hash": _key(c["model"], c["messages"])} for c in calls],
        "repeated_calls": sum(n - 1 for n in seen.values()),
        "before": graph.to_ui(),
        "after": after.to_ui(),
        "passes": passes,
        "waste": _waste(graph, after),
        "note": "Dependencies between calls are inferred from their text: a wrapper sees the calls, not the code "
                "between them. Compile the app to apply and verify the changes.",
    }


def _preload() -> None:
    import concurrent.futures.thread  # noqa: F401 - pulled in by the passes

    import synapse0g.graph.frontend.importers._common  # noqa: F401
    import synapse0g.graph.frontend.lift  # noqa: F401
    import synapse0g.graph.passes.base  # noqa: F401
    import synapse0g.graph.profile.waste  # noqa: F401


def _waste(graph, after) -> dict:
    """What the passes found on this run. Only merged repeats count as removable: `waste()` also
    drops calls whose answer nothing later read, which a wrapper cannot know."""
    from synapse0g.graph.profile.waste import waste

    out = waste(graph)
    elided = after.meta.get("elided") or {}
    nodes = {n.id: n for n in graph.nodes}
    out["removable"] = [{"node": nid, "target": nodes[nid].target, "reason": why} for nid, why in elided.items()]
    out["tokens_in_removable"] = sum(nodes[nid].meta.get("recorded_tokens_in") or 0 for nid in elided)
    out["tokens_out_removable"] = sum(nodes[nid].meta.get("recorded_tokens_out") or 0 for nid in elided)
    out.pop("priced_at", None)
    return out


class _Completions:
    def __init__(self, inner: Any, owner: Wrapped):
        self._inner, self._owner = inner, owner

    def create(self, **kwargs: Any) -> Any:
        started = time.perf_counter()
        response = self._inner.create(**kwargs)
        if inspect.isawaitable(response):
            async def finish() -> Any:
                done = await response
                self._owner._record(kwargs, done, started)
                return done

            return finish()
        self._owner._record(kwargs, response, started)
        return response

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


class _Chat:
    def __init__(self, inner: Any, owner: Wrapped):
        self._inner = inner
        self.completions = _Completions(inner.completions, owner)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


class Wrapped:
    def __init__(self, client: Any, workflow: str, out_dir: Path | None, at_exit: bool):
        self._client = client
        self.chat = _Chat(client.chat, self)
        self.workflow = workflow
        self.run_id = f"{workflow}-{time.strftime('%Y%m%dT%H%M%S')}-{secrets.token_hex(3)}"
        self.calls: list[dict] = []
        self._out = out_dir or OBSERVED
        self._lock = threading.Lock()
        self._saved = 0
        if at_exit:
            _preload()  # at interpreter exit, a first import of these would fail
            atexit.register(self.synapse0g_flush)

    def _record(self, kwargs: dict, response: Any, started: float) -> None:
        latency_ms = (time.perf_counter() - started) * 1000
        if kwargs.get("stream"):
            print("synapse0g: a streamed call is not recorded", file=sys.stderr)
            return
        try:
            reply = response.model_dump() if hasattr(response, "model_dump") else dict(response)
            usage = reply.get("usage") or {}
            message = ((reply.get("choices") or [{}])[0]).get("message") or {}
            with self._lock:
                self.calls.append({"model": kwargs.get("model", "unknown"), "messages": kwargs.get("messages") or [],
                                   "output": message.get("content"), "latency_ms": latency_ms,
                                   "params": {k: kwargs[k] for k in ("temperature", "seed") if k in kwargs},
                                   "tokens_in": usage.get("prompt_tokens"), "tokens_out": usage.get("completion_tokens")})
        except (TypeError, ValueError, AttributeError) as exc:  # recording must never break the app
            print(f"synapse0g: could not record a call: {exc}", file=sys.stderr)

    def synapse0g_report(self) -> dict | None:
        with self._lock:
            calls = list(self.calls)
        return report_of(self.workflow, self.run_id, calls) if calls else None

    def synapse0g_flush(self) -> Path | None:
        """Save the report for every call so far. Runs at exit by itself."""
        with self._lock:
            if len(self.calls) == self._saved:
                return None
            n = len(self.calls)
        report = self.synapse0g_report()
        self._out.mkdir(parents=True, exist_ok=True)
        path = self._out / f"{self.run_id}.json"
        path.write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")
        self._saved = n
        print(f"synapse0g: {n} calls from {self.workflow} saved to {path}", file=sys.stderr)
        return path

    def __getattr__(self, name: str) -> Any:
        return getattr(self._client, name)


def wrap(client: Any, *, workflow: str = "my_workflow", out_dir: str | Path | None = None, at_exit: bool = True) -> Wrapped:
    """Record an OpenAI-compatible client's calls and turn them into a graph. See the module doc."""
    return Wrapped(client, workflow, Path(out_dir) if out_dir else None, at_exit)
