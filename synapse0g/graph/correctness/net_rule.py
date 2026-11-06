"""A proposed net-correctness rule, `net-1`. NOT IN FORCE: the strict firewall decides.

Today any task that passed before and fails after is a BLOCK. On a real model that is too strict to
ever approve: research_fanout fixed 5 tasks and broke 2 and was blocked, while re-running the
unmodified graph alone broke 2 and fixed 1. This rule weighs fixed against broken and compares
the churn with what re-running the original produces. It is reported beside the strict verdict and
never replaces it until the team agrees (docs/CORRECTNESS.md section 4.4).

**Frozen on 1 Nov 2026, before any run judged it.** It was written after the 1 Nov results were
seen, so those results cannot validate it. It is judged on the next labelled run. Changing a
number below after that run is tuning on the evidence, and makes it a new rule with a new id.

`net-1` approves only when all of these hold:

1. **Hard blocks are untouched.** Every strict reason other than counted breaks still blocks:
   crashes, failed invariants, changed side effects, no cold win above the noise band, mixed splits
   or conditions (`VerificationReport.other_reasons`). A broken task whose side effects changed, or
   that failed an invariant, blocks on its own.
2. **Accuracy does not fall.** On cold runs, tasks fixed >= tasks broken.
3. **Churn stays within repeat noise.** Fixed plus broken, the answers that changed, is not
   significantly more than the answers that changed when the unmodified graph simply ran again on
   the same tasks: one-sided Fisher exact test, alpha 0.05. An optimization that breaks ten and
   fixes ten has changed behaviour even though it nets to zero.
4. **No repeat run, no net verdict.** Without the unmodified graph run twice there is nothing to
   compare churn with, so the rule blocks and says why.

McNemar's exact two-sided p-value for fixed against broken is reported too, so a reader can see
whether the optimization is significantly better or worse. It does not decide anything.
"""

from __future__ import annotations

from math import comb
from typing import Literal

from pydantic import BaseModel, Field

RULE_ID = "net-1"
FROZEN_ON = "2026-11-01"
ALPHA = 0.05


class NetVerdict(BaseModel):
    rule: str = RULE_ID
    frozen_on: str = FROZEN_ON
    in_force: bool = False  # the strict verdict is the verdict until the team decides otherwise
    decision: Literal["APPROVE", "BLOCK"]
    reasons: list[str] = Field(default_factory=list)
    relation: str
    split: str
    condition: str = "cold"
    tasks: int
    fixed: int
    broken: int
    net: int
    churn: int
    repeat_churn: int | None = None
    repeat_tasks: int | None = None
    p_churn_above_repeat: float | None = None
    p_mcnemar: float | None = None
    alpha: float = ALPHA


def fisher_greater(a: int, n1: int, c: int, n2: int) -> float:
    """One-sided Fisher exact p-value that rate a/n1 is greater than c/n2."""
    if n1 <= 0 or n2 <= 0:
        return 1.0
    total, hits = n1 + n2, a + c
    denominator = comb(total, hits)
    return sum(comb(n1, k) * comb(n2, hits - k) for k in range(a, min(n1, hits) + 1)) / denominator


def mcnemar_exact(fixed: int, broken: int) -> float:
    """Two-sided exact McNemar p-value on the discordant pairs."""
    n = fixed + broken
    if n == 0:
        return 1.0
    tail = sum(comb(n, k) for k in range(0, min(fixed, broken) + 1)) / 2**n
    return min(1.0, 2 * tail)


def net_verdict(report, repeat: dict | None) -> NetVerdict:
    """`net-1` on a firewall report. `repeat` is the unmodified graph run twice on the same tasks:
    `{"broke": int, "fixed": int, "tasks": int}` (bench/suite.py `repeat_flips`)."""
    rows = [r for r in report.per_task if r.cold]
    fixed = sum(r.status == "fixed" for r in rows)
    broken = sum(r.status == "broken" for r in rows)
    reasons = list(report.other_reasons)
    structural = [r.task_id for r in rows if r.status == "broken" and r.invariant_failures]
    if structural:
        reasons.append(f"{len(structural)} broken task(s) also changed side effects or failed an invariant: "
                       f"{', '.join(structural[:5])}")
    if fixed < broken:
        reasons.append(f"accuracy fell: {broken} broken against {fixed} fixed")

    churn, judged = fixed + broken, len([r for r in rows if r.status != "crashed"])
    repeat_churn = repeat_tasks = p_churn = None
    if not repeat or not repeat.get("tasks"):
        reasons.append("no repeat run of the unmodified graph, so churn cannot be compared with noise")
    else:
        repeat_churn, repeat_tasks = int(repeat["broke"]) + int(repeat["fixed"]), int(repeat["tasks"])
        p_churn = fisher_greater(churn, judged, repeat_churn, repeat_tasks)
        if p_churn < ALPHA:
            reasons.append(f"{churn} of {judged} answers changed against {repeat_churn} of {repeat_tasks} when the "
                           f"original simply ran again (p = {p_churn:.3f} < {ALPHA}): more churn than noise")

    return NetVerdict(
        decision="BLOCK" if reasons else "APPROVE",
        reasons=reasons or [f"no hard block; {fixed} fixed >= {broken} broken; churn within repeat noise"],
        relation=report.verdict.relation, split=report.verdict.split,
        tasks=judged, fixed=fixed, broken=broken, net=fixed - broken, churn=churn,
        repeat_churn=repeat_churn, repeat_tasks=repeat_tasks, p_churn_above_repeat=p_churn,
        p_mcnemar=mcnemar_exact(fixed, broken),
    )
