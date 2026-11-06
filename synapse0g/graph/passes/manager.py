"""Profile-guided greedy/beam search and an optional learned pass policy.

Feature allowlisting and ancestry extraction adapted from Black Box diagnosis/
features.py and graph_data.py (7af6e69). No diagnosis labels, answer features,
post-optimization measurements, metadata or cached flags enter the policy.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel

from synapse0g.graph.core.sir import Graph
from synapse0g.graph.correctness.firewall import Verdict, VerificationReport
from synapse0g.graph.correctness.preconditions import audit_transform, evaluate
from synapse0g.graph.passes.base import Finding, Transformation
from synapse0g.graph.profile.collect import Profile
from synapse0g.graph.profile.critical_path import critical_path

FEATURE_NAMES = (
    "nodes",
    "edges",
    "models",
    "reads",
    "writes",
    "protected",
    "regions",
    "critical_ms",
    "wall_ms",
    "tokens",
    "cost_usd",
)


def features(graph: Graph, profile: Profile) -> list[float]:
    """Only structure and PRE-candidate profiles; label provenance is enforced in fit()."""
    if graph.id != profile.graph_id:
        raise ValueError("feature profile must describe the pre-candidate graph")
    cp = critical_path(graph, profile).duration_ms if not graph.regions else -1
    return [
        float(len(graph.nodes)),
        float(sum(len(graph.predecessors(n.id, effects=False)) for n in graph.nodes)),
        float(sum(n.kind == "model" for n in graph.nodes)),
        float(sum(n.effect == "read" for n in graph.nodes)),
        float(sum(n.effect == "write" for n in graph.nodes)),
        float(sum(n.effect in {"external", "irreversible"} for n in graph.nodes)),
        float(len(graph.regions)),
        cp,
        profile.total.wall_p50_ms if profile.total.wall_p50_ms is not None else -1,
        profile.total.tokens_in if profile.total.tokens_in is not None else -1,
        profile.total.cost_usd if profile.total.cost_usd is not None else -1,
    ]


class TrainingExample(BaseModel):
    task_id: str  # canonical task family id, shared by variants
    split: Literal["train", "calib", "test"]
    pass_name: str
    features: list[float]
    gain_pct: float
    broken: bool
    source: Literal["measured"] = "measured"
    feature_stage: Literal["before"] = "before"


class LearnedPolicy:
    """Small per-pass forests; unfitted/unseen passes fall back to greedy ranking."""

    def __init__(self):
        self.models = {}
        self.training_tasks = set()

    def fit(self, records: list[TrainingExample]):
        from sklearn.ensemble import RandomForestRegressor

        if not records or any(r.split != "train" for r in records):
            raise ValueError("policy fitting accepts train records only")
        if any(len(r.features) != len(FEATURE_NAMES) for r in records):
            raise ValueError("feature schema mismatch")
        self.models = {}
        self.training_tasks = {r.task_id for r in records}
        for name in sorted({r.pass_name for r in records}):
            rows = [r for r in records if r.pass_name == name]
            if len(rows) < 4:
                continue
            estimator = RandomForestRegressor(n_estimators=32, max_depth=4, random_state=7, n_jobs=1)
            estimator.fit([r.features for r in rows], [[r.gain_pct, float(r.broken)] for r in rows])
            self.models[name] = estimator
        return self

    def predict(self, pass_name: str, x: list[float]) -> tuple[float, float] | None:
        estimator = self.models.get(pass_name)
        if estimator is None:
            return None
        gain, risk = estimator.predict([x])[0]
        return float(gain), min(1.0, max(0.0, float(risk)))

    def evaluate(self, records: list[TrainingExample]) -> dict:
        if not records or len({r.split for r in records}) != 1 or records[0].split == "train":
            raise ValueError("evaluation requires one held-out split")
        if self.training_tasks & {r.task_id for r in records}:
            raise ValueError("task leakage between train and evaluation")
        errors = []
        for r in records:
            prediction = self.predict(r.pass_name, r.features)
            if prediction is not None:
                errors.append(abs(prediction[0] - r.gain_pct))
        return {
            "split": records[0].split,
            "samples": len(records),
            "predicted_samples": len(errors),
            "gain_mae_pct": sum(errors) / len(errors) if errors else None,
        }


class PredictionRecord(BaseModel):
    """What a pass promised at one step of the search, and what measuring it then showed.

    Both gains are percent of the parent graph's measured p50 wall time, the graph the pass was
    applied to, so they are directly comparable. `measured_gain_pct` is None when the candidate was
    never run: the pass predicted a gain inside the noise band and was not tried, or it failed.
    """

    pass_name: str
    parent_pipeline: list[str]
    predicted_gain_pct: float | None  # the pass's own estimate
    learned_gain_pct: float | None = None  # the learned policy's estimate, when it ranked this pass
    measured_gain_pct: float | None = None
    error_pp: float | None = None  # measured minus the prediction that ranked it; negative = over-promised
    ranked_by: Literal["pass", "policy"] = "pass"
    outcome: Literal["kept", "no_gain", "blocked", "inside_noise", "rejected", "error"]
    firewall: str | None = None
    noise_band_pct: float | None = None


def prediction_error(records: list[PredictionRecord]) -> dict[str, dict]:
    """Per pass: how far measured gains landed from predicted ones, over every measured candidate.

    `mean_error_pp` keeps the sign, so a pass that always over-promises shows a negative mean even
    when its absolute error looks small next to another's.
    """
    out: dict[str, dict] = {}
    for name in sorted({r.pass_name for r in records}):
        rows = [r for r in records if r.pass_name == name]
        scored = [r for r in rows if r.error_pp is not None]
        out[name] = {
            "predictions": len(rows),
            "measured": len(scored),
            "untested": sum(r.measured_gain_pct is None for r in rows),
            "mean_abs_error_pp": sum(abs(r.error_pp) for r in scored) / len(scored) if scored else None,
            "mean_error_pp": sum(r.error_pp for r in scored) / len(scored) if scored else None,
            "mean_predicted_pct": (sum(r.predicted_gain_pct for r in scored if r.predicted_gain_pct is not None)
                                   / len(scored)) if scored else None,
            "mean_measured_pct": sum(r.measured_gain_pct for r in scored) / len(scored) if scored else None,
        }
    return out


class OptimizationResult(BaseModel):
    graph: Graph
    profile: Profile
    pipeline: list[str]
    transformations: list[Transformation]
    findings: list[Finding]
    strategy: str
    status: Literal["draft", "approved", "blocked"] = "draft"
    verdict: Verdict | None = None
    evaluations: int = 0
    elapsed_s: float = 0
    predictions: list[PredictionRecord] = []


@dataclass
class State:
    graph: Graph
    profile: Profile
    pipeline: list[str]
    changes: list[Transformation]
    score: float


def _fingerprint(g: Graph):
    data = g.model_dump(exclude={"id", "derived_from", "pipeline", "name"})
    for n in data["nodes"]:
        n.pop("profile", None)
        n.pop("origin", None)
    return hashlib.sha256(json.dumps(data, sort_keys=True, allow_nan=False).encode()).hexdigest()


def _gain(before: Profile, after: Profile, objective: str):
    a = before.total.wall_p50_ms if objective == "latency" else before.total.cost_usd
    b = after.total.wall_p50_ms if objective == "latency" else after.total.cost_usd
    return (a - b) / a * 100 if a is not None and b is not None and a > 0 else float("-inf")


def optimize(
    graph: Graph,
    profile: Profile,
    passes,
    *,
    reprofile: Callable[[Graph], Profile],
    validate: Callable[[Graph, Graph], VerificationReport],
    strategy="greedy",
    policy: LearnedPolicy | None = None,
    objective="latency",
    beam_width=3,
    max_steps=8,
    max_candidates=32,
    budget_s=60.0,
) -> OptimizationResult:
    """Search only on calibration tasks. finalize() is a distinct held-out operation.

    reprofile must execute/measure the candidate, not transplant its parent's timings.
    validate compares ORIGINAL vs candidate on calibration data (including invariants).
    """
    if profile.split != "calib":
        raise ValueError("search requires calib, never test profiles")
    if profile.noise_band_pct is None or not profile.cold:
        raise ValueError("search requires cold profile and calibrated noise")
    if profile.graph_id != graph.id:
        raise ValueError("profile graph mismatch")
    if policy is not None and policy.training_tasks & set(profile.meta.get("task_ids", [])):
        raise ValueError("policy training tasks overlap calibration tasks")
    if strategy not in {"greedy", "beam", "learned"} or objective not in {"latency", "cost"}:
        raise ValueError("unknown strategy or objective")
    if min(beam_width, max_steps, max_candidates) < 1 or budget_s <= 0:
        raise ValueError("search limits must be positive")
    passes = list(passes)
    if len({p.name for p in passes}) != len(passes):
        raise ValueError("pass names must be unique")
    start = time.monotonic()
    initial = State(graph.model_copy(deep=True), profile.model_copy(deep=True), [], [], 0)
    frontier, best, findings = [initial], initial, []
    seen, evaluations = {_fingerprint(graph)}, 0
    predictions: list[PredictionRecord] = []
    for _ in range(max_steps):
        next_states = []
        for state in frontier:
            ranked = []
            for pass_ in passes:
                if pass_.name in state.pipeline:
                    continue
                try:
                    fs = evaluate(pass_, state.graph, state.profile)
                    # A finding explains one node a pass will leave alone. It does not stop the pass:
                    # one `external` step must not forbid parallel work everywhere else. Whatever the
                    # pass then does is checked by audit_transform and the firewall.
                    findings.extend(f for f in fs if f not in findings)
                    estimate = pass_.predict(state.graph.model_copy(deep=True), state.profile.model_copy(deep=True))
                    baseline = (
                        state.profile.total.wall_p50_ms if objective == "latency" else state.profile.total.cost_usd
                    )
                    change = estimate.d_latency_ms if objective == "latency" else estimate.d_cost_usd
                    noise = profile.noise_band_pct if objective == "latency" else profile.meta.get("noise_cost_pct")
                    learned = None
                    if strategy == "learned" and policy is not None and objective == "latency":
                        learned = policy.predict(pass_.name, features(state.graph, state.profile))
                    if learned is None and (baseline is None or baseline <= 0 or change is None):
                        continue
                    if noise is None:
                        continue
                    # Under "learned", the policy's prediction gates the pass where it has one. Gating on
                    # the pass's own estimate first meant the policy could only reorder what greedy
                    # already liked, and never choose a pass greedy underrates.
                    own = -change / baseline * 100 if baseline and baseline > 0 and change is not None else None
                    gain = learned[0] if learned is not None else own
                    record = PredictionRecord(
                        pass_name=pass_.name, parent_pipeline=list(state.pipeline), predicted_gain_pct=own,
                        learned_gain_pct=learned[0] if learned is not None else None,
                        ranked_by="policy" if learned is not None else "pass", outcome="inside_noise",
                        noise_band_pct=noise,
                    )
                    if gain <= noise:
                        predictions.append(record)
                        findings.append(
                            Finding(
                                node="*",
                                pass_name=pass_.name,
                                precondition="gain_above_noise",
                                message="Predicted gain is inside the measured noise band",
                            )
                        )
                        continue
                    score = gain * estimate.confidence / (1 + estimate.risk)
                    if learned is not None:
                        score = learned[0] / (1 + learned[1])
                    ranked.append((score, pass_, record))
                except Exception as exc:  # noqa: BLE001 - record plugin/handler failures in the returned report
                    findings.append(
                        Finding(
                            node="*",
                            pass_name=pass_.name,
                            precondition="pass_error",
                            message=f"{type(exc).__name__}: {exc}",
                        )
                    )
            for _, pass_, record in sorted(ranked, key=lambda x: (-x[0], x[1].name)):
                if evaluations >= max_candidates or time.monotonic() - start >= budget_s:
                    break
                evaluations += 1
                record.outcome = "error"
                predictions.append(record)
                try:
                    candidate, changes = pass_.apply(state.graph.model_copy(deep=True))
                    fs = audit_transform(state.graph, candidate, pass_.name, changes)
                    findings.extend(fs)
                    if fs:
                        record.outcome = "rejected"
                        continue
                    fingerprint = _fingerprint(candidate)
                    if fingerprint in seen:
                        predictions.remove(record)  # the same graph was already measured
                        continue
                    seen.add(fingerprint)
                    candidate.id = f"{graph.id}_{fingerprint[:12]}"
                    candidate.derived_from = graph.id
                    candidate.pipeline = state.pipeline + [pass_.name]
                    measured = reprofile(candidate.model_copy(deep=True))
                    if (
                        measured.graph_id != candidate.id
                        or measured.split != "calib"
                        or not measured.cold
                        or set(measured.meta.get("task_ids", [])) != set(profile.meta.get("task_ids", []))
                        or measured.meta.get("crashed", 0) > 0
                    ):
                        raise ValueError("reprofile must use the same calibration tasks/conditions without crashes")
                    parent_wall = state.profile.total.wall_p50_ms
                    after_wall = measured.total.wall_p50_ms
                    if objective == "latency" and parent_wall and after_wall is not None:
                        record.measured_gain_pct = (parent_wall - after_wall) / parent_wall * 100
                        promised = (record.learned_gain_pct if record.ranked_by == "policy"
                                    else record.predicted_gain_pct)
                        if promised is not None:
                            record.error_pp = record.measured_gain_pct - promised
                    check = validate(graph.model_copy(deep=True), candidate.model_copy(deep=True))
                    record.firewall = check.verdict.decision
                    if check.verdict.split != "calib" or check.verdict.reportable:
                        raise ValueError("candidate selection must not consult test results")
                    if (
                        check.original_graph_id != graph.id
                        or check.candidate_graph_id != candidate.id
                        or {r.task_id for r in check.per_task} != set(profile.meta.get("task_ids", []))
                    ):
                        raise ValueError("calibration verdict belongs to different graphs or tasks")
                    if check.verdict.decision != "APPROVE":
                        record.outcome = "blocked"
                        findings.append(
                            Finding(
                                node="*",
                                pass_name=pass_.name,
                                precondition="calibration_firewall",
                                message=check.verdict.reason,
                            )
                        )
                        continue
                    score = _gain(profile, measured, objective)
                    if score <= state.score:
                        record.outcome = "no_gain"
                        continue
                    record.outcome = "kept"
                    child = State(candidate, measured, candidate.pipeline, state.changes + changes, score)
                    next_states.append(child)
                    if score > best.score:
                        best = child
                    if strategy != "beam":
                        break
                except Exception as exc:  # noqa: BLE001 - record plugin/handler failures in the returned report
                    findings.append(
                        Finding(
                            node="*",
                            pass_name=pass_.name,
                            precondition="candidate_error",
                            message=f"{type(exc).__name__}: {exc}",
                        )
                    )
        if not next_states:
            break
        frontier = sorted(next_states, key=lambda s: -s.score)[: beam_width if strategy == "beam" else 1]
        if evaluations >= max_candidates or time.monotonic() - start >= budget_s:
            break
    return OptimizationResult(
        graph=best.graph,
        profile=best.profile,
        pipeline=best.pipeline,
        transformations=best.changes,
        findings=findings,
        strategy=strategy,
        evaluations=evaluations,
        elapsed_s=time.monotonic() - start,
        predictions=predictions,
    )


def finalize(
    result: OptimizationResult, original: Graph, verify: Callable[[Graph, Graph], VerificationReport]
) -> OptimizationResult:
    """Evaluate the frozen pipeline once on held-out tasks. Never feeds search."""
    if result.status != "draft":
        raise ValueError("result is already finalized")
    report = verify(original.model_copy(deep=True), result.graph.model_copy(deep=True))
    if report.verdict.split != "test" or not report.verdict.reportable:
        raise ValueError("finalization requires held-out test results")
    if report.original_graph_id != original.id or report.candidate_graph_id != result.graph.id:
        raise ValueError("verification graph ids do not match the frozen pipeline")
    if set(result.profile.meta.get("task_ids", [])) & {r.task_id for r in report.per_task}:
        raise ValueError("test tasks overlap pipeline calibration tasks")
    return result.model_copy(
        update={"verdict": report.verdict, "status": "approved" if report.verdict.decision == "APPROVE" else "blocked"}
    )


def training_example(
    graph: Graph, before: Profile, after: Profile, pass_name: str, report: VerificationReport, *, task_id: str
) -> TrainingExample:
    """Build a policy row from a measured experiment; never accept test-derived labels."""
    if before.split != "train" or after.split != "train" or report.verdict.split != "train":
        raise ValueError("training observations must all come from train")
    same_tasks = set(before.meta.get("task_ids", [])) == set(after.meta.get("task_ids", []))
    if before.cold != after.cold or not before.cold or not same_tasks:
        raise ValueError("training profiles must pair on cold task inputs")
    if before.meta.get("task_ids") != [task_id] or after.meta.get("task_ids") != [task_id]:
        raise ValueError("one policy observation per canonical task")
    gain = _gain(before, after, "latency")
    if gain == float("-inf"):
        raise ValueError("training requires measured latency")
    return TrainingExample(
        task_id=task_id,
        split="train",
        pass_name=pass_name,
        features=features(graph, before),
        gain_pct=gain,
        broken=bool(report.verdict.broken or report.verdict.crashed or report.verdict.invariant_failures),
    )
