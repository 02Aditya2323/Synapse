"""The paid agent: it prices jobs, works within its balance, and degrades instead of crashing."""

from __future__ import annotations

import pytest

from synapse0g import service, study, upstream

SAMPLE = "synapse0g/samples/market_desk.py"


@pytest.fixture
def agent(tmp_path, monkeypatch):
    db = tmp_path / "service.db"
    queue, ledger = service.Queue(db), service.Ledger(db)
    monkeypatch.setattr(service, "measured_cost_per_call", lambda name: 0.0005)

    def fake_study(spec, registry, **kw):  # stands in for real calls: spends what a small study would
        upstream.BUDGET.add(0.01)
        return {"agent": spec.name, "model": "zg-main", "started_at": "2026-11-06T00:00:00Z",
                "evaluation": {"verdict": {"approve": True, "reasons": [], "cost_gain": 0.5, "latency_gain": 0.4}},
                "sizes": (len(spec.calib), len(spec.test), kw["max_trials"])}

    monkeypatch.setattr(study, "run", fake_study)
    return service.Agent(queue, ledger, registry=object(), log=lambda m: None), queue, ledger


def test_modes_follow_the_balance():
    cost = 0.7
    assert service.mode_for(10, cost) == "full"
    assert service.mode_for(0.5, cost) == "reduced"
    assert service.mode_for(0.2, cost) == "static"
    assert service.mode_for(0.01, cost) == "paused"


def test_a_study_waits_when_funds_are_low_and_runs_once_they_arrive(agent):
    a, queue, ledger = agent
    job = service.submit(queue, "study", {"agent": "procurement"})
    assert job.quote["provider_usd"] > 0 and job.quote["price_usd"] > job.quote["provider_usd"]
    assert a.step() is None  # empty balance: nothing runs, nothing crashes
    waiting = queue.get(job.id)
    assert waiting.status == "waiting" and "waiting for funds" in waiting.note
    ledger.deposit(10, "test")
    done = a.step()
    assert done.status == "done" and done.mode == "full" and done.result["verdict"] == "APPROVE"
    assert ledger.available_usd() == pytest.approx(10 - 0.01)  # what the study spent came off the balance


def test_a_low_balance_runs_a_smaller_study_and_caps_its_spend(agent):
    a, queue, ledger = agent
    full = service.estimate("procurement", "full")["provider_usd"]
    ledger.deposit(full * 0.8 + service.RESERVE_USD, "test")
    cap_before = upstream.BUDGET.cap_usd
    job = service.submit(queue, "study", {"agent": "procurement"})
    done = a.step()
    assert done.mode == "reduced"
    assert done.result["cap_usd"] <= full * 0.8 + 1e-9  # never more than the balance above the reserve
    assert upstream.BUDGET.cap_usd == cap_before  # the job's cap does not leak into the next one
    assert job.id == done.id


def test_inspect_jobs_cost_nothing_and_run_even_when_paused(agent):
    a, queue, ledger = agent
    assert a.status()["mode"] == "paused"
    job = service.submit(queue, "inspect", {"path": SAMPLE})
    assert job.quote["price_usd"] == 0
    done = a.step()
    assert done.status == "done" and done.result["entry"] == "trade_desk" and done.result["after"]["nodes"]
    assert ledger.available_usd() == 0


def test_bad_jobs_are_refused_at_the_door(agent):
    _, queue, _ = agent
    with pytest.raises(ValueError, match="task set"):
        service.submit(queue, "study", {"agent": "my_unknown_agent"})
    with pytest.raises(ValueError, match="code or a path"):
        service.submit(queue, "inspect", {})
