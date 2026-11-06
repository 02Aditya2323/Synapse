"""Paired outcome experiments for comparing baseline and optimized runs.

Verification is empirical evidence under a named relation, never a universal proof.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Literal

from pydantic import BaseModel, Field

from synapse0g.graph.profile.collect import Execution

Checker = Callable[[str, object], bool]
Invariant = Callable[[Execution], bool]
Relation = Literal["exact", "checker", "invariant", "semantic"]


class Comparison(BaseModel):
    task_id: str
    cold: bool
    repeat: int
    status: Literal["unchanged", "fixed", "broken", "crashed"]
    equivalent: bool
    before_passed: bool | None = None
    after_passed: bool | None = None
    invariant_failures: list[str] = Field(default_factory=list)
    error: str | None = None
    sufficient: bool = True


def exact(a, b) -> bool:
    # Type-sensitive canonical JSON: Python's True == 1 is not exact equivalence.
    return json.dumps(a, sort_keys=True, ensure_ascii=False, allow_nan=False) == json.dumps(
        b, sort_keys=True, ensure_ascii=False, allow_nan=False
    )


def compare(
    before: Execution,
    after: Execution,
    *,
    relation: Relation = "checker",
    checker: Checker | None = None,
    invariants: dict[str, Invariant] | None = None,
    semantic: Callable[[object, object], bool] | None = None,
) -> Comparison:
    base = {"task_id": before.task_id, "cold": before.cold, "repeat": before.repeat}
    if (before.task_id, before.split, before.cold, before.seed, before.repeat) != (
        after.task_id,
        after.split,
        after.cold,
        after.seed,
        after.repeat,
    ):
        return Comparison(**base, status="crashed", equivalent=False, error="unpaired inputs/conditions")
    if before.crashed or after.crashed:
        errors = [r.error or next((n.error for n in r.nodes if n.error), "") for r in (before, after) if r.crashed]
        return Comparison(**base, status="crashed", equivalent=False, error="; ".join(errors))
    try:
        failures = [
            f"{side}:{name}"
            for side, run in [("before", before), ("after", after)]
            for name, predicate in (invariants or {}).items()
            if not predicate(run)
        ]
        if not exact(before.actions, after.actions):
            failures.append("observable_actions_changed")
        bp = checker(before.task_id, before.output) if checker else None
        ap = checker(after.task_id, after.output) if checker else None
        if checker and (type(bp) is not bool or type(ap) is not bool):
            raise TypeError("checker must return bool")
        sufficient = True
        if relation == "checker":
            if checker is None:
                raise ValueError("checker relation requires task checker")
            eq = bp and ap
        elif relation == "exact":
            eq = exact(before.output, after.output)
        elif relation == "invariant":
            if not invariants:
                raise ValueError("invariant relation requires declared invariants")
            eq = not failures
        elif relation == "semantic":
            if semantic is None:
                raise ValueError("semantic relation requires a judge")
            eq = bool(semantic(before.output, after.output))
            sufficient = checker is not None or bool(invariants)
        else:
            raise ValueError(f"unknown relation {relation}")
        regression = bp is True and ap is False
        # Both failing is unchanged for checker pass-rate accounting, but remains non-equivalent.
        status = (
            "broken"
            if regression or failures or (relation != "checker" and not eq)
            else "fixed"
            if bp is False and ap is True
            else "unchanged"
            if eq or (relation == "checker" and bp is False and ap is False)
            else "broken"
        )
        return Comparison(
            **base,
            status=status,
            equivalent=bool(eq) and not failures,
            before_passed=bp,
            after_passed=ap,
            invariant_failures=failures,
            sufficient=sufficient,
        )
    except Exception as exc:  # noqa: BLE001 - record plugin/handler failures in the returned report
        return Comparison(**base, status="crashed", equivalent=False, error=f"{type(exc).__name__}: {exc}")
