"""synapse0g.compile on a plain Python function: the map runs side by side, declared effects win,
and the irreversible step never runs unasked."""

from __future__ import annotations

import time

import pytest

import synapse0g
from tests.plain import quote_agent as qa

REQUEST = {"qty": 15}
DOCS = [{"supplier": s, "price": p} for s, p in (("A", 12), ("B", 9), ("C", 11), ("D", 14))]
EFFECTS = {"extract_quote": {"effect": "pure", "determinism": "seeded"}, "read_request": "pure",
           "submit_purchase_request": "irreversible"}


def test_a_plain_function_compiles_and_its_map_runs_in_parallel(tmp_path):
    app = synapse0g.compile(qa.handle, effects=EFFECTS, cache_dir=tmp_path)
    assert {"parallelize"} <= set(app.applied)
    assert [r.kind for r in app.before.regions] == ["map"]
    qa.CALLS.clear()
    t0 = time.perf_counter()
    as_written = qa.handle(REQUEST, DOCS)  # what calling the function does: five steps one at a time
    serial = time.perf_counter() - t0
    qa.CALLS.clear()
    t0 = time.perf_counter()
    result = app.invoke(request=REQUEST, documents=DOCS, allow_irreversible=True)
    compiled = time.perf_counter() - t0
    assert result == as_written == {"submitted": "B"}
    assert compiled < serial * 0.6
    assert qa.CALLS.count("submit") == 1


def test_the_irreversible_step_is_skipped_unless_allowed(tmp_path):
    app = synapse0g.compile(qa.handle, effects=EFFECTS, cache_dir=tmp_path)
    qa.CALLS.clear()
    app.invoke(request=REQUEST, documents=DOCS)
    assert "submit" not in qa.CALLS
    assert "refused  memoize on" in app.explain() or any(f.node for p in app.passes for f in p.findings)


def test_a_declared_pure_extraction_is_reused_when_the_same_document_comes_back(tmp_path):
    app = synapse0g.compile(qa.handle, effects=EFFECTS, cache_dir=tmp_path, cache_scope="acme")
    app.invoke(request=REQUEST, documents=DOCS)
    qa.CALLS.clear()
    app.invoke(request={"qty": 20}, documents=DOCS)  # a new request, the same quotations
    assert qa.CALLS.count("extract_quote") == 0 and qa.CALLS.count("read_request") == 1
    other = synapse0g.compile(qa.handle, effects=EFFECTS, cache_dir=tmp_path, cache_scope="globex")
    qa.CALLS.clear()
    other.invoke(request=REQUEST, documents=DOCS)  # another customer never gets acme's extractions
    assert qa.CALLS.count("extract_quote") == 4


def test_unknown_declarations_are_refused():
    with pytest.raises(ValueError, match="not in"):
        synapse0g.compile(qa.handle, effects={"nope": "pure"})
