"""Split discipline: every number we show depends on these holding (CLAUDE.md rule 6)."""

import os
import subprocess
import sys
from collections import Counter

from synapse0g.graph.bench.splits import SPLITS, bucket, split_for


def test_split_is_a_stable_function_of_the_id():
    # pinned values: changing the hash or the bucket edges silently reshuffles train and test
    assert bucket("hotpot_5a8b57f25542995d1e6f1371") == 0
    assert split_for("hotpot_5a8b57f25542995d1e6f1371") == "train"
    assert bucket("gsm8k_0002") == 63
    assert split_for("gsm8k_0002") == "calib"


def test_split_is_the_same_in_a_fresh_process():
    # Python's hash() is salted per process; this fails if anyone "simplifies" to it
    code = "from synapse0g.graph.bench.splits import bucket; print(bucket('hotpot_5a8b57f25542995d1e6f1371'))"
    for seed in ("1", "2"):
        out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True,
                             env={**os.environ, "PYTHONHASHSEED": seed})
        assert out.stdout.strip() == "0"


def test_a_variant_shares_its_base_task_split():
    for i in range(500):
        task_id = f"task_{i}"
        assert split_for(f"{task_id}#swap-entities") == split_for(task_id)
        assert split_for(f"{task_id}#a#b") == split_for(task_id)


def test_shares_are_about_60_20_20():
    counts = Counter(split_for(f"task_{i}") for i in range(10_000))
    assert set(counts) == set(SPLITS)
    assert 5800 < counts["train"] < 6200
    assert 1800 < counts["calib"] < 2200
    assert 1800 < counts["test"] < 2200
