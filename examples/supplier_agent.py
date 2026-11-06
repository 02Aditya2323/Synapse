"""A supplier-check agent: an ordinary LangGraph app, written the way such apps usually are.

It gets a request for quotation, reads the company purchasing policy, looks up quotes, reviews and
certificates from the supplier API, picks the supplier the policy allows, and places the order.
The nodes run one after another because that is how it was written, not because they need to.

Three lines connect it to synapse0g (marked below). Nothing else in the app knows about it.

    python examples/supplier_agent.py RFQ-003            # compile, show the plan, run one request
    python examples/supplier_agent.py RFQ-003 --order    # same, and really place the order
"""

from __future__ import annotations

import json
import re
import sys
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from synapse0g.agents import supplier_desk as api  # the supplier marketplace's client
from synapse0g.openai import OpenAI  # synapse0g line 1 of 3 (was: from openai import OpenAI)

MODEL = "zg-main"  # 0G qwen3.8-flash, by its models.yaml id
client = OpenAI()


class State(TypedDict, total=False):
    rfq: dict
    checklist: str
    quotes: str
    review_flags: str
    certificates: str
    order: dict
    receipt: dict


def ask(system: str, user: str, *, seed: int | None = None) -> str:
    extra = {"seed": seed} if seed is not None else {}
    reply = client.chat.completions.create(model=MODEL, temperature=0, **extra,
                                           messages=[{"role": "system", "content": system},
                                                     {"role": "user", "content": user}])
    return reply.choices[0].message.content or ""


def load_policy(state: State) -> dict:
    """The same policy every time, summarised into the rules a buyer checks."""
    return {"checklist": ask("Purchasing policy summariser. Turn the policy into a short numbered checklist of "
                             "the conditions a supplier must meet and how to choose between suppliers.",
                             api.company_policy(), seed=7)}


def get_quotes(state: State) -> dict:
    rfq = state["rfq"]
    data = api.quotes(rfq["rfq_id"])
    return {"quotes": ask("Quote analyst. For each supplier, say whether it has the full quantity in stock, whether "
                          "its lead time fits, whether its unit price is at or under the maximum, and its total "
                          "cost = unit price x quantity + shipping fee. One line per supplier id.",
                          f"Request: {json.dumps(rfq)}\nQuotes: {json.dumps(data)}")}


def screen_reviews(state: State) -> dict:
    rfq = state["rfq"]
    data = api.reviews(rfq["rfq_id"])
    return {"review_flags": ask("Supplier review screener. For each supplier id, answer SERIOUS if any review reports "
                                "counterfeit or fake goods, a safety incident, or goods not delivered after payment; "
                                "otherwise OK. Ordinary complaints are OK. One line per supplier id.",
                                json.dumps(data))}


def check_certificates(state: State) -> dict:
    rfq = state["rfq"]
    data = api.certificates(rfq["rfq_id"])
    return {"certificates": ask("Certificate checker. For each supplier id, answer VALID if it holds an ISO 9001 "
                                "certificate valid on the order date, otherwise INVALID with the reason. Expired, "
                                "suspended or pending certificates are INVALID; other ISO standards do not count. "
                                "One line per supplier id.",
                                f"Order date: {rfq['order_date']}\n{json.dumps(data)}")}


def decide(state: State) -> dict:
    rfq = state["rfq"]
    text = ask("Purchasing decision maker. Apply the checklist to the findings and choose the one supplier to order "
               "from, or none. End with one line of JSON: {\"supplier_id\": \"...\" or null, \"quantity\": n, "
               "\"total_cost\": x}.",
               f"Checklist:\n{state['checklist']}\n\nRequest: {json.dumps(rfq)}\n\nQuotes:\n{state['quotes']}\n\n"
               f"Reviews:\n{state['review_flags']}\n\nCertificates:\n{state['certificates']}")
    found = re.findall(r"\{[^{}]*\"supplier_id\"[^{}]*\}", text)
    try:
        choice = json.loads(found[-1]) if found else {}
    except json.JSONDecodeError:
        choice = {}
    return {"order": {"rfq_id": rfq["rfq_id"], "supplier_id": choice.get("supplier_id"),
                      "quantity": choice.get("quantity"), "total_cost": choice.get("total_cost")}}


def place_order(state: State) -> dict:
    order = state["order"]
    if not order.get("supplier_id"):
        return {"receipt": {"status": "no order: manual sourcing review"}}
    return {"receipt": api.submit_order(order)}


def build() -> StateGraph:
    g = StateGraph(State)
    steps = [load_policy, get_quotes, screen_reviews, check_certificates, decide, place_order]
    for f in steps:
        g.add_node(f.__name__, f)
    g.add_edge(START, steps[0].__name__)
    for a, b in zip(steps, steps[1:], strict=False):
        g.add_edge(a.__name__, b.__name__)
    g.add_edge(steps[-1].__name__, END)
    return g


# What each step does to the world. Anything not listed is treated as touching it.
EFFECTS = {
    "load_policy": {"effect": "pure", "determinism": "seeded"},  # same policy, seeded call
    "get_quotes": {"effect": "read", "staleness_s": 300},
    "screen_reviews": {"effect": "read", "staleness_s": 3600},
    "check_certificates": {"effect": "read", "staleness_s": 3600},
    "decide": "pure",
    "place_order": {"effect": "irreversible", "updates": ["receipt"]},  # money moves
}

builder = build()

if __name__ == "__main__":
    import synapse0g

    rfq = api.request(sys.argv[1] if len(sys.argv) > 1 else "RFQ-003")
    app = synapse0g.compile(builder, sample={"rfq": rfq}, effects=EFFECTS, name="supplier_check")  # synapse0g line 2 of 3
    print(app.explain())
    state = app.invoke({"rfq": rfq}, allow_irreversible="--order" in sys.argv)  # line 3 (was: builder.compile().invoke)
    print(json.dumps(state["order"]), "->", state.get("receipt"))
