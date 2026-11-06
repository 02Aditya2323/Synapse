from synapse0g.graph.correctness.equivalence import compare
from synapse0g.graph.correctness.firewall import verify_pairs
from synapse0g.graph.profile.collect import Execution, NodeMeasurement, NoiseBand


def execution(graph, answer=4, wall=10, task="t", cold=True):
    return Execution(
        task_id=task,
        graph_id=graph,
        split="test",
        cold=cold,
        wall_ms=wall,
        output=answer,
        nodes=[NodeMeasurement(node_id="n", latency_ms=wall)],
    )


def test_checker_and_crash_classification():
    def check(task, output):
        return output == 4

    a, b = execution("a"), execution("b", answer=5)
    assert compare(a, b, checker=check).status == "broken"
    a.output = 5
    b.output = 4
    assert compare(a, b, checker=check).status == "fixed"
    a.error = "boom"
    assert compare(a, b, checker=check).status == "crashed"


def test_gate_requires_real_gain_and_no_regression():
    noise = NoiseBand(latency_pct=5, samples=4)
    a, b = execution("a"), execution("b", wall=5)
    assert verify_pairs([(a, b)], noise=noise, checker=lambda t, o: o == 4).verdict.decision == "APPROVE"
    b.output = 5
    assert verify_pairs([(a, b)], noise=noise, checker=lambda t, o: o == 4).verdict.decision == "BLOCK"
    b.output = 4
    b.wall_ms = 9.9
    assert verify_pairs([(a, b)], noise=noise, checker=lambda t, o: o == 4).verdict.decision == "BLOCK"


def test_semantic_never_approves_alone_and_invariant_overrides():
    a, b = execution("a"), execution("b", wall=5)
    n = NoiseBand(latency_pct=0, samples=1)
    report = verify_pairs([(a, b)], noise=n, relation="semantic", semantic=lambda a, b: True)
    assert report.verdict.decision == "BLOCK"
    report = verify_pairs([(a, b)], noise=n, relation="exact", invariants={"safe": lambda r: False})
    assert report.verdict.decision == "BLOCK"
    assert report.verdict.invariant_failures


def test_empty_missing_and_unpaired_fail_closed():
    n = NoiseBand(latency_pct=0, samples=1)
    assert verify_pairs([], noise=n, relation="exact").verdict.decision == "BLOCK"
    a, b = execution("a"), execution("b", wall=5, task="different")
    assert verify_pairs([(a, b)], noise=n, relation="exact").verdict.decision == "BLOCK"


def test_exact_relation_cannot_be_overridden_by_checker_improvement():
    a, b = execution("a", answer=1), execution("b", answer=4, wall=5)
    assert compare(a, b, relation="exact", checker=lambda t, o: o == 4).status == "broken"


def test_unknown_cost_and_token_totals_remain_unknown():
    a, b = execution("a"), execution("b", wall=5)
    report = verify_pairs([(a, b)], noise=NoiseBand(latency_pct=1, samples=1), relation="exact")
    assert report.verdict.d_cost_pct is None and report.verdict.d_tokens_pct is None


def test_runtime_error_in_baseline_blocks_even_when_candidate_passes():
    a, b = execution("a"), execution("b", wall=5)
    a.error = "baseline failed"
    report = verify_pairs([(a, b)], noise=NoiseBand(latency_pct=1, samples=1), relation="exact")
    assert report.verdict.crashed == 1 and report.verdict.decision == "BLOCK"
    assert report.verdict.cold.before.samples == 0 and report.verdict.cold.after.samples == 0


def test_changed_action_is_blocked_even_if_final_answer_matches():
    a, b = execution("a"), execution("b", wall=5)
    a.actions = [{"target": "pay", "args": {"amount": 10}, "stubbed": True}]
    b.actions = [{"target": "pay", "args": {"amount": 100}, "stubbed": True}]
    result = verify_pairs([(a, b)], noise=NoiseBand(latency_pct=1, samples=1), relation="exact")
    assert result.verdict.decision == "BLOCK"


def test_cold_payload_preserves_documented_top_level_measurements():
    a, b = execution("a"), execution("b", wall=5)
    result = verify_pairs([(a, b)], noise=NoiseBand(latency_pct=1, samples=1), relation="exact")
    assert result.verdict.model_dump()["cold"]["wall_p50_ms"] == 5


def test_a_break_on_a_faster_run_is_not_called_structural():
    """code_review_0041: the unmodified rerun lost the same task, yet a 2.5x run read as a new code path."""
    a, b = execution("g", answer=4, wall=100), execution("g2", answer=5, wall=40)
    report = verify_pairs([(a, b)], noise=NoiseBand(latency_pct=1, samples=1), checker=lambda t, o: o == 4)
    assert report.verdict.stochastic_breaks == 1 and "structural" not in report.verdict.reason
    assert report.verdict.decision == "BLOCK", "the label changes, the strict verdict does not"


def test_a_break_on_a_much_slower_run_is_still_structural():
    a, b = execution("g", answer=4, wall=40), execution("g2", answer=5, wall=100)
    report = verify_pairs([(a, b)], noise=NoiseBand(latency_pct=1, samples=1), checker=lambda t, o: o == 4)
    assert report.verdict.stochastic_breaks == 0 and "structural" in report.verdict.reason
