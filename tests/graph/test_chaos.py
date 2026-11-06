import pytest

from synapse0g.graph.correctness.chaos import run_chaos


@pytest.mark.asyncio
async def test_all_ten_unsafe_transformations_are_caught_without_errors():
    rows = await run_chaos()
    assert len(rows) == 10
    assert all(r.verdict == "BLOCK" and r.caught_by == r.expected_layer for r in rows), rows
