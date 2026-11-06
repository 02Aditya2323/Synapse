"""synapse0g.wrap: calls recorded on the app's own client, lifted into a graph with the right edges."""

from __future__ import annotations

import json

import synapse0g
from synapse0g.recorder import report_of

POLICY = "Refunds within 30 days with a receipt. Damaged items are replaced free. Shipping takes 3 to 5 days."
SUMMARY = "Refunds within 30 days with receipt; damaged items replaced free; shipping 3-5 days."
TICKETS = [("My blender arrived cracked.", "damage"), ("I bought shoes 45 days ago.", "refund"), ("Where is my order?", "shipping")]


def _support_trace() -> list[dict]:
    calls = []

    def call(prompt, out):
        calls.append({"model": "m", "messages": [{"role": "user", "content": prompt}], "output": out, "latency_ms": 5.0,
                      "tokens_in": 10, "tokens_out": 2, "params": {"temperature": 0}})

    for ticket, category in TICKETS:
        call(f"Classify this ticket as refund, damage or shipping. One word.\nTicket: {ticket}", category)
        call(f"Summarize this policy in one sentence: {POLICY}", SUMMARY)
        call(f"Category: {category}\nPolicy: {SUMMARY}\nWrite a two-sentence reply to: {ticket}", "reply: " + ticket)
    return calls


def test_a_one_word_answer_links_to_the_step_that_used_it_and_not_to_its_template():
    r = report_of("support", "support-1", _support_trace())
    edges = {(e["from"], e["to"]) for e in r["before"]["edges"]["data"]}
    assert ("n0", "n2") in edges and ("n3", "n5") in edges and ("n6", "n8") in edges  # category -> its reply
    assert ("n0", "n3") not in edges and ("n3", "n6") not in edges  # the classifier template is not a dependency
    assert ("n6", "n7") not in edges  # "shipping" vs the policy's "Shipping": case counts


def test_the_repeated_policy_call_is_merged_and_independent_calls_overlap():
    r = report_of("support", "support-1", _support_trace())
    assert r["repeated_calls"] == 2
    cse = next(p for p in r["passes"] if p["pass_name"] == "cse")
    assert cse["applied"] and len(cse["transformations"]) == 2
    assert ["n0", "n1", "n3", "n6"] in r["after"]["meta"]["parallel_groups"]
    assert {p["pass_name"] for p in r["passes"]} == {"cse", "memoize", "parallelize"}  # never dce or downgrade on a trace
    assert [x["node"] for x in r["waste"]["removable"]] == ["n4", "n7"]  # only the merged repeats


def test_wrap_passes_calls_through_and_saves_no_prompt_text(tmp_path):
    class Reply:
        def __init__(self, text):
            self.text = text

        def model_dump(self):
            return {"choices": [{"message": {"content": self.text}}], "usage": {"prompt_tokens": 7, "completion_tokens": 3}}

    class Completions:
        def create(self, **kw):
            return Reply("SECRET-ANSWER")

    class Client:
        chat = type("Chat", (), {"completions": Completions()})()

    client = synapse0g.wrap(Client(), workflow="w", out_dir=tmp_path, at_exit=False)
    reply = client.chat.completions.create(model="m", messages=[{"role": "user", "content": "SECRET-PROMPT"}])
    assert reply.text == "SECRET-ANSWER"
    saved = client.synapse0g_flush().read_text()
    assert "SECRET-PROMPT" not in saved and "SECRET-ANSWER" not in saved
    assert json.loads(saved)["calls"][0]["tokens_in"] == 7
