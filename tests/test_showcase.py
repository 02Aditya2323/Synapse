"""The dashboard feed is built from the saved reports and repeats their numbers exactly."""

from synapse0g import showcase


def test_feed_matches_the_reports():
    feed = showcase.feed()
    by_id = {r["id"]: r for r in feed["featured"]}
    research, maths, retail = by_id["research-0g"], by_id["math-groq"], by_id["retail-zerog"]

    assert research["verdict"]["decision"] == "APPROVE"
    assert round(research["headline"]["value"], 2) == 1.99
    assert research["billing"]["source"] == "listed"  # no 0G bill was stored for this run: no credit claim

    assert maths["verdict"]["decision"] == "APPROVE"
    assert round(maths["headline"]["value"], 3) == 0.775

    assert retail["verdict"]["decision"] == "BLOCK"
    assert retail["verdict"]["counts"]["broken"] == 5
    t028 = next(t for t in retail["task_rows"] if t["id"].endswith("028"))
    assert "cancel_pending_order" in t028["actions_after"]
    assert all("cancel_pending_order" not in a for a in t028["actions_before"])
    assert retail["billing"]["requests_with_bill"] == retail["billing"]["requests"]

    assert any(h["invalid"] for h in feed["history"])  # invalid runs stay listed


def test_every_metric_has_both_sides_or_says_so():
    for r in showcase.featured():
        for m in r["metrics"]:
            assert set(m) >= {"name", "unit", "before", "after", "change"}
