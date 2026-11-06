"""Purchase requests and supplier quotations: the documents the procurement agent works from.

A scenario is what lands on a purchasing desk: an office manager's request ("15 monitors, at most
INR 12,000 each, within seven days"), the company purchasing policy, and three or four supplier
quotations as they arrive by email: a PDF, a spreadsheet, or the email text itself.

The quotations are generated from a fixed seed, so every run reads the same documents, and the
right outcome for each request is computed here by code from the facts behind the documents. The
documents are prose and tables a person would read; the facts are never shown to the agent.

`python -m synapse0g.agents.procurement_desk` writes the documents to `procurement_data/`.
"""

from __future__ import annotations

import json
import random
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from pathlib import Path

DATA = Path(__file__).resolve().parent / "procurement_data"
ORDER_DATE = date(2026, 11, 6)
GST = 0.18
MAX_ADVANCE = 0.30  # policy: at most 30% of the order value paid before delivery
SEED = 611
N_SCENARIOS = 36

POLICY = """ACME Workplace Services Pvt. Ltd. - Purchasing Policy (rev. 4, applies from 1 April 2026)

1. Scope. Applies to every purchase of equipment and furniture for our offices. Services and
software licences follow the IT procurement policy instead.

2. Minimum specifications. An offer is acceptable only if the product meets the minimum for its
category:
   - Monitors: screen size at least 27 inches and resolution at least 2560x1440 (QHD).
   - Laptops: at least 16 GB RAM and at least 512 GB SSD storage.
   - Office chairs: adjustable lumbar support and a warranty of at least 3 years.
   - Laser printers: automatic duplex printing and at least 30 pages per minute.
   - UPS units: capacity of at least 1500 VA.
   - Headsets: active noise cancelling and a warranty of at least 1 year.
   - Projectors: brightness of at least 3500 lumens.
   - Desks: width of at least 140 cm.
   Refurbished or used goods are never acceptable.

3. Eligibility. An offer may be selected only if, on the order date:
   3.1 the quotation is still valid (not past its expiry date);
   3.2 the supplier can supply the full quantity requested (no split orders);
   3.3 delivery is within the number of days the requester needs;
   3.4 the unit price before GST does not exceed the maximum unit price in the request;
   3.5 no more than 30% of the order value is payable before delivery.

4. Prices. Unless a quotation states that its prices include GST, add GST at 18% to the unit
price. Landed cost = unit price including GST x quantity + the supplier's shipping charge.

5. Choosing. Among eligible offers, select the lowest landed cost. If two are equal, select the
faster delivery.

6. Missing information. An offer that does not state its price, quantity available, delivery time,
validity, payment terms or a required specification cannot be selected. If no complete offer is
eligible but some offer is incomplete, ask the suppliers for clarification instead of choosing.
If no offer is eligible at all, report that the request needs manual sourcing.

7. Approval. Purchase requests above INR 5,00,000 landed cost also need the finance director's
approval. Every purchase request is approved by a person before it is sent.

8. Travel and expenses are covered by the T&E policy and do not affect purchasing.
"""

# category: (item names, base unit price INR, the specs (key, wording, passes))
CATEGORIES = {
    "monitor": (["Dell P2723D 27-inch QHD monitor", "LG 27QN600 27-inch IPS monitor", "Samsung S27R650 27-inch monitor",
                 "BenQ GW2790Q 27-inch monitor", "Acer KA272 27-inch monitor"], 11000),
    "laptop": (["Lenovo ThinkPad E14 Gen 5", "HP ProBook 440 G10", "Dell Latitude 3440", "ASUS ExpertBook B1"], 64000),
    "chair": (["Featherlite Optima ergonomic chair", "Green Soul Seoul office chair", "Godrej Interio Motion chair"], 9000),
    "printer": (["HP LaserJet Pro 4003dn", "Brother HL-L5210DN", "Canon imageCLASS LBP246dw"], 21000),
    "ups": (["APC Back-UPS BX1600C", "Luminous LD1600 UPS", "Microtek UPS EB1600"], 7800),
    "headset": (["Jabra Evolve2 40", "Poly Voyager 4320", "Logitech Zone Vibe 100"], 3400),
    "projector": (["Epson EB-E01", "BenQ MW560", "ViewSonic PA503X"], 42000),
    "desk": (["Featherlite Edge desk", "Godrej Interio Slimline desk", "Nilkamal Freedom desk"], 13500),
}

# the spec each category is checked on: (field, required minimum, unit, a failing value)
SPECS = {
    "monitor": [("screen_size_in", 27, "inches", 24), ("resolution", "2560x1440", "", "1920x1080")],
    "laptop": [("ram_gb", 16, "GB RAM", 8), ("storage_gb", 512, "GB SSD", 256)],
    "chair": [("lumbar_support", "adjustable", "", "fixed"), ("warranty_years", 3, "years warranty", 1)],
    "printer": [("duplex", "automatic", "", "manual"), ("pages_per_minute", 30, "ppm", 22)],
    "ups": [("capacity_va", 1500, "VA", 1000)],
    "headset": [("noise_cancelling", "active", "", "none"), ("warranty_years", 1, "years warranty", 0)],
    "projector": [("brightness_lumens", 3500, "lumens", 3000)],
    "desk": [("width_cm", 140, "cm wide", 120)],
}

SUPPLIERS = ["Shree Balaji Traders", "Vardhman Office Solutions", "TechNova Distributors", "Prakash IT World",
             "Om Sai Enterprises", "Metro Business Supplies", "Kaveri Computers", "Sunrise Infotech",
             "Galaxy Office Mart", "Rathi Systems", "Pioneer Corporate Supplies", "Arihant Digital",
             "BlueLine Workspace", "Coastal IT Services"]
TERMS_OK = ["Net 30 days from delivery", "100% on delivery", "20% advance, balance on delivery", "Net 15 days",
            "30% advance with order, 70% on delivery"]
TERMS_BAD = ["100% advance with purchase order", "50% advance, 50% on delivery", "Full payment before dispatch"]
FAULTS = ["none", "expired", "quantity", "spec", "delivery", "price", "advance", "refurbished", "missing"]


@dataclass
class Quote:
    supplier: str
    quote_no: str
    item: str
    specs: dict
    refurbished: bool
    quantity_available: int | None
    unit_price: float | None
    gst_included: bool
    shipping: float
    delivery_days: int | None
    valid_until: str | None
    payment_terms: str | None
    fault: str
    fmt: str  # pdf | xlsx | email
    file: str = ""

    def landed(self, qty: int) -> float | None:
        if self.unit_price is None:
            return None
        unit = self.unit_price if self.gst_included else self.unit_price * (1 + GST)
        return round(unit * qty + self.shipping, 2)

    def ex_gst(self) -> float | None:
        if self.unit_price is None:
            return None
        return self.unit_price / (1 + GST) if self.gst_included else self.unit_price


@dataclass
class Scenario:
    id: str
    category: str
    request: str
    quantity: int
    max_unit_price: float
    needed_within_days: int
    quotes: list[Quote] = field(default_factory=list)


def advance_share(terms: str) -> float:
    t = terms.lower()
    if "100% advance" in t or "full payment before" in t:
        return 1.0
    for pct in (50, 30, 20):
        if f"{pct}% advance" in t:
            return pct / 100
    return 0.0


def _meets(category: str, specs: dict) -> bool:
    for key, need, _, _ in SPECS[category]:
        have = specs.get(key)
        if have is None:
            return False
        if isinstance(need, int) and have < need:
            return False
        if isinstance(need, str) and have != need:
            return False
    return True


def status_of(q: Quote, s: Scenario) -> str:
    """eligible | incomplete | ineligible, by the policy."""
    if None in (q.unit_price, q.quantity_available, q.delivery_days, q.valid_until, q.payment_terms) or \
            any(q.specs.get(k) is None for k, *_ in SPECS[s.category]):
        return "incomplete"
    ok = (date.fromisoformat(q.valid_until) >= ORDER_DATE and q.quantity_available >= s.quantity
          and q.delivery_days <= s.needed_within_days and q.ex_gst() <= s.max_unit_price + 1e-6
          and advance_share(q.payment_terms) <= MAX_ADVANCE and not q.refurbished and _meets(s.category, q.specs))
    return "eligible" if ok else "ineligible"


def right_answer(s: Scenario) -> dict:
    eligible = [q for q in s.quotes if status_of(q, s) == "eligible"]
    if eligible:
        best = min(eligible, key=lambda q: (q.landed(s.quantity), q.delivery_days))
        return {"status": "selected", "supplier": best.supplier, "landed_total": best.landed(s.quantity)}
    if any(status_of(q, s) == "incomplete" for q in s.quotes):
        return {"status": "needs_clarification", "supplier": None, "landed_total": None}
    return {"status": "no_eligible_offer", "supplier": None, "landed_total": None}


def _scenario(i: int) -> Scenario:
    rng = random.Random(SEED * 1000 + i)
    category = list(CATEGORIES)[i % len(CATEGORIES)]
    names, base = CATEGORIES[category]
    qty = rng.choice([4, 6, 8, 10, 12, 15, 20, 25]) if base < 30000 else rng.choice([3, 5, 6, 8, 10])
    max_price = round(base * rng.uniform(1.05, 1.2) / 100) * 100
    days = rng.choice([5, 7, 10, 14, 21])
    who = rng.choice(["Priya (Office Manager, Pune)", "Arjun (Facilities, Bengaluru)", "Meera (Admin, Mumbai)",
                      "Rahul (IT Lead, Gurugram)"])
    noun = {"monitor": "monitors", "laptop": "laptops", "chair": "office chairs", "printer": "laser printers",
            "ups": "UPS units", "headset": "headsets", "projector": "projectors", "desk": "desks"}[category]
    request = rng.choice([
        f"Please buy {qty} {noun}, maximum Rs {max_price:,} each before GST, delivered within {days} days. Compare the attached quotations and prepare a purchase request. - {who}",
        f"We need {qty} {noun} for the new floor within {days} days. Budget is up to INR {max_price:,} per unit excluding GST. Quotes attached; please recommend one. - {who}",
        f"Hi team, requesting {qty} new {noun}. Must arrive within {days} days; do not go above {max_price:,} rupees per unit (pre-GST). Three suppliers replied, files attached. - {who}",
    ])
    s = Scenario(f"PR-{i:03d}", category, request, qty, max_price, days)
    n_quotes = rng.choice([3, 3, 4])
    suppliers = rng.sample(SUPPLIERS, n_quotes)
    roll = rng.random()
    for j, supplier in enumerate(suppliers):
        if roll < 0.12:  # no complete offer qualifies
            fault = rng.choice(["expired", "quantity", "spec", "delivery", "price", "advance"])
        elif roll < 0.2 and j == 0:
            fault = "missing"
        else:
            fault = rng.choices(FAULTS, [0.36, 0.09, 0.08, 0.1, 0.08, 0.08, 0.08, 0.05, 0.08])[0]
        if roll < 0.2 and fault == "none":
            fault = rng.choice(["expired", "quantity", "delivery", "advance"])
        specs = {k: need if isinstance(need, str) else need + rng.choice([0, 0, 4, 8]) if isinstance(need, int) and need > 100
                 else need + rng.choice([0, 0, 1]) for k, need, _, _ in SPECS[category]}
        price = round(base * rng.uniform(0.85, 1.12) / 10) * 10
        price = min(price, max_price - rng.choice([0, 100, 300, 700]))
        gst_included = rng.random() < 0.3
        if gst_included:
            price = round(price * (1 + GST) / 10) * 10
            if price / (1 + GST) > max_price:
                price = int(max_price * (1 + GST) // 10 * 10)
        q = Quote(
            supplier=supplier, quote_no=f"Q/{rng.randint(1000, 9999)}/26-27", item=rng.choice(names), specs=specs,
            refurbished=False, quantity_available=qty + rng.choice([0, 2, 5, 10, 30]), unit_price=float(price),
            gst_included=gst_included, shipping=float(rng.choice([0, 0, 500, 1200, 2500, 4000])),
            delivery_days=rng.randint(max(1, days - 6), days), valid_until=str(ORDER_DATE + timedelta(days=rng.randint(3, 40))),
            payment_terms=rng.choice(TERMS_OK), fault=fault, fmt=["pdf", "xlsx", "email"][(i + j) % 3],
        )
        if fault == "expired":
            q.valid_until = str(ORDER_DATE - timedelta(days=rng.randint(1, 20)))
        elif fault == "quantity":
            q.quantity_available = max(qty - rng.randint(1, max(2, qty // 2)), 1)
        elif fault == "spec":
            key, _, _, bad = rng.choice(SPECS[category])
            q.specs[key] = bad
        elif fault == "delivery":
            q.delivery_days = days + rng.choice([2, 5, 9])
        elif fault == "price":
            over = max_price * rng.uniform(1.03, 1.12)
            q.unit_price = float(round((over * (1 + GST) if gst_included else over) / 10) * 10)
        elif fault == "advance":
            q.payment_terms = rng.choice(TERMS_BAD)
        elif fault == "refurbished":
            q.refurbished = True
        elif fault == "missing":
            gone = rng.choice(["delivery_days", "payment_terms", "valid_until", "spec"])
            if gone == "spec":
                q.specs[rng.choice(SPECS[category])[0]] = None
            else:
                setattr(q, gone, None)
        s.quotes.append(q)
    return s


def demo_scenario() -> Scenario:
    """The one for the live demo: 15 monitors, at most INR 12,000 each, within seven days."""
    s = Scenario("PR-DEMO", "monitor", "Buy 15 monitors, maximum Rs 12,000 each before GST, delivered within seven days. "
                 "Compare the uploaded supplier quotations and prepare a purchase request. - Priya (Office Manager, Pune)",
                 15, 12000, 7)
    s.quotes = [
        Quote("Shree Balaji Traders", "Q/4471/26-27", "Dell P2723D 27-inch QHD monitor",
              {"screen_size_in": 27, "resolution": "2560x1440"}, False, 40, 11400.0, False, 1500.0, 5,
              str(ORDER_DATE + timedelta(days=14)), "Net 30 days from delivery", "none", "pdf"),
        Quote("TechNova Distributors", "Q/2208/26-27", "LG 27QN600 27-inch IPS monitor",
              {"screen_size_in": 27, "resolution": "2560x1440"}, False, 15, 13040.0, True, 0.0, 4,
              str(ORDER_DATE + timedelta(days=10)), "20% advance, balance on delivery", "none", "xlsx"),
        Quote("Metro Business Supplies", "Q/9310/26-27", "Samsung S24C450 24-inch monitor",
              {"screen_size_in": 24, "resolution": "1920x1080"}, False, 50, 8900.0, False, 0.0, 3,
              str(ORDER_DATE + timedelta(days=20)), "100% on delivery", "spec", "email"),
    ]
    return s


# ---- rendering the documents -------------------------------------------------------------
def _spec_text(category: str, specs: dict) -> list[str]:
    out = []
    for key, _, unit, _ in SPECS[category]:
        v = specs.get(key)
        if v is None:
            continue
        label = key.replace("_", " ").replace(" in", "").replace("gb", "GB").replace("va", "VA")
        out.append(f"{label}: {v} {unit}".strip())
    return out


def _price_line(q: Quote) -> str:
    if q.unit_price is None:
        return "Unit price: on request"
    return f"Unit price: Rs {q.unit_price:,.0f} {'(inclusive of GST)' if q.gst_included else '+ GST 18%'}"


def _lines(q: Quote, s: Scenario) -> list[tuple[str, str]]:
    rows = [("Supplier", q.supplier), ("Quotation no.", q.quote_no), ("Date", str(ORDER_DATE - timedelta(days=2))),
            ("Item", q.item + (" (refurbished)" if q.refurbished else ""))]
    rows += [("Specification", t) for t in _spec_text(s.category, q.specs)]
    rows += [("Quantity available", "" if q.quantity_available is None else f"{q.quantity_available} units"),
             ("Unit price", _price_line(q).split(": ", 1)[1]),
             ("Shipping", "Free delivery" if q.shipping == 0 else f"Rs {q.shipping:,.0f} for the consignment"),
             ("Delivery", "" if q.delivery_days is None else f"{q.delivery_days} working days from PO"),
             ("Payment terms", q.payment_terms or ""),
             ("Valid until", "" if q.valid_until is None else date.fromisoformat(q.valid_until).strftime("%d %B %Y"))]
    return [(k, v) for k, v in rows if v]


def _pdf(path: Path, q: Quote, s: Scenario) -> None:
    from fpdf import FPDF

    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 15)
    pdf.cell(0, 9, q.supplier.encode("latin-1", "replace").decode("latin-1"), new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 9)
    pdf.cell(0, 5, "GSTIN 27AAACX1234X1Z5 | sales@" + q.supplier.split()[0].lower() + ".in", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(4)
    pdf.set_font("Helvetica", "B", 12)
    pdf.cell(0, 8, "QUOTATION", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 10)
    for k, v in _lines(q, s):
        pdf.cell(48, 7, k)
        pdf.multi_cell(0, 7, v.replace("Rs", "Rs."), new_x="LMARGIN", new_y="NEXT")
    pdf.ln(4)
    pdf.multi_cell(0, 6, "Thank you for the opportunity. Prices are subject to the terms above.")
    pdf.output(str(path))


def _xlsx(path: Path, q: Quote, s: Scenario) -> None:
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "Quotation"
    ws.append([q.supplier, "", "Quotation"])
    ws.append([])
    for k, v in _lines(q, s):
        ws.append([k, v])
    wb.save(path)


def _email(path: Path, q: Quote, s: Scenario) -> None:
    specs = ", ".join(_spec_text(s.category, q.specs))
    parts = [f"From: sales@{q.supplier.split()[0].lower()}.in", f"Subject: Quotation {q.quote_no}", "",
             "Dear Sir/Madam,", "",
             f"Thank you for your enquiry. We are pleased to offer the {q.item}"
             + (" (refurbished)" if q.refurbished else "") + (f" ({specs})." if specs else ".")]
    if q.quantity_available is not None:
        parts.append(f"We have {q.quantity_available} units ready in stock.")
    if q.unit_price is not None:
        parts.append(f"Our best price is Rs {q.unit_price:,.0f} per unit, "
                     + ("all taxes included." if q.gst_included else "plus GST at 18%."))
    parts.append("Delivery is free of charge." if q.shipping == 0 else f"Shipping for the full lot is Rs {q.shipping:,.0f}.")
    if q.delivery_days is not None:
        parts.append(f"We can deliver within {q.delivery_days} working days of receiving your PO.")
    if q.payment_terms:
        parts.append(f"Payment terms: {q.payment_terms}.")
    if q.valid_until:
        parts.append(f"This offer is valid till {date.fromisoformat(q.valid_until).strftime('%d/%m/%Y')}.")
    parts += ["", "Regards,", f"Sales Team, {q.supplier}"]
    path.write_text("\n".join(parts), encoding="utf-8")


def scenarios() -> list[Scenario]:
    return [_scenario(i) for i in range(N_SCENARIOS)]


def write_all(root: Path = DATA) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "purchasing_policy.txt").write_text(POLICY, encoding="utf-8")
    index = []
    for s in [*scenarios(), demo_scenario()]:
        folder = root / s.id
        folder.mkdir(exist_ok=True)
        (folder / "request.txt").write_text(s.request, encoding="utf-8")
        for j, q in enumerate(s.quotes):
            ext = {"pdf": "pdf", "xlsx": "xlsx", "email": "txt"}[q.fmt]
            q.file = f"quotation_{j + 1}_{q.supplier.split()[0].lower()}.{ext}"
            {"pdf": _pdf, "xlsx": _xlsx, "email": _email}[q.fmt](folder / q.file, q, s)
        index.append({"id": s.id, "category": s.category, "quantity": s.quantity, "max_unit_price": s.max_unit_price,
                      "needed_within_days": s.needed_within_days, "files": [q.file for q in s.quotes],
                      "answer": right_answer(s), "facts": [asdict(q) for q in s.quotes]})
    (root / "index.json").write_text(json.dumps(index, indent=1, default=str), encoding="utf-8")


def index() -> list[dict]:
    return json.loads((DATA / "index.json").read_text(encoding="utf-8"))


if __name__ == "__main__":
    write_all()
    from collections import Counter

    rows = index()
    print(len(rows), "scenarios", Counter(r["answer"]["status"] for r in rows))
