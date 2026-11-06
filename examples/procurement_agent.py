"""A purchasing agent: an office request plus supplier quotations in, a purchase request out.

Plain Python, written the way such an agent usually is: one function after another. It reads the
request and the company purchasing policy, reads each quotation (PDF, spreadsheet or email text),
checks every offer against the policy in code, has the model explain the choice, drafts the
purchase request with its evidence, and files it for a person's approval. Nothing is ever sent
before that approval, and the same request is never filed twice.

The model reads and explains; code decides. A missing price, date or spec makes an offer
"incomplete", never a guess.

    python examples/procurement_agent.py PR-DEMO          # run the demo request, as written
    python examples/procurement_agent.py PR-DEMO --compiled   # the same, compiled by synapse0g
    python examples/procurement_agent.py approve <request id>  # a person approves: it is submitted once

Two lines connect it to synapse0g: the client import below, and `synapse0g.compile(handle_request, ...)`.
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import sqlite3
import sys
import time
from datetime import date
from pathlib import Path

from synapse0g.openai import OpenAI  # synapse0g line 1 of 2 (was: from openai import OpenAI)

MODEL = os.environ.get("PROCUREMENT_MODEL", "zg-main")
GST = 0.18
MAX_ADVANCE_PERCENT = 30
DB = Path(os.environ.get("PROCUREMENT_DB", Path(__file__).resolve().parent.parent / "data" / "procurement.db"))
client = OpenAI()

# the fields our purchasing system keeps for each offer; specs by product category
SPEC_FIELDS = {
    "monitor": ["screen_size_in", "resolution"], "laptop": ["ram_gb", "storage_gb"],
    "chair": ["lumbar_support", "warranty_years"], "printer": ["duplex", "pages_per_minute"],
    "ups": ["capacity_va"], "headset": ["noise_cancelling", "warranty_years"],
    "projector": ["brightness_lumens"], "desk": ["width_cm"],
}
ALL_SPECS = sorted({f for fs in SPEC_FIELDS.values() for f in fs})


def ask(system: str, user: str) -> str:
    reply = client.chat.completions.create(model=MODEL, temperature=0, seed=7,
                                           messages=[{"role": "system", "content": system}, {"role": "user", "content": user}])
    return reply.choices[0].message.content or ""


def parse_json(text: str) -> dict:
    found = re.findall(r"\{.*\}", text, re.S)
    try:
        return json.loads(found[-1]) if found else {}
    except json.JSONDecodeError:
        return {}


# ---- steps ---------------------------------------------------------------------------------
def load_policy(policy_path):
    with open(policy_path, encoding="utf-8") as f:
        return f.read()


def load_documents(quotation_paths):
    """Each quotation as text, with page / row / line markers so findings can cite their source."""
    from openpyxl import load_workbook
    from pypdf import PdfReader

    documents = []
    for path in map(Path, quotation_paths):
        if path.suffix == ".pdf":
            pages = [p.extract_text() or "" for p in PdfReader(path).pages]
            text = "\n".join(f"[page {i + 1}]\n{t}" for i, t in enumerate(pages))
        elif path.suffix in (".xlsx", ".xls"):
            sheet = load_workbook(path, read_only=True).active
            text = "\n".join(f"[row {i}] " + " | ".join(str(c) for c in row if c is not None)
                             for i, row in enumerate(sheet.iter_rows(values_only=True), 1) if any(row))
        elif path.suffix == ".csv":
            with open(path, encoding="utf-8") as f:
                text = "\n".join(f"[row {i}] " + " | ".join(r) for i, r in enumerate(csv.reader(f), 1))
        else:
            text = "\n".join(f"[line {i}] {t}" for i, t in enumerate(path.read_text(encoding="utf-8").splitlines(), 1) if t)
        documents.append({"file": path.name, "text": text})
    return documents


def read_requirements(request, policy):
    text = ask(
        "You turn a purchase request into requirements, using the company purchasing policy for the minimum "
        "specifications. Reply with JSON only: {\"category\": one of " + json.dumps(list(SPEC_FIELDS)) + ", "
        "\"quantity\": int, \"max_unit_price_before_gst\": number, \"needed_within_days\": int, "
        "\"min_specs\": {field: minimum}} using only these spec fields for the category: " + json.dumps(SPEC_FIELDS)
        + ". Numeric minimums are numbers; resolution is like \"2560x1440\"; lumbar_support \"adjustable\"; "
        "duplex \"automatic\"; noise_cancelling \"active\".",
        f"Purchase request:\n{request}\n\nPurchasing policy:\n{policy}")
    return parse_json(text)


def extract_quotation(document):
    text = ask(
        "You read one supplier quotation and extract what it states, with where it says it. Reply with JSON "
        "only: {\"supplier\", \"item\", \"refurbished\": bool, \"specs\": {" + ", ".join(ALL_SPECS) + "}, "
        "\"quantity_available\": int, \"unit_price\": number, \"prices_include_gst\": bool, \"shipping\": number "
        "(0 if free), \"delivery_days\": int, \"payment_terms\": text, \"advance_percent\": percent of the order "
        "value payable before delivery (0-100), \"valid_until\": \"YYYY-MM-DD\", \"sources\": {field: the "
        "[page n] / [row n] / [line n] marker it came from}}. Use null for anything the quotation does not "
        "state; never guess. Specs: numbers for sizes, RAM, storage, warranty years, pages per minute, VA, "
        "lumens and cm; resolution as \"WxH\"; lumbar_support \"adjustable\"/\"fixed\"; duplex "
        "\"automatic\"/\"manual\"; noise_cancelling \"active\"/\"none\".",
        f"File: {document['file']}\n{document['text']}")
    offer = parse_json(text)
    offer["file"] = document["file"]
    return offer


def _spec_ok(have, need) -> bool:
    if have is None:
        return False
    if isinstance(need, (int, float)):
        try:
            return float(have) >= float(need)
        except (TypeError, ValueError):
            return False
    if "x" in str(need):
        try:
            hw, nw = [int(v) for v in str(have).lower().split("x")], [int(v) for v in str(need).lower().split("x")]
            return hw[0] >= nw[0] and hw[1] >= nw[1]
        except ValueError:
            return False
    return str(have).strip().lower() == str(need).strip().lower()


def _number(v, free_is_zero=False):
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float)):
        return v
    text = str(v).replace(",", "")
    if free_is_zero and "free" in text.lower():
        return 0.0
    m = re.search(r"-?\d+(?:\.\d+)?", text)
    return float(m.group()) if m else None


def _date(v):
    try:
        return date.fromisoformat(str(v)[:10])
    except ValueError:
        return None


def check_offers(requirements, offers):
    """The policy, in code: every offer is eligible, ineligible (with reasons) or incomplete."""
    today = date.fromisoformat(os.environ.get("PROCUREMENT_ORDER_DATE", "2026-11-06"))
    qty = int(requirements.get("quantity") or 0)
    cap = float(requirements.get("max_unit_price_before_gst") or 0)
    days = int(requirements.get("needed_within_days") or 0)
    specs_needed = requirements.get("min_specs") or {}
    rows = []
    for o in offers:
        specs = o.get("specs") or {}
        # numbers as the model wrote them ("1,500", "Free delivery"); one that cannot be read counts as not stated
        o = dict(o, **{k: _number(o.get(k)) for k in ("unit_price", "quantity_available", "delivery_days", "advance_percent")},
                 shipping=_number(o.get("shipping"), free_is_zero=True))
        needed = ["unit_price", "quantity_available", "delivery_days", "valid_until", "payment_terms", "advance_percent"]
        missing = [k for k in needed if o.get(k) in (None, "")] + [f"spec {k}" for k in specs_needed if specs.get(k) is None]
        if _date(o.get("valid_until")) is None and "valid_until" not in missing:
            missing.append("valid_until")
        row = {"supplier": o.get("supplier"), "file": o.get("file"), "sources": o.get("sources") or {}, "reasons": []}
        if missing:
            row.update(status="incomplete", reasons=[f"does not state {', '.join(missing)}"])
            rows.append(row)
            continue
        price = float(o["unit_price"])
        ex_gst = price / (1 + GST) if o.get("prices_include_gst") else price
        landed = round((price if o.get("prices_include_gst") else price * (1 + GST)) * qty + float(o.get("shipping") or 0), 2)
        checks = [
            (_date(o["valid_until"]) >= today, f"quotation expired on {o['valid_until']}"),
            (int(o["quantity_available"]) >= qty, f"only {o['quantity_available']} available, {qty} needed"),
            (int(o["delivery_days"]) <= days, f"delivers in {o['delivery_days']} days, needed within {days}"),
            (ex_gst <= cap + 0.5, f"unit price before GST {ex_gst:,.0f} is above the maximum {cap:,.0f}"),
            (float(o["advance_percent"]) <= MAX_ADVANCE_PERCENT, f"{o['advance_percent']}% payable in advance (policy max 30%)"),
            (not o.get("refurbished"), "refurbished goods are not acceptable"),
        ] + [(_spec_ok(specs.get(k), v), f"{k} {specs.get(k)} below the required {v}") for k, v in specs_needed.items()]
        reasons = [why for ok, why in checks if not ok]
        row.update(status="ineligible" if reasons else "eligible", reasons=reasons, landed_total=landed,
                   unit_price_before_gst=round(ex_gst, 2), delivery_days=int(o["delivery_days"]))
        rows.append(row)
    eligible = sorted((r for r in rows if r["status"] == "eligible"), key=lambda r: (r["landed_total"], r["delivery_days"]))
    if eligible:
        decision = {"status": "selected", "supplier": eligible[0]["supplier"], "landed_total": eligible[0]["landed_total"]}
    elif any(r["status"] == "incomplete" for r in rows):
        decision = {"status": "needs_clarification", "supplier": None, "landed_total": None}
    else:
        decision = {"status": "no_eligible_offer", "supplier": None, "landed_total": None}
    return {"decision": decision, "offers": rows}


def recommend(requirements, evaluation):
    return ask(
        "You are a purchasing assistant. The decision below was made by the company's rules; do not change it. "
        "In 3 to 5 sentences, explain it to the approver: why the chosen offer wins, the trade-offs against "
        "the other eligible offers, and why the others were excluded. If nothing was chosen, say what is "
        "needed next. Name the chosen supplier and its landed cost.",
        f"Requirements: {json.dumps(requirements)}\nEvaluation: {json.dumps(evaluation)}")


def draft_purchase_request(requirements, evaluation, recommendation):
    decision = evaluation["decision"]
    body = {"decision": decision, "quantity": requirements.get("quantity"), "category": requirements.get("category"),
            "justification": recommendation.strip(),
            "evidence": [{k: r.get(k) for k in ("supplier", "file", "status", "reasons", "landed_total", "sources")}
                         for r in evaluation["offers"]]}
    body["id"] = "PR-" + hashlib.sha256(json.dumps([requirements, decision], sort_keys=True).encode()).hexdigest()[:10]
    body["needs_finance_approval"] = bool(decision.get("landed_total") and decision["landed_total"] > 500000)
    return body


def file_for_approval(draft):
    """Files the draft as pending approval. External: it changes our records. Never twice for one request."""
    if os.environ.get("PROCUREMENT_STAGING") == "1":
        return {"status": "not filed (staging run)"}
    DB.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DB) as db:
        db.execute("create table if not exists requests (id text primary key, created real, draft text, status text, "
                   "decided_at real)")
        db.execute("insert or ignore into requests values (?, ?, ?, 'pending', null)", (draft["id"], time.time(), json.dumps(draft)))
    return {"status": "pending approval", "id": draft["id"]}


# ---- the agent, as written ---------------------------------------------------------------------
def handle_request(request, policy_path, quotation_paths):
    policy = load_policy(policy_path)
    documents = load_documents(quotation_paths)
    requirements = read_requirements(request, policy)
    offers = [extract_quotation(document) for document in documents]
    evaluation = check_offers(requirements, offers)
    recommendation = recommend(requirements, evaluation)
    draft = draft_purchase_request(requirements, evaluation, recommendation)
    file_for_approval(draft)
    return draft


# What each step does to the world, for synapse0g. The rest it works out from the code.
EFFECTS = {
    "load_documents": {"effect": "read", "staleness_s": 3600},
    "read_requirements": {"effect": "pure", "determinism": "seeded"},
    "extract_quotation": {"effect": "pure", "determinism": "seeded"},  # same document, same reading
    "check_offers": "pure",  # the policy in code: same inputs, same answer
    "recommend": {"effect": "pure", "determinism": "seeded"},
    "draft_purchase_request": "pure",
    "file_for_approval": "external",
}


# ---- approval: a person's decision, outside the agent ------------------------------------------
def approve(request_id: str) -> str:
    """Submit an approved request exactly once. A second approval changes nothing."""
    with sqlite3.connect(DB) as db:
        row = db.execute("select status from requests where id = ?", (request_id,)).fetchone()
        if row is None:
            return "no such request"
        if row[0] != "pending":
            return f"already {row[0]}; nothing submitted again"
        db.execute("update requests set status = 'submitted', decided_at = ? where id = ? and status = 'pending'",
                   (time.time(), request_id))
    return "approved and submitted"


if __name__ == "__main__":
    from synapse0g.agents.procurement_desk import DATA

    if sys.argv[1:2] == ["approve"]:
        print(approve(sys.argv[2]))
        raise SystemExit
    folder = DATA / (sys.argv[1] if len(sys.argv) > 1 else "PR-DEMO")
    files = sorted(str(p) for p in folder.glob("quotation_*"))
    request = (folder / "request.txt").read_text(encoding="utf-8")
    t0 = time.perf_counter()
    if "--compiled" in sys.argv:
        import synapse0g

        app = synapse0g.compile(handle_request, effects=EFFECTS)  # synapse0g line 2 of 2
        print(app.explain())
        draft = app.invoke(request=request, policy_path=str(DATA / "purchasing_policy.txt"), quotation_paths=files)
    else:
        draft = handle_request(request, str(DATA / "purchasing_policy.txt"), files)
    print(json.dumps(draft["decision"]), f"{time.perf_counter() - t0:.1f}s")
    print(draft["justification"])
