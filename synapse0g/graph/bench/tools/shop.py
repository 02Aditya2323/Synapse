"""A mock shop API: orders, stock and refunds.

The shop's database is plain data. Every tool takes it as `db` and, when it changes
something, returns the new one:

    {"ok": True, "message": "...", "db": <new database>}     a change
    {"ok": True, "order": {...}}                               a lookup
    {"ok": False, "message": "why not"}                        a refusal, nothing changed

Tools never mutate their input and keep no state of their own.

    db = {"orders":  {"O101": {"customer", "item", "qty", "status", "paid"}},
          "stock":   {"mug": 12},
          "prices":  {"mug": 8},
          "refunds": [{"order_id": "O101", "amount": 16}]}

Order status: placed -> shipped -> delivered, or placed -> cancelled;
a refund makes it partially_refunded or refunded.
"""

from __future__ import annotations

import copy
from collections.abc import Callable
from typing import Any


def _fail(message: str) -> dict:
    return {"ok": False, "message": message}


def _order(db: dict, order_id: Any) -> dict | None:
    orders = db.get("orders") if isinstance(db, dict) else None
    return orders.get(str(order_id)) if isinstance(orders, dict) else None


def _whole_number(value: Any) -> int | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return int(number) if number.is_integer() else None


def _amount(value: Any) -> int | float | None:
    try:
        number = round(float(value), 2)
    except (TypeError, ValueError):
        return None
    return int(number) if number.is_integer() else number


def refunded_so_far(db: dict, order_id: str) -> int | float:
    return sum(r["amount"] for r in db.get("refunds", []) if r["order_id"] == order_id)


def get_order(db: dict, order_id: str) -> dict:
    """Look up one order: customer, item, quantity, status, amount paid, unit price."""
    order = _order(db, order_id)
    if order is None:
        return _fail(f"no order {order_id}")
    return {"ok": True, "order": {"order_id": str(order_id), **order},
            "unit_price": db["prices"][order["item"]], "refunded": refunded_so_far(db, str(order_id))}


def check_stock(db: dict, item: str) -> dict:
    """How many units of an item are in stock."""
    if item not in db.get("stock", {}):
        return _fail(f"no item {item}")
    return {"ok": True, "item": item, "in_stock": db["stock"][item], "unit_price": db["prices"][item]}


def cancel_order(db: dict, order_id: str) -> dict:
    """Cancel an order that has not shipped yet; its units go back into stock. Does not refund."""
    order = _order(db, order_id)
    if order is None:
        return _fail(f"no order {order_id}")
    if order["status"] != "placed":
        return _fail(f"order {order_id} is {order['status']}; only a placed order can be cancelled")
    new = copy.deepcopy(db)
    new["orders"][str(order_id)]["status"] = "cancelled"
    new["stock"][order["item"]] += order["qty"]
    return {"ok": True, "message": f"order {order_id} cancelled, {order['qty']} {order['item']} back in stock",
            "db": new}


def ship_order(db: dict, order_id: str) -> dict:
    """Ship a placed order."""
    order = _order(db, order_id)
    if order is None:
        return _fail(f"no order {order_id}")
    if order["status"] != "placed":
        return _fail(f"order {order_id} is {order['status']}; only a placed order can be shipped")
    new = copy.deepcopy(db)
    new["orders"][str(order_id)]["status"] = "shipped"
    return {"ok": True, "message": f"order {order_id} shipped", "db": new}


def update_quantity(db: dict, order_id: str, quantity: int) -> dict:
    """Change the quantity (the new total, not the difference) of an order that has not shipped."""
    order = _order(db, order_id)
    if order is None:
        return _fail(f"no order {order_id}")
    if order["status"] != "placed":
        return _fail(f"order {order_id} is {order['status']}; only a placed order can be changed")
    qty = _whole_number(quantity)
    if qty is None or qty < 1:
        return _fail(f"quantity must be a whole number of at least 1, got {quantity!r}")
    extra = qty - order["qty"]
    if extra > db["stock"][order["item"]]:
        return _fail(f"only {db['stock'][order['item']]} more {order['item']} in stock, {extra} needed")
    new = copy.deepcopy(db)
    new["stock"][order["item"]] -= extra
    new["orders"][str(order_id)]["qty"] = qty
    new["orders"][str(order_id)]["paid"] = qty * db["prices"][order["item"]]
    return {"ok": True, "message": f"order {order_id} now has {qty} {order['item']}", "db": new}


def refund_order(db: dict, order_id: str, amount: float) -> dict:
    """Refund money for a delivered or cancelled order, at most what was paid and not yet refunded."""
    order = _order(db, order_id)
    if order is None:
        return _fail(f"no order {order_id}")
    if order["status"] not in ("delivered", "cancelled", "partially_refunded"):
        return _fail(f"order {order_id} is {order['status']}; only a delivered or cancelled order can be refunded")
    money = _amount(amount)
    if money is None or money <= 0:
        return _fail(f"amount must be a positive number, got {amount!r}")
    left = order["paid"] - refunded_so_far(db, str(order_id))
    if money > left:
        return _fail(f"only {left} can still be refunded on order {order_id}")
    new = copy.deepcopy(db)
    new["refunds"].append({"order_id": str(order_id), "amount": money})
    new["orders"][str(order_id)]["status"] = "refunded" if money == left else "partially_refunded"
    return {"ok": True, "message": f"refunded {money} on order {order_id}", "db": new}


def place_order(db: dict, customer: str, item: str, quantity: int) -> dict:
    """Place a new order and charge the customer for it. Money moves: this cannot be undone."""
    if item not in db.get("stock", {}):
        return _fail(f"no item {item}")
    qty = _whole_number(quantity)
    if qty is None or qty < 1:
        return _fail(f"quantity must be a whole number of at least 1, got {quantity!r}")
    if qty > db["stock"][item]:
        return _fail(f"only {db['stock'][item]} {item} in stock, {qty} needed")
    new = copy.deepcopy(db)
    order_id = f"O{max((int(oid[1:]) for oid in db['orders']), default=100) + 1}"
    new["orders"][order_id] = {"customer": customer, "item": item, "qty": qty, "status": "placed",
                               "paid": qty * db["prices"][item]}
    new["stock"][item] -= qty
    return {"ok": True, "message": f"order {order_id} placed: {qty} {item} for {customer}", "db": new}


TOOLS: dict[str, Callable[..., dict]] = {
    "get_order": get_order,
    "check_stock": check_stock,
    "cancel_order": cancel_order,
    "ship_order": ship_order,
    "update_quantity": update_quantity,
    "refund_order": refund_order,
    "place_order": place_order,
}

# What each tool does to the world. The shop_ops `call` node can reach any of these, so it
# takes the most restrictive effect among them (docs/IR_SPEC.md §4). Money movement is
# `irreversible`: Synapse never re-runs, reorders, caches or speculates it.
TOOL_EFFECTS: dict[str, str] = {
    "get_order": "read",
    "check_stock": "read",
    "cancel_order": "external",
    "ship_order": "external",
    "update_quantity": "external",
    "refund_order": "irreversible",
    "place_order": "irreversible",
}


def call_tool(name: str, db: dict, **args: Any) -> dict:
    """A tool name the model made up is a refusal it can read, not an exception."""
    if name not in TOOLS:
        return _fail(f"no tool {name}; tools are {', '.join(sorted(TOOLS))}")
    try:
        return TOOLS[name](db, **args)
    except TypeError as exc:  # an argument name that is not a valid keyword
        return _fail(str(exc))
