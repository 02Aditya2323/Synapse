"""Splits by task id: the implementation is in synapse/correctness/splits.py (CLAUDE.md rule 6).

It lives in the package because an installed copy of Synapse has no `bench`, and two split
functions would be a correctness bug.
"""

from synapse0g.graph.correctness.splits import BUCKETS, SPLITS, VARIANT_SEP, base_id, bucket, split_for

__all__ = ["BUCKETS", "SPLITS", "VARIANT_SEP", "base_id", "bucket", "split_for"]
