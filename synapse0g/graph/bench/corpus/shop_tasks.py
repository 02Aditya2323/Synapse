"""Shop tasks, generated: a customer request, a starting database, and the database a
correct agent must leave behind.

The expected database is not written by hand: it is what the shop's own tools produce
from the correct sequence of actions, so the checker and the tools can never disagree
about what "right" means.

    load(200)  -> shop_0000 .. shop_0199, the same on every machine
"""

from __future__ import annotations

import random

from synapse0g.graph.bench.tasks import Task
from synapse0g.graph.bench.tools.shop import TOOLS

N_TASKS = 200
SEED = 20261003

ITEMS = {"mug": 8, "notebook": 5, "backpack": 40, "lamp": 25, "headphones": 60, "water bottle": 12,
         "desk mat": 18, "keyboard": 75}
CUSTOMERS = ["Asha", "Bilal", "Chen", "Divya", "Emeka", "Farah", "Goran", "Hana", "Ivan", "Jaya"]
KINDS = ("cancel", "cancel_refund", "refund_full", "refund_units", "change_quantity", "ship", "cancel_and_ship",
         "place")


def _database(rng: random.Random) -> dict:
    items = rng.sample(sorted(ITEMS), 3)
    db = {"orders": {}, "stock": {i: rng.randint(3, 20) for i in items},
          "prices": {i: ITEMS[i] for i in items}, "refunds": []}
    number = rng.randint(100, 880)
    statuses = ["placed", "placed", "delivered", "delivered", rng.choice(["shipped", "placed", "delivered"])]
    rng.shuffle(statuses)
    for status in statuses:
        item = rng.choice(items)
        qty = rng.randint(2, 5)
        number += rng.randint(1, 7)
        db["orders"][f"O{number}"] = {
            "customer": rng.choice(CUSTOMERS), "item": item, "qty": qty, "status": status, "paid": qty * ITEMS[item],
        }
    return db


def _pick(db: dict, status: str, rng: random.Random, exclude: str | None = None) -> str:
    return rng.choice(sorted(oid for oid, o in db["orders"].items() if o["status"] == status and oid != exclude))


def _scenario(kind: str, db: dict, rng: random.Random) -> tuple[str, list[tuple[str, dict]]]:
    """(the customer's request, the correct actions in order)."""
    if kind == "cancel":
        oid = _pick(db, "placed", rng)
        return (f"{db['orders'][oid]['customer']} no longer needs order {oid}. Please cancel it.",
                [("cancel_order", {"order_id": oid})])
    if kind == "cancel_refund":
        oid = _pick(db, "placed", rng)
        return (f"Cancel order {oid} for {db['orders'][oid]['customer']} and refund the full payment.",
                [("cancel_order", {"order_id": oid}), ("refund_order", {"order_id": oid,
                                                                        "amount": db["orders"][oid]["paid"]})])
    if kind == "refund_full":
        oid = _pick(db, "delivered", rng)
        return (f"Order {oid} arrived damaged. {db['orders'][oid]['customer']} wants a full refund.",
                [("refund_order", {"order_id": oid, "amount": db["orders"][oid]["paid"]})])
    if kind == "refund_units":
        oid = _pick(db, "delivered", rng)
        order = db["orders"][oid]
        units = rng.randint(1, order["qty"] - 1)
        request = (f"In order {oid}, {units} of the {order['qty']} {order['item']} units arrived broken. "
                   f"Refund {order['customer']} for the broken units only.")
        return (request,
                [("refund_order", {"order_id": oid, "amount": units * db["prices"][order["item"]]})])
    if kind == "change_quantity":
        oid = _pick(db, "placed", rng)
        order = db["orders"][oid]
        options = [q for q in range(1, order["qty"] + db["stock"][order["item"]] + 1) if q != order["qty"]]
        qty = rng.choice(options[:8])
        return (f"{order['customer']} wants order {oid} changed to {qty} {order['item']} units in total.",
                [("update_quantity", {"order_id": oid, "quantity": qty})])
    if kind == "ship":
        oid = _pick(db, "placed", rng)
        return (f"Order {oid} is packed and ready. Please ship it.", [("ship_order", {"order_id": oid})])
    if kind == "cancel_and_ship":
        a = _pick(db, "placed", rng)
        b = _pick(db, "placed", rng, exclude=a)
        return (f"Two things: cancel order {a}, and ship order {b}.",
                [("cancel_order", {"order_id": a}), ("ship_order", {"order_id": b})])
    if kind == "place":
        item = rng.choice(sorted(db["stock"]))
        qty = rng.randint(1, db["stock"][item])
        customer = rng.choice(CUSTOMERS)
        return (f"{customer} wants to buy {qty} {item} units. Please place the order.",
                [("place_order", {"customer": customer, "item": item, "quantity": qty})])
    raise KeyError(kind)


def apply_actions(db: dict, actions: list[tuple[str, dict]]) -> dict:
    """The database after the correct actions, computed by the shop's own tools."""
    for name, args in actions:
        result = TOOLS[name](db, **args)
        if not result.get("ok") or "db" not in result:
            raise ValueError(f"generated task is not solvable: {name}({args}) -> {result.get('message')}")
        db = result["db"]
    return db


def expected_state(db: dict) -> dict:
    """What the checker compares: orders, stock and refunds (prices never change)."""
    return {"orders": db["orders"], "stock": db["stock"], "refunds": db["refunds"]}


def make_task(index: int) -> Task:
    rng = random.Random(SEED + index)
    kind = KINDS[index % len(KINDS)]
    db = _database(rng)
    request, actions = _scenario(kind, db, rng)
    return Task(
        task_id=f"shop_{index:04d}",
        workflow="shop_ops",
        checker="ops_state",
        input={"request": request, "db": db},
        expected=expected_state(apply_actions(db, actions)),
        meta={"kind": kind, "actions": [[name, args] for name, args in actions]},
    )


def load(n: int | None = None, offset: int = 0) -> list[Task]:
    stop = N_TASKS if n is None else min(N_TASKS, offset + n)
    return [make_task(i) for i in range(offset, stop)]
