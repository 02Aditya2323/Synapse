"""shop_ops: a support agent acting on a mock shop. The safety demo, not a speedup.

    plan(model)  [act(model)  call(tool)] x 1..MAX_ACTIONS  act(model, DONE)

Every `call` is one dispatcher: the model picks the tool at run time, so at compile time
that node could be `place_order` or `refund_order`. It therefore carries the most
restrictive effect of anything it can reach, `irreversible`, and Synapse must refuse to
reorder, cache, re-run or speculate it. This workflow earns its place by producing no
optimization of the `call` node, and saying why.

The model never sees the database, only what the tools report. The final answer is the
database the agent leaves behind, which the checker compares with the correct one.
"""

from __future__ import annotations

import json
import re
from functools import reduce
from typing import Any

from synapse0g.graph.bench.tools.parsing import clip, field
from synapse0g.graph.bench.tools.shop import TOOL_EFFECTS, TOOLS, call_tool
from synapse0g.graph.bench.workflows._llm import declare, guard, last_given, prompt, transform
from synapse0g.graph.bench.workflows.research_fanout import Llm
from synapse0g.graph.core.effects import join
from synapse0g.graph.core.sir import Effect, Graph
from synapse0g.graph.frontend.dsl import workflow as trace

MAX_ACTIONS = 6
MAX_TURNS = 7  # act visits, including the one that says DONE

_KEY_VALUE = re.compile(r"""["']?(\w+)["']?\s*[:=]\s*("[^"]*"|'[^']*'|[^,}\s]+)""")

SIGNATURES = {
    "get_order": "order_id: string",
    "check_stock": "item: string",
    "cancel_order": "order_id: string",
    "ship_order": "order_id: string",
    "update_quantity": "order_id: string, quantity: integer",
    "refund_order": "order_id: string, amount: number",
    "place_order": "customer: string, item: string, quantity: integer",
}


def call_effect() -> Effect:
    """The `call` node's effect: the most restrictive among the tools it can dispatch to.
    `core/effects.py` owns the lattice, so the ordering is asked for here, not repeated."""
    return reduce(join, TOOL_EFFECTS.values())


def describe_tools() -> str:
    return "\n".join(f"- {name}({SIGNATURES[name]}): {TOOLS[name].__doc__.split(chr(10))[0]}" for name in TOOLS)


PLAN_SYSTEM = (
    "You are a shop support agent. Read the customer request and list the steps to handle it, "
    "one short line each, naming the order ids. Always look up an order before changing it. "
    "At most 4 lines.\n\nTools you will be able to use:\n" + describe_tools()
)
ACT_SYSTEM = (
    "You are a shop support agent handling a customer request with tools. "
    "Do one tool call at a time. Always look up an order with get_order before changing it. "
    "If a tool says ok=false, read its message and do not repeat the same call. "
    "As soon as the results show the request is handled, reply DONE.\n\n"
    "Tools:\n" + describe_tools() + "\n\n"
    "Reply in exactly this format:\n"
    "ACTION: <tool name>\n"
    'ARGS: <JSON object, e.g. {"order_id": "O101"}>\n'
    "or, when the request has been fully handled:\n"
    "DONE: <one sentence telling the customer what was done>"
)


def parse_args(raw: str | None) -> dict | None:
    """The ARGS line as a dict. Small models drop quotes or add text; None if hopeless."""
    if not raw:
        return {}
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    text = match.group() if match else raw
    try:
        value = json.loads(text)
        return value if isinstance(value, dict) else None
    except json.JSONDecodeError:
        pass
    out: dict[str, Any] = {}
    for key, val in _KEY_VALUE.findall(text):
        val = val.strip("\"'")
        try:
            out[key] = json.loads(val)
        except json.JSONDecodeError:
            out[key] = val
    return out or None


def format_history(history: list[dict]) -> str:
    if not history:
        return "(nothing done yet)"
    return "\n".join(
        f"{i + 1}. {h['tool']}({json.dumps(h['args'])}) -> {clip(json.dumps(h['result']), 260)}"
        for i, h in enumerate(history)
    )


def plan_messages(request: str) -> list[dict]:
    return [{"role": "system", "content": PLAN_SYSTEM}, {"role": "user", "content": f"Customer request: {request}"}]


def act_messages(request: str, plan: str, history: list[dict]) -> list[dict]:
    return [{"role": "system", "content": ACT_SYSTEM},
            {"role": "user", "content": (f"Customer request: {request}\n\nPlan:\n{plan}\n\n"
                                         f"Done so far:\n{format_history(history)}")}]


def parse_action(text: str) -> dict | None:
    """The first action, if the model wrote several; None means it is done."""
    action = field(text, "ACTION", first=True)
    if action is None:
        return None
    return {"tool": action.split("(")[0].strip(), "args": parse_args(field(text, "ARGS", first=True))}


def call(tool: str, args: dict | None, db: dict) -> dict:
    if args is None:
        return {"ok": False, "message": "ARGS was not a JSON object"}
    return call_tool(tool, db, **{k: v for k, v in args.items() if k != "db"})


def run(task_input: dict[str, Any], llm: Llm) -> dict:
    request, db = task_input["request"], task_input["db"]
    plan = clip(llm(plan_messages(request), 120), 500)
    history: list[dict] = []
    reply = ""
    for turn in range(1, MAX_TURNS + 1):
        text = llm(act_messages(request, plan, history), 96)
        proposed = parse_action(text)
        if proposed is None or len(history) >= MAX_ACTIONS or turn >= MAX_TURNS:
            reply = field(text, "DONE") or clip(text, 200)
            break
        if history and (proposed["tool"], proposed["args"]) == (history[-1]["tool"], history[-1]["args"]):
            # small models re-issue the call they just made; say so instead of paying for it again
            history.append({**proposed, "result": {
                "ok": False, "message": "this exact call was just made; do the next step or reply DONE"}})
            continue
        result = call(proposed["tool"], proposed["args"], db)
        if result.get("ok") and isinstance(result.get("db"), dict):
            db = result["db"]
        shown = {k: v for k, v in result.items() if k != "db"}  # the model sees the report, not the database
        history.append({"tool": proposed["tool"], "args": proposed["args"] or {}, "result": shown})
    return {"reply": reply, "db": db}


@prompt("shop_ops.plan_prompt")
def plan_prompt(request: str) -> dict:
    return {"messages": plan_messages(request), "max_tokens": 120}


@transform("shop_ops.first_state", outputs=["state"])
def first_state(text: str, db: dict) -> dict:
    return {"plan": clip(text, 500), "history": [], "db": db}


@prompt("shop_ops.act_prompt")
def act_prompt(request: str, state: dict) -> dict:
    return {"messages": act_messages(request, state["plan"], state["history"]), "max_tokens": 96}


@transform("shop_ops.parse_act", outputs=["action", "go"])
def parse_act(text: str) -> dict:
    proposed = parse_action(text)
    return {"action": proposed, "go": proposed is not None}


@declare("shop_ops.call", kind="tool", effect=call_effect())
def dispatch(action: dict, state: dict) -> dict:
    """The one node that touches the shop. The model picks the tool at run time, so this
    carries the most restrictive effect of any tool it can reach."""
    return call(action["tool"], action["args"], state["db"])


@transform("shop_ops.record", outputs=["state"])
def record(state: dict, action: dict, result: dict) -> dict:
    db = result["db"] if result.get("ok") and isinstance(result.get("db"), dict) else state["db"]
    shown = {k: v for k, v in result.items() if k != "db"}  # the model sees the report, not the database
    entry = {"tool": action["tool"], "args": action["args"] or {}, "result": shown}
    return {**state, "db": db, "history": state["history"] + [entry]}


@transform("shop_ops.finish")
def finish(s0: dict, s1: dict | None, s2: dict | None, s3: dict | None, s4: dict | None, s5: dict | None,
           s6: dict | None) -> dict:
    return {"reply": "", "db": last_given(s0, s1, s2, s3, s4, s5, s6)["db"]}


def graph() -> Graph:
    turns: list = []

    @trace(name="shop_ops")
    def shop(request: str, db: dict) -> dict:
        state = first_state(plan_prompt(request), db)
        states = [state]
        go = None
        for _ in range(MAX_ACTIONS):
            text = act_prompt(request, state)
            action, next_go = parse_act(text)
            if go is not None:
                turns.append(([text, action], go.ref))
            result = dispatch(action, state)
            state = record(state, action, result)
            turns.append(([result, state], next_go.ref))
            states.append(state)
            go = next_go
        return finish(*states)

    return guard(shop.compile(), turns)


workflow = graph  # `synapse compile bench.workflows.shop_ops:workflow`
