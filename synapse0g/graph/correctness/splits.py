"""Splits are by task id (CLAUDE.md rule 6, docs/BENCHMARKS.md §5).

    train   60 %   fitting the learned pass policy and the cost model
    calib   20 %   thresholds: early-exit gate, noise band, risk priors
    test    20 %   every headline number, touched once

A task, its variants and its optimized forms carry the same base task id, so the
same question never sits on both sides of the line. Executions are never split:
two executions of one task are not independent samples.

The bucket comes from sha1, not Python's hash(): hash() of a string is salted per
process, so two machines would split the same task differently.
"""

from __future__ import annotations

import hashlib

BUCKETS = (("train", 60), ("calib", 80), ("test", 100))
SPLITS = tuple(name for name, _ in BUCKETS)
VARIANT_SEP = "#"  # "hotpot_abc#swap-entities" is a variant of "hotpot_abc"


def base_id(task_id: str) -> str:
    return task_id.split(VARIANT_SEP, 1)[0]


def bucket(task_id: str) -> int:
    """Stable 0-99 bucket for a task id; every variant lands in its base task's bucket."""
    return int(hashlib.sha1(base_id(task_id).encode("utf-8")).hexdigest()[:8], 16) % 100


def split_for(task_id: str) -> str:
    b = bucket(task_id)
    return next(name for name, upper in BUCKETS if b < upper)


# Legacy name used by manifests and the demo.
split_for_task = split_for
