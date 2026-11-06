"""A synthetic supplier marketplace: the outside world the supplier-check agent works against.

Every request for quotation (RFQ) has five suppliers with prices, stock, lead times, customer
reviews and quality certificates. The data is generated from a fixed seed, so every run sees the
same marketplace, and the right supplier for each RFQ is computed here by code from the company
policy below. That is what makes each task checkable exactly.

The reviews and certificates are prose, on purpose: deciding that "valid until 30 June 2026" is
expired, or that "two shipments were counterfeit" disqualifies a supplier, is the work the agent's
model does. The numbers come from lookups, as they would from a real supplier API.

`submit_order` is the irreversible step. Here it writes to an in-memory order book per RFQ, the
only place an order may be placed during testing.
"""

from __future__ import annotations

import random
import threading
from dataclasses import dataclass

TODAY = "2026-11-06"
N_RFQS = 60
SEED = 2026

POLICY = """ACME Industrial: Purchasing Policy, revision 7 (applies to all purchase orders)

1. Scope. This policy applies to every purchase of goods for production or maintenance. Services,
travel and software licences are covered by separate policies and are out of scope here.

2. Approved suppliers. A supplier may receive a purchase order only if ALL of the following hold
on the order date:
  2.1 Stock: the supplier has at least the full requested quantity in stock. Split orders across
      suppliers are not allowed.
  2.2 Delivery: the supplier's quoted lead time, in days, is no longer than the number of days
      within which the goods are needed.
  2.3 Price ceiling: the quoted unit price does not exceed the maximum unit price on the request.
  2.4 Quality: the supplier holds an ISO 9001 quality-management certificate that is valid on the
      order date. A certificate that has expired, is suspended, or is still pending does not count.
      Environmental (ISO 14001) or safety (ISO 45001) certificates do not replace ISO 9001.
  2.5 Conduct: no customer review reports counterfeit or fake goods, a safety incident (fire,
      injury, toxic material), or goods not delivered after payment. Ordinary complaints, such as
      slow replies, late invoices or poor packaging with intact goods, do not disqualify.

3. Choosing between approved suppliers. Order from the approved supplier with the lowest total
cost, where total cost = unit price x quantity + the supplier's flat shipping fee. If two totals
are equal, choose the shorter lead time.

4. No approved supplier. If no supplier meets every condition in section 2, place no order and
report that the request needs a manual sourcing review.

5. Records. Each purchase order states the supplier id, the quantity, the unit price and the total
cost. Purchase orders are final once submitted: cancellations go through the supplier's own
returns process and may carry fees.

6. Approvals. Orders above USD 250,000 also need sign-off from the finance director. This is
handled after submission by the finance team and does not change the choice of supplier.

7. Expenses. Staff travel and meals are reimbursed under the Travel Policy. Receipts must be kept
for seven years. None of this affects purchase orders.
"""

ITEMS = [
    ("hydraulic hose, 1/2 inch, per metre", 4.0), ("M8 stainless hex bolt", 0.18), ("nitrile gloves, box of 100", 6.5),
    ("industrial bearing 6204-2RS", 2.9), ("LED panel light 60x60", 21.0), ("copper cable 2.5mm, per metre", 0.95),
    ("pneumatic cylinder 50mm", 48.0), ("safety helmet, EN 397", 9.5), ("pallet stretch film roll", 14.0),
    ("PLC input module, 16 channel", 185.0), ("stainless pipe 2 inch, per metre", 22.0), ("V-belt A42", 5.2),
    ("hi-vis vest", 3.1), ("industrial fan motor 0.75kW", 132.0), ("PTFE thread tape roll", 0.6),
    ("forklift battery 48V", 2450.0), ("welding rod E6013, 5kg pack", 18.0), ("air filter cartridge", 27.0),
    ("cable tie 300mm, pack of 100", 2.2), ("conveyor roller 50mm", 11.5),
]
NAMES = ["Northwind Supply", "Brightline Parts", "Kestrel Industrial", "Orbit Components", "Harbor Trade Co",
         "Summit Wholesale", "Pioneer Hardware", "Redwood Distributors", "Atlas Fittings", "Bluegate Traders",
         "Granite Supply Group", "Meridian Industrial", "Falcon Parts", "Lakeside Equipment", "Sterling Source",
         "Cobalt Trading", "Evergreen Materials", "Ironbridge Supply", "Silverline Imports", "Vantage Components"]

GOOD = ["Reliable supplier, every delivery arrived on the promised day.", "Good quality and accurate paperwork.",
        "We have ordered from them for three years without a problem.", "Prices are fair and the parts match the spec.",
        "Helpful account manager, quick to confirm orders."]
MINOR = ["Packaging was flimsy, but every item arrived intact.", "Replies to emails are slow, sometimes two days.",
         "The invoice arrived a week late, which annoyed our accounts team.", "Their website is clunky to order from.",
         "One delivery came a day early and we had nowhere to store it."]
SERIOUS = ["About a third of the last batch turned out to be counterfeit: the serial numbers did not match the maker's records.",
           "We paid in full upfront and the goods never arrived. They stopped answering our emails.",
           "One unit caught fire during bench testing and they refused to recall the batch.",
           "The gloves contained a toxic dye that gave two of our staff skin burns.",
           "The 'genuine' parts we received were fakes; the manufacturer confirmed it in writing."]


@dataclass(frozen=True)
class Supplier:
    id: str
    name: str
    unit_price: float
    shipping_fee: float
    stock: int
    lead_days: int
    reviews: tuple[str, ...]
    certificate: str
    # the facts behind the prose, used only to compute the right answer
    cert_valid: bool
    serious_review: bool


def _certificate(rng: random.Random, valid: bool) -> str:
    no = f"QM-{rng.randint(10000, 99999)}"
    if valid:
        year = rng.choice([2027, 2028, 2029])
        month = rng.choice(["January", "March", "May", "August", "October", "December"])
        return rng.choice([
            f"ISO 9001:2015 certificate no. {no}, issued by Bureau Certis, valid until {rng.randint(1, 28)} {month} {year}.",
            f"Quality management: ISO 9001:2015 (certificate {no}). Expiry date: {month} {rng.randint(1, 28)}, {year}.",
            f"Holds ISO 9001:2015, certificate {no}, renewed this year and valid through {month} {year}. Also ISO 14001.",
        ])
    return rng.choice([
        f"ISO 9001:2015 certificate no. {no}, valid until {rng.randint(1, 28)} {rng.choice(['February', 'April', 'June', 'September'])} 2026.",
        f"ISO 9001 audit scheduled for {rng.choice(['January', 'February', 'March'])} 2027; certificate pending.",
        f"ISO 14001 environmental certificate {no}, valid until 2028. No quality-management certificate on file.",
        f"ISO 9001:2015 certificate {no} suspended by the certification body in {rng.choice(['August', 'September', 'October'])} 2026 pending a corrective audit.",
        f"ISO 45001 occupational safety certificate, valid until 2029. ISO 9001 lapsed in {rng.choice(['March', 'May'])} 2025 and was not renewed.",
    ])


def _total(s: Supplier, qty: int) -> float:
    return round(s.unit_price * qty + s.shipping_fee, 2)


def _rfq(i: int) -> dict:
    rng = random.Random(SEED * 1000 + i)
    item, base = ITEMS[i % len(ITEMS)]
    qty = rng.choice([50, 100, 120, 200, 250, 400, 500, 1000]) if base < 100 else rng.choice([4, 6, 10, 12, 20])
    if base > 1000:
        qty = rng.choice([2, 3, 4])
    need = rng.choice([7, 10, 14, 21, 30])
    max_price = round(base * rng.uniform(1.15, 1.35), 2)
    names = rng.sample(NAMES, 5)
    no_answer = rng.random() < 0.12
    suppliers = []
    for j, name in enumerate(names):
        price = round(base * rng.uniform(0.80, 1.25), 2)
        fee = float(rng.choice([0, 25, 40, 60, 90, 150]))
        stock = rng.choice([qty * 2, qty * 3, qty + rng.randint(0, qty), max(qty - rng.randint(1, qty // 2 + 1), 0)])
        lead = rng.choice([2, 3, 5, 7, 10, 12, 18, 25])
        # each supplier fails at most one rule, or none, so every wrong pick has one clear reason
        fault = rng.choices(["none", "cert", "review", "stock", "lead", "price"], [0.34, 0.2, 0.18, 0.1, 0.1, 0.08])[0]
        if no_answer and fault == "none":
            fault = rng.choice(["cert", "review", "stock", "lead"])
        cert_valid = fault != "cert"
        serious = fault == "review"
        stock = max(stock, qty) if fault != "stock" else max(qty - rng.randint(1, max(qty // 2, 1)), 0)
        lead = min(lead, need) if fault != "lead" else need + rng.choice([2, 5, 9])
        price = min(price, max_price) if fault != "price" else round(max_price * rng.uniform(1.03, 1.12), 2)
        reviews = rng.sample(GOOD, 1) + rng.sample(MINOR, 1)
        if serious:
            reviews.append(rng.choice(SERIOUS))
        else:
            reviews.append(rng.choice(GOOD + MINOR))
        rng.shuffle(reviews)
        suppliers.append(Supplier(f"S{i:02d}{j}", name, price, fee, stock, lead, tuple(reviews),
                                  _certificate(rng, cert_valid), cert_valid, serious))
    # the trap: often the cheapest-looking supplier is the one that fails a rule
    return {"rfq_id": f"RFQ-{i:03d}", "item": item, "quantity": qty, "needed_within_days": need,
            "max_unit_price": max_price, "order_date": TODAY, "suppliers": suppliers}


def approved(s: Supplier, rfq: dict) -> bool:
    return (s.stock >= rfq["quantity"] and s.lead_days <= rfq["needed_within_days"]
            and s.unit_price <= rfq["max_unit_price"] and s.cert_valid and not s.serious_review)


def right_answer(rfq: dict) -> str | None:
    """Section 3 of the policy, applied by code: the supplier the agent should order from, or None."""
    ok = [s for s in rfq["suppliers"] if approved(s, rfq)]
    if not ok:
        return None
    return min(ok, key=lambda s: (_total(s, rfq["quantity"]), s.lead_days)).id


_RFQS = {r["rfq_id"]: r for r in (_rfq(i) for i in range(N_RFQS))}


def rfq_ids() -> list[str]:
    return list(_RFQS)


def request(rfq_id: str) -> dict:
    """What a buyer submits: the RFQ without the marketplace data."""
    r = _RFQS[rfq_id]
    return {k: r[k] for k in ("rfq_id", "item", "quantity", "needed_within_days", "max_unit_price", "order_date")}


# ---- the supplier API the agent calls ------------------------------------------------
def quotes(rfq_id: str) -> list[dict]:
    return [{"supplier_id": s.id, "name": s.name, "unit_price": s.unit_price, "shipping_fee": s.shipping_fee,
             "stock": s.stock, "lead_days": s.lead_days} for s in _RFQS[rfq_id]["suppliers"]]


def reviews(rfq_id: str) -> list[dict]:
    return [{"supplier_id": s.id, "name": s.name, "reviews": list(s.reviews)} for s in _RFQS[rfq_id]["suppliers"]]


def certificates(rfq_id: str) -> list[dict]:
    return [{"supplier_id": s.id, "name": s.name, "certificate": s.certificate} for s in _RFQS[rfq_id]["suppliers"]]


def company_policy() -> str:
    return POLICY


_orders: dict[str, list[dict]] = {}
_orders_lock = threading.Lock()


def submit_order(order: dict) -> dict:
    """Irreversible in the real world. Here: the in-memory order book of a test marketplace."""
    with _orders_lock:
        book = _orders.setdefault(str(order.get("rfq_id")), [])
        book.append(dict(order))
        return {"status": "submitted", "po_number": f"PO-{order.get('rfq_id')}-{len(book)}"}


def orders_for(rfq_id: str) -> list[dict]:
    with _orders_lock:
        return list(_orders.get(rfq_id, []))
