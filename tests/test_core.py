"""Offline tests: a fake OpenAI-compatible provider on a local port, no keys, no spend."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from synapse0g import upstream
from synapse0g.effects import classify
from synapse0g.optimizer import Policy, StepPolicy, compact, prepare, step_of
from synapse0g.proxy import Engine, Proxy
from synapse0g.registry import load_registry
from synapse0g.tune import Batch, TaskRun, judge


class FakeProvider:
    """Answers chat requests; remembers every body it received."""

    def __init__(self, reply):
        self.bodies: list[dict] = []
        self.reply = reply
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                outer.bodies.append(body)
                raw = json.dumps(outer.reply(body)).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"


def _reply(content="Answer: 42", tool_calls=None, reasoning=50):
    def fn(body):
        msg = {"role": "assistant", "content": content}
        if tool_calls:
            msg["tool_calls"] = tool_calls
        thinking = 0 if body.get("enable_thinking") is False else reasoning
        return {
            "id": "chatcmpl-test",
            "choices": [{"index": 0, "message": msg, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 10 + thinking,
                      "completion_tokens_details": {"reasoning_tokens": thinking}},
            "x_0g_trace": {"request_id": "req-1", "billing": {"total_cost_usd_micro": "7"}},
        }
    return fn


@pytest.fixture
def registry(tmp_path, monkeypatch):
    def make(fake: FakeProvider):
        monkeypatch.setenv("FAKE_KEY", "test")
        path = tmp_path / "models.yaml"
        path.write_text(f"""
providers:
  fake: {{base_url: {fake.url}/v1, key_env: [FAKE_KEY]}}
  other: {{base_url: {fake.url}/v1, key_env: [FAKE_KEY]}}
models:
  big: {{provider: fake, tag: big-1, price_per_mtok: {{in: 1.0, out: 4.0}}, reasoning_off: {{enable_thinking: false}}, cheaper: [small]}}
  small: {{provider: fake, tag: small-1, price_per_mtok: {{in: 0.1, out: 0.4}}, reasoning_off: {{enable_thinking: false}}}}
  elsewhere: {{provider: other, tag: x-1, price_per_mtok: {{in: 0.01, out: 0.01}}}}
""")
        return load_registry(path, catalog={})
    return make


def _post(base_url, token, body):
    import urllib.request
    req = urllib.request.Request(base_url + "/chat/completions", data=json.dumps(body).encode(),
                                 headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"})
    with urllib.request.urlopen(req) as r:
        return json.load(r)


# ---- effects --------------------------------------------------------------------------
@pytest.mark.parametrize("name,effect", [
    ("get_order", "read"), ("place_order", "irreversible"), ("issue_refund", "irreversible"),
    ("send_email", "external"), ("search", "read"), ("calculator", "read"),
    ("get_and_charge", "irreversible"), ("frobnicate", "irreversible"), ("update_address", "write"),
    ("find_user_id_by_email", "read"), ("get_and_send_report", "external"),
])
def test_tool_effects(name, effect):
    assert classify(name)[0] == effect


def test_developer_label_wins():
    assert classify("frobnicate", labels={"frobnicate": "read"}) == ("read", "labelled by the developer")


# ---- request transforms ------------------------------------------------------------------
def test_compaction_keeps_every_message_and_the_last_results(registry):
    msgs = [{"role": "user", "content": "q"}]
    for i in range(4):
        msgs += [{"role": "assistant", "content": "", "tool_calls": [{"id": f"c{i}"}]},
                 {"role": "tool", "tool_call_id": f"c{i}", "content": f"r{i}" + "x" * 1000}]
    out, removed = compact(msgs, keep=2, trim_chars=50)
    assert len(out) == len(msgs)
    tool = [m for m in out if m["role"] == "tool"]
    assert len(tool[0]["content"]) < 100 and len(tool[1]["content"]) < 100
    assert tool[2]["content"] == msgs[6]["content"] and tool[3]["content"] == msgs[8]["content"]
    assert removed > 1800
    assert msgs[2]["content"].endswith("x")  # the caller's list is not modified


def test_routing_never_crosses_providers(registry):
    reg = registry(FakeProvider(_reply()))
    with pytest.raises(ValueError, match="cross providers"):
        prepare({"model": "big", "messages": []}, StepPolicy(route_to="elsewhere"), reg)


def test_prepare_applies_each_technique(registry):
    reg = registry(FakeProvider(_reply()))
    body = {"model": "big", "messages": [{"role": "system", "content": "s"}], "max_tokens": 500}
    out, model, applied = prepare(body, StepPolicy(reasoning_off=True, route_to="small", max_tokens=100), reg)
    assert model.id == "small" and out["model"] == "small-1"
    assert out["enable_thinking"] is False and out["max_tokens"] == 100
    assert applied == ["route:small", "reasoning_off", "cap:100"]


def test_step_is_the_call_site_not_the_conversation():
    a = {"model": "big", "messages": [{"role": "system", "content": "S"}, {"role": "user", "content": "1"}]}
    b = {"model": "big", "messages": [{"role": "system", "content": "S"}, {"role": "user", "content": "2"}]}
    c = {"model": "big", "messages": [{"role": "system", "content": "T"}]}
    assert step_of(a)[0] == step_of(b)[0] != step_of(c)[0]


# ---- the proxy, end to end ---------------------------------------------------------------
def test_proxy_records_measurements_and_applies_policy(registry):
    fake = FakeProvider(_reply())
    eng = Engine(registry(fake))
    with Proxy(eng) as px:
        s = eng.open(Policy({"*": StepPolicy(reasoning_off=True)}))
        _post(px.base_url, s.id, {"model": "big", "messages": [{"role": "user", "content": "hi"}]})
    rec = s.records[0]
    assert fake.bodies[0]["model"] == "big-1" and fake.bodies[0]["enable_thinking"] is False
    assert (rec.tokens_in, rec.tokens_out, rec.reasoning_tokens) == (100, 10, 0)
    assert rec.billed_usd == 7e-6 and rec.request_id == "req-1"
    assert rec.listed_cost_usd == pytest.approx(100 * 1e-6 + 10 * 4e-6)


def test_unknown_session_is_refused(registry):
    eng = Engine(registry(FakeProvider(_reply())))
    with Proxy(eng) as px, pytest.raises(Exception, match="401"):
        _post(px.base_url, "nobody", {"model": "big", "messages": []})


def test_reuse_serves_identical_read_only_requests(registry):
    fake = FakeProvider(_reply())
    eng = Engine(registry(fake))
    body = {"model": "big", "messages": [{"role": "user", "content": "same"}]}
    with Proxy(eng) as px:
        s = eng.open(Policy({"*": StepPolicy(reuse=True)}))
        _post(px.base_url, s.id, body)
        _post(px.base_url, s.id, body)
    assert len(fake.bodies) == 1
    assert [r.status for r in s.records] == ["ok", "reused"]


def test_reuse_never_repeats_an_irreversible_tool_call(registry):
    call = [{"id": "c1", "type": "function", "function": {"name": "place_order", "arguments": "{}"}}]
    fake = FakeProvider(_reply(content="", tool_calls=call))
    eng = Engine(registry(fake))
    tools = [{"type": "function", "function": {"name": "place_order", "description": "Place the order"}}]
    body = {"model": "big", "messages": [{"role": "user", "content": "buy"}], "tools": tools}
    with Proxy(eng) as px:
        s = eng.open(Policy({"*": StepPolicy(reuse=True)}))
        _post(px.base_url, s.id, body)
        _post(px.base_url, s.id, body)
    assert len(fake.bodies) == 2  # asked the model again rather than replaying the order


def test_budget_cap_stops_paid_calls(registry, monkeypatch):
    fake = FakeProvider(_reply())
    eng = Engine(registry(fake))
    monkeypatch.setattr(upstream, "BUDGET", upstream.Budget(0.0))
    with Proxy(eng) as px, pytest.raises(Exception, match="402"):
        s = eng.open()
        _post(px.base_url, s.id, {"model": "big", "messages": []})
    assert fake.bodies == []


# ---- firewall ----------------------------------------------------------------------------
def _batch(passed: dict[str, bool], cost: float, wall: float) -> Batch:
    runs = {
        t: TaskRun(t, ok, False, None, "", wall, [{"cost_usd": cost / len(passed), "tokens_in": 10, "tokens_out": 1,
                                                   "status": "ok", "billed_usd": None, "listed_cost_usd": None,
                                                   "reasoning_tokens": 0, "retries": 0}])
        for t, ok in passed.items()
    }
    return Batch("b", {}, runs)


def test_firewall_approves_a_cost_win_with_nothing_broken():
    base = {"a": True, "b": True, "c": False}
    v = judge(_batch(base, 1.0, 5), _batch(base, 1.02, 5.1), _batch(base, 0.6, 5.0))
    assert v.approve, v.reasons


def test_firewall_blocks_a_saving_that_breaks_a_stable_task():
    base = {"a": True, "b": True, "c": False}
    cand = {"a": True, "b": False, "c": False}
    v = judge(_batch(base, 1.0, 5), _batch(base, 1.0, 5), _batch(cand, 0.4, 3), _batch(cand, 0.4, 3))
    assert not v.approve and v.broken == ["b"]


def test_firewall_forgives_a_failure_that_passes_on_recheck():
    base = {"a": True, "b": True}
    v = judge(_batch(base, 1.0, 5), _batch(base, 1.0, 5), _batch({"a": True, "b": False}, 0.5, 5),
              _batch({"a": True, "b": True}, 0.5, 5))
    assert v.broken == [] and v.flaky == ["b"]


def _costs(costs: list[float], wall: float = 5.0) -> Batch:
    runs = {
        f"t{i}": TaskRun(f"t{i}", True, False, None, "", wall, [{"cost_usd": c, "tokens_in": 10, "tokens_out": 1, "status": "ok",
                                                              "billed_usd": None, "listed_cost_usd": None, "reasoning_tokens": 0,
                                                              "retries": 0, "waited_s": 0.0}])
        for i, c in enumerate(costs)
    }
    return Batch("b", {}, runs)


def test_firewall_blocks_a_saving_that_is_not_consistent_across_tasks():
    base = [1.0] * 20
    noisy = [0.4, 1.6] * 10  # some tasks much cheaper, others much dearer: no reliable saving
    v = judge(_costs(base), _costs(base), _costs(noisy))
    assert not v.approve and "95% confidence" in v.reasons[0]
    assert v.cost_ci[0] < 0 < v.cost_ci[1]


def test_firewall_approves_a_consistent_saving_even_when_totals_are_noisy():
    b1 = [1.0, 2.0, 3.0, 4.0] * 5
    b2 = [1.2, 1.8, 3.3, 3.9] * 5
    cand = [x * 0.8 for x in b1]
    v = judge(_costs(b1), _costs(b2), _costs(cand))
    assert v.approve, v.reasons
    assert v.cost_ci[0] > 0.1


def test_time_is_judged_against_the_control_run_alongside_the_change():
    # unchanged runs earlier were fast; the provider then slowed for everyone
    early, cand, control = _costs([1.0] * 10, wall=5), _costs([1.0] * 10, wall=9), _costs([1.0] * 10, wall=10)
    v = judge(early, early, cand, control=control)
    assert v.latency_gain == pytest.approx(0.1) and v.latency_ci[0] > 0


def test_rate_limit_waits_do_not_count_as_agent_time():
    from synapse0g.tune import _task_wall
    run = TaskRun("t", True, False, None, "", 30.0, [{"waited_s": 12.0}, {"waited_s": 3.0}])
    assert _task_wall(run) == 15.0


def test_tasks_that_flip_between_unchanged_runs_are_not_counted_as_broken():
    v = judge(_batch({"a": True, "b": True}, 1, 5), _batch({"a": True, "b": False}, 1, 5),
              _batch({"a": True, "b": False}, 0.5, 5))
    assert v.broken == [] and v.unstable == ["b"]


# ---- a real LangGraph agent through the proxy ---------------------------------------------
def test_langgraph_agent_runs_through_the_proxy(registry):
    pytest.importorskip("langgraph")
    from synapse0g.agents.langgraph_agents import run_math
    from synapse0g.tasks import Task, check_math

    state = {"n": 0}

    def reply(body):
        state["n"] += 1
        if state["n"] == 1:
            call = [{"id": "c1", "type": "function", "function": {"name": "calculator", "arguments": '{"expression": "6*7"}'}}]
            return _reply(content="", tool_calls=call)(body)
        assert body["messages"][-1]["content"] == "42"  # the real calculator ran
        return _reply(content="Answer: 42")(body)

    eng = Engine(registry(FakeProvider(reply)))
    with Proxy(eng) as px:
        s = eng.open()
        res = run_math(Task("t", "math", "What is 6*7?", "42"), px.base_url, s.id, "big")
    assert res.error is None and check_math(Task("t", "math", "", "42"), res.answer)
    assert len(s.records) == 2 and len({r.step for r in s.records}) == 1


# ---- prompt-cache marks ------------------------------------------------------------------
def test_cache_marks_change_layout_not_text(registry):
    from synapse0g.optimizer import add_cache_marks

    msgs = [{"role": "system", "content": "policy"}, {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "c"}]}, {"role": "tool", "tool_call_id": "c", "content": "result"}]
    out, n = add_cache_marks(msgs)
    assert n == 3  # system, the previous turn's last message, the newest message
    flat = [m["content"] if isinstance(m["content"], str) else "".join(p["text"] for p in m["content"]) for m in out]
    assert flat == [m["content"] for m in msgs]
    assert out[0]["content"][-1]["cache_control"] == {"type": "ephemeral"}
    assert isinstance(msgs[0]["content"], str)  # the caller's messages are untouched


def test_cache_marks_only_on_models_proven_to_accept_them(registry, tmp_path, monkeypatch):
    reg = registry(FakeProvider(_reply()))  # the fake models do not set explicit_cache
    body = {"model": "big", "messages": [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}]}
    out, _, applied = prepare(body, StepPolicy(cache_marks=True), reg)
    assert "cache_marks" not in applied and out["messages"][0]["content"] == "s"


def test_a_broken_upstream_reply_becomes_an_error_not_a_dropped_connection(registry, monkeypatch):
    """Regression: a reply cut off mid-transfer used to drop the agent's connection (seen on 0G, 6 Nov)."""

    class Truncating(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            self.send_response(200)
            self.send_header("Content-Length", "500")
            self.end_headers()
            self.wfile.write(b'{"choices": [')  # far fewer bytes than promised

    monkeypatch.setattr(upstream.time, "sleep", lambda s: None)
    server = ThreadingHTTPServer(("127.0.0.1", 0), Truncating)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    fake = FakeProvider(_reply())
    fake.url = f"http://127.0.0.1:{server.server_address[1]}"
    eng = Engine(registry(fake))
    import urllib.error
    with Proxy(eng) as px:
        s = eng.open()
        with pytest.raises(urllib.error.HTTPError) as err:
            _post(px.base_url, s.id, {"model": "big", "messages": []})
    assert err.value.code == 502
    assert s.records[0].status == "error" and "broken reply" in s.records[0].error


def _crashing(passed: dict[str, bool], crashed: set[str]) -> Batch:
    b = _batch(passed, 1.0, 5)
    for t in crashed:
        b.runs[t].crashed = True
        b.runs[t].passed = False
    return b


def test_crashes_in_the_unchanged_runs_are_not_counted_as_fixed_by_the_change():
    """Regression: on Groq's free tier, rate-limit crashes made a cheaper model look like it fixed tasks."""
    ids = {f"t{i}": True for i in range(20)}
    b1 = _crashing(ids, {"t0"})
    cand = _batch(ids, 0.5, 5)
    v = judge(b1, _batch(ids, 1.0, 5), cand)
    assert "t0" not in v.fixed


def test_no_verdict_when_too_many_runs_crashed():
    ids = {f"t{i}": True for i in range(10)}
    v = judge(_crashing(ids, {"t0", "t1", "t2"}), _batch(ids, 1.0, 5), _batch(ids, 0.2, 2))
    assert not v.approve and v.reasons[0].startswith("no verdict")


def test_groq_wait_hint_is_read():
    from synapse0g.upstream import _retry_after
    assert _retry_after({}, "Please try again in 1.5s.") == 1.5
    assert _retry_after({"Retry-After": "7"}, "") == 7.0


def test_keys_rotate_per_call(monkeypatch):
    from synapse0g.registry import Provider
    monkeypatch.setenv("K1", "a")
    monkeypatch.setenv("K-2", "b")
    p = Provider("rot", "http://x", ("K1", "K-2", "K3"))
    assert p.keys() == ["a", "b"]
    assert [upstream._next_key(p)[1] for _ in range(4)] == ["a", "b", "a", "b"]


def test_env_names_may_contain_hyphens(tmp_path, monkeypatch):
    from synapse0g.registry import load_env
    monkeypatch.delenv("GROQ_API_KEY-2", raising=False)
    env = tmp_path / ".env"
    env.write_text("GROQ_API_KEY-2=secret\n")
    load_env(env)
    import os
    assert os.environ.pop("GROQ_API_KEY-2") == "secret"
