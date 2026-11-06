"""The live run, offline: the real app, compiler, runtime and proxy with a fake provider. Checks the
stream the dashboard draws from: the graph, the policy, every call, the runtime's own node events,
and a result that never places the order in the compiled run."""

from __future__ import annotations

import time

import pytest

pytest.importorskip("langgraph")

from synapse0g import live, proxy, upstream  # noqa: E402
from tests.test_supplier import fake_call  # noqa: E402


@pytest.fixture(autouse=True)
def _fake(monkeypatch, tmp_path):
    import yaml

    from synapse0g import registry

    models = yaml.safe_load((registry.ROOT / "models.yaml").read_text())["models"]
    catalog = {m["tag"]: {"pricing_usd": {"prompt": 0.000001, "completion": 0.000002}}
               for m in models.values()}
    monkeypatch.setattr(registry, "fetch_catalog", lambda url: catalog)
    monkeypatch.setattr(upstream, "call", fake_call)
    monkeypatch.setattr(proxy.upstream, "call", fake_call)
    monkeypatch.setattr(live, "CACHE_DIR", tmp_path / "live_cache")
    monkeypatch.setattr(live, "RUNS", {})
    monkeypatch.setattr(live, "_COMPILED", {})


def _finish(run, timeout=60):
    end = time.time() + timeout
    while not run.done and time.time() < end:
        time.sleep(0.05)
    assert run.done
    return run.events


def _of(events, kind):
    return [e for e in events if e["type"] == kind]


def test_a_live_run_streams_the_graph_the_calls_and_both_results():
    task = live.tasks()[0]
    events = _finish(live.start(task["id"], cold=True))
    assert not _of(events, "failed"), _of(events, "failed")
    compiled = _of(events, "compiled")[0]
    assert {"memoize", "parallelize"} <= set(compiled["applied"]) and not compiled["reused"]
    assert compiled["before"]["nodes"] and compiled["after"]["nodes"]
    assert "refused" in compiled["explain"]  # the order step is protected, and the run says so
    calls = _of(events, "call")
    assert {c["arm"] for c in calls} == {"compile", "as_written", "compiled"}
    # the runtime's own events, under their own names, for the compiled graph
    kinds = {e["event"] for e in _of(events, "node")}
    assert {"node_start", "node_end", "node_stub"} <= kinds
    stub = next(e for e in _of(events, "node") if e["event"] == "node_stub")
    assert stub["effect"] == "irreversible"
    result = _of(events, "result")[0]
    a, c = result["as_written"], result["compiled"]
    assert a["passed"] and c["passed"] and result["same_decision"]
    assert a["order_placed"] and not c["order_placed"]
    assert a["calls"] == 5 and c["calls"] == 5


def test_a_second_run_reuses_the_compiled_graph_and_one_at_a_time():
    first = live.start(live.tasks()[0]["id"], cold=True)
    with pytest.raises(RuntimeError, match="already in progress"):
        live.start(live.tasks()[1]["id"])
    _finish(first)
    events = _finish(live.start(live.tasks()[1]["id"]))
    compiled = _of(events, "compiled")[0]
    assert compiled["reused"] and compiled["compiled_on"] == live.tasks()[0]["id"]
    assert not [c for c in _of(events, "call") if c["arm"] == "compile"]
    # the policy summary is pure and memoized: the second request is served it from the cache
    assert any(e["event"] == "cache_hit" for e in _of(events, "node"))


def test_simultaneous_start_requests_register_only_one_run(monkeypatch):
    import threading
    from concurrent.futures import ThreadPoolExecutor

    ready = threading.Barrier(8)
    release = threading.Event()
    finished = threading.Event()

    def worker(run, cold):
        release.wait(10)
        run.done = True
        finished.set()

    def slow_tasks(agent="supplier"):
        # Widen the check/register window so an unlocked start admits competitors.
        time.sleep(0.05)
        return [{"id": "offline-request"}]

    monkeypatch.setattr(live, "_run", worker)
    monkeypatch.setattr(live, "tasks", slow_tasks)

    def request():
        ready.wait(timeout=5)
        try:
            return live.start()
        except RuntimeError as exc:
            assert "already in progress" in str(exc)
            return None

    try:
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: request(), range(8)))
        assert sum(r is not None for r in results) == 1
        assert len(live.RUNS) == 1
    finally:
        release.set()
        assert finished.wait(5)


def test_the_procurement_agent_runs_live_compiled_and_as_written(monkeypatch):
    from tests.test_procurement import fake_call as procurement_call

    monkeypatch.setattr(upstream, "call", procurement_call)
    monkeypatch.setattr(proxy.upstream, "call", procurement_call)
    task = live.tasks("procurement")[0]
    events = _finish(live.start(task["id"], cold=True, agent="procurement"), timeout=120)
    assert not _of(events, "failed"), _of(events, "failed")
    assert _of(events, "task")[0]["agent"] == "procurement" and _of(events, "task")[0]["summary"]
    compiled = _of(events, "compiled")[0]
    assert compiled["applied"] and compiled["after"]["nodes"]
    assert {c["arm"] for c in _of(events, "call")} == {"as_written", "compiled"}  # compiling a function makes no calls
    assert any(e["event"] == "node_stub" for e in _of(events, "node"))  # filing for approval is never executed
    result = _of(events, "result")[0]
    assert result["as_written"]["passed"] and result["compiled"]["passed"] and result["same_decision"]


def test_an_agent_its_study_left_unchanged_runs_once_and_says_so():
    task = live.tasks("research")[0]
    events = _finish(live.start(task["id"], agent="research"), timeout=120)
    assert not _of(events, "failed"), _of(events, "failed")
    assert not _of(events, "compiled")  # a ReAct loop has no graph to rewrite
    assert _of(events, "policy")[0]["steps"] == {}
    result = _of(events, "result")[0]
    assert result["unchanged"] and result["compiled"] is None
    assert {c["arm"] for c in _of(events, "call")} == {"as_written"}


def test_unknown_live_agents_are_refused():
    with pytest.raises(ValueError, match="no live agent"):
        live.start(agent="shopping")
