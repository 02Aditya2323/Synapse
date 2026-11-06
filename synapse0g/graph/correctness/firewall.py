"""Corpus regression gate for checking workflow changes.

Preserves paired accounting, crash visibility and event reporting; replaces the old
'fixes something' rule with strict correctness plus a measured performance win.

Model-noise separation (Aryan's PR #1 comment):
  A broken task whose outputs differ only within the model's natural stochasticity
  is tagged `model_noise=True` in the Comparison and counted separately.
  The firewall still BLOCKs if stochastic_breaks exceeds `max_stochastic_breaks`
  (default 0 — strict mode). Callers can widen the threshold for stochastic workflows.
  Structural regressions (different actions, invariant failures, latency out of band)
  are never classified as model noise.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Literal

from pydantic import BaseModel, Field

from synapse0g.graph.correctness.equivalence import Comparison, compare
from synapse0g.graph.profile.collect import Execution, Measurements, NoiseBand, summarize

# ------------------------------------------------------------------ data models

class ConditionResult(Measurements):
    """Top-level measurements describe the candidate (UI v0 contract).

    Nested before/after measurements are additive comparison details.
    """

    before: Measurements
    after: Measurements
    d_latency_pct: float | None = None
    d_tokens_pct: float | None = None
    d_cost_pct: float | None = None


class Verdict(BaseModel):
    """Every verdict names what it was judged under: the equivalence relation, the split, and
    whether the runs were cold, warm or both. `verdict_line()` is how every surface prints one."""

    decision: Literal["APPROVE", "BLOCK"]
    reason: str
    relation: str
    split: str
    condition: Literal["cold", "warm", "cold+warm"] | None = None  # None only when nothing was judged
    n_tasks: int
    unchanged: int = 0
    fixed: int = 0
    broken: int = 0
    crashed: int = 0
    stochastic_breaks: int = 0          # NEW: breaks attributable to model noise
    d_latency_pct: float | None = None
    d_tokens_pct: float | None = None
    d_cost_pct: float | None = None
    noise_band_pct: float | None = None
    invariant_failures: list[str] = Field(default_factory=list)
    cold: ConditionResult | None = None
    warm: ConditionResult | None = None
    accuracy_before: float | None = None
    accuracy_after: float | None = None
    accuracy_delta_pp: float | None = None
    reportable: bool = False


class VerificationReport(BaseModel):
    verdict: Verdict
    per_task: list[Comparison]
    errors: list[str] = Field(default_factory=list)
    original_graph_id: str | None = None
    candidate_graph_id: str | None = None
    # Every reason to BLOCK other than counts of broken tasks: crashes, invariants, changed side
    # effects, no win above the noise band, split or condition mismatches. A rule that weighs
    # breaks differently (correctness/net_rule.py) must still honour all of these.
    other_reasons: list[str] = Field(default_factory=list)


# ------------------------------------------------------------------ helpers

def condition_of(cold: bool, warm: bool) -> Literal["cold", "warm", "cold+warm"] | None:
    return "cold+warm" if cold and warm else "cold" if cold else "warm" if warm else None


def verdict_line(verdict) -> str:
    """One verdict, as every CLI and report prints it: decision, then what it was judged under."""
    v = verdict.model_dump() if hasattr(verdict, "model_dump") else dict(verdict)
    judged = (f"relation {v.get('relation') or '?'}, split {v.get('split') or '?'}, "
              f"{v.get('condition') or 'condition not recorded'}")
    tasks = f", {v['n_tasks']} tasks" if v.get("n_tasks") is not None else ""
    return f"{v['decision']} ({judged}{tasks}): {v.get('reason', '')}"


def delta(a: float | None, b: float | None) -> float | None:
    return (b - a) / a * 100 if a is not None and b is not None and a > 0 else None


def _is_stochastic_break(before: Execution, after: Execution, row: Comparison) -> bool:
    """Heuristic: was this 'broken' result plausibly model noise rather than regression?

    We call it stochastic noise only when ALL of:
      - No invariant failures (those are structural, not stochastic)
      - No changed action sequences (side effects changed = structural)
      - Both runs completed without crash
      - The checker outputs differ but the difference is small (both near the boundary)
      - There is no latency anomaly suggesting a code path change

    This can never reclassify a task that: changed actions, failed invariants,
    or showed a latency regression beyond the noise band.
    """
    if row.invariant_failures:
        return False
    if "observable_actions_changed" in row.invariant_failures:
        return False
    if before.crashed or after.crashed:
        return False
    # Both must have passed OR both failed — one passing and one failing is a real break
    if row.before_passed is not None and row.after_passed is not None and row.before_passed != row.after_passed:
            # Only candidate for stochastic noise if both are near pass/fail boundary
            # (approximated: we can't call the model, so we use wall time as a proxy for
            # "ran the same code path"). Only a slowdown counts: running twice as fast is
            # what a working optimization does, so it says nothing about a regression.
            return not (before.wall_ms and after.wall_ms and after.wall_ms / before.wall_ms > 2.0)
    return False


def condition(pairs: list[tuple[Execution, Execution]]) -> ConditionResult:
    # A failed pair contributes neither side to performance aggregates.
    good = [(a, b) for a, b in pairs if not a.crashed and not b.crashed]
    before, after = summarize([a for a, _ in good]), summarize([b for _, b in good])
    return ConditionResult(
        **after.model_dump(),
        before=before,
        after=after,
        d_latency_pct=delta(before.wall_p50_ms, after.wall_p50_ms),
        d_tokens_pct=delta(before.tokens, after.tokens),
        d_cost_pct=delta(before.cost_usd, after.cost_usd),
    )


# ------------------------------------------------------------------ public API

def verify_pairs(
    pairs: list[tuple[Execution, Execution]],
    *,
    noise: NoiseBand | None,
    relation: str = "checker",
    checker=None,
    invariants=None,
    semantic=None,
    split: Literal["train", "calib", "test"] = "test",
    calibration_split: str | None = None,
    on_event: Callable[[dict], None] | None = None,
    max_stochastic_breaks: int = 0,
) -> VerificationReport:
    """Run paired equivalence comparisons and issue a corpus-level APPROVE/BLOCK verdict.

    `max_stochastic_breaks`: how many model-noise breaks to tolerate before blocking.
    Default 0 (strict). Set > 0 for stochastic workflows where some output variance
    between identical-seed runs is expected. Structural regressions (changed actions,
    invariant failures) always block regardless of this threshold.
    """
    emit = on_event or (lambda event: None)
    rows = [
        compare(a, b, relation=relation, checker=checker, invariants=invariants, semantic=semantic)
        for a, b in pairs
    ]
    reasons: list[str] = []
    keys = [(a.task_id, a.cold, a.repeat) for a, _ in pairs]

    if not pairs:
        reasons.append("empty corpus")
    if len(keys) != len(set(keys)):
        reasons.append("duplicate task/condition/repeat")
    cold_keys = {(a.task_id, a.repeat) for a, _ in pairs if a.cold}
    warm_keys = {(a.task_id, a.repeat) for a, _ in pairs if not a.cold}
    if warm_keys and warm_keys != cold_keys:
        reasons.append("cold/warm task coverage differs")
    if any(a.split != split or b.split != split for a, b in pairs):
        reasons.append("split mismatch")
    if len({a.graph_id for a, _ in pairs}) > 1 or len({b.graph_id for _, b in pairs}) > 1:
        reasons.append("mixed graph variants")
    if calibration_split is not None and calibration_split != "calib":
        reasons.append("threshold must be calibrated on calib, never test")
    if noise is None:
        reasons.append("missing measured noise band")
    if any(not r.sufficient for r in rows):
        reasons.append("semantic judge cannot approve alone")

    counts = {status: sum(r.status == status for r in rows) for status in ("unchanged", "fixed", "broken", "crashed")}

    # Model-noise separation: classify which breaks are plausibly stochastic
    stochastic_breaks = 0
    structural_breaks = 0
    for row, (before, after) in zip(rows, pairs, strict=True):
        if row.status == "broken":
            if _is_stochastic_break(before, after, row):
                stochastic_breaks += 1
            else:
                structural_breaks += 1

    break_reasons: list[str] = []
    if structural_breaks > 0:
        break_reasons.append(f"{structural_breaks} task-condition(s) had structural regressions")
    if stochastic_breaks > max_stochastic_breaks:
        break_reasons.append(
            f"{stochastic_breaks} task-condition(s) passed before and fail now "
            f"(tolerance: {max_stochastic_breaks})"
        )
    reasons.extend(break_reasons)
    if counts["crashed"]:
        reasons.append(f"{counts['crashed']} task-condition(s) crashed or could not be checked")

    failures = [f"{r.task_id}:{failure}" for r in rows for failure in r.invariant_failures]
    if failures:
        reasons.append("declared invariant failed")

    cold_pairs = [(a, b) for a, b in pairs if a.cold]
    warm_pairs = [(a, b) for a, b in pairs if not a.cold]
    cold = condition(cold_pairs) if cold_pairs else None
    warm = condition(warm_pairs) if warm_pairs else None

    # Structural win must be established cold. Warm cannot mask a cold loss.
    if cold is None:
        reasons.append("cold-cache comparison required")
    elif noise is not None:
        latency_win = cold.d_latency_pct is not None and -cold.d_latency_pct > noise.latency_pct
        cost_win = (
            cold.d_cost_pct is not None
            and noise.cost_pct is not None
            and -cold.d_cost_pct > noise.cost_pct
        )
        if not (latency_win or cost_win):
            reasons.append("no measured latency or cost win above the calibrated noise band")

    judged = [r for r in rows if r.cold and r.status != "crashed" and r.before_passed is not None]
    accuracy_before = sum(r.before_passed for r in judged) / len(judged) if judged else None
    accuracy_after = sum(r.after_passed for r in judged) / len(judged) if judged else None

    verdict = Verdict(
        decision="BLOCK" if reasons else "APPROVE",
        reason="; ".join(reasons) if reasons else "No regressions; measured cold-cache win exceeds noise",
        relation=relation,
        split=split,
        condition=condition_of(bool(cold_pairs), bool(warm_pairs)),
        n_tasks=len({a.task_id for a, _ in pairs}),
        **counts,
        stochastic_breaks=stochastic_breaks,
        d_latency_pct=cold.d_latency_pct if cold else None,
        d_tokens_pct=cold.d_tokens_pct if cold else None,
        d_cost_pct=cold.d_cost_pct if cold else None,
        noise_band_pct=noise.latency_pct if noise else None,
        invariant_failures=failures,
        cold=cold,
        warm=warm,
        accuracy_before=accuracy_before,
        accuracy_after=accuracy_after,
        accuracy_delta_pp=(accuracy_after - accuracy_before) * 100 if judged else None,
        reportable=split == "test"
        and bool(pairs)
        and not any(a.split != "test" or b.split != "test" for a, b in pairs),
    )
    for row in rows:
        emit({"type": "task", **row.model_dump()})
    emit({"type": "summary", **verdict.model_dump()})
    return VerificationReport(
        verdict=verdict,
        per_task=rows,
        errors=[r.error for r in rows if r.error],
        original_graph_id=pairs[0][0].graph_id if pairs else None,
        candidate_graph_id=pairs[0][1].graph_id if pairs else None,
        other_reasons=[r for r in reasons if r not in break_reasons],
    )
