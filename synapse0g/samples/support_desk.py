"""Handles a support ticket. Its last step issues a refund, which is never run in a test."""
import json
import os
import urllib.parse
import urllib.request

from openai import OpenAI

client = OpenAI(base_url=os.environ.get("OPENAI_BASE_URL", "https://router-api.0g.ai/v1"),
                api_key=os.environ.get("OPENAI_API_KEY") or os.environ.get("0G_API_KEY"))
MODEL = os.environ.get("MODEL", "qwen3.8-flash")


def _ask(system, user):
    reply = client.chat.completions.create(model=MODEL, temperature=0, seed=7, max_tokens=220, messages=[
        {"role": "system", "content": system}, {"role": "user", "content": user}])
    return reply.choices[0].message.content or ""


def _get_json(url):
    request = urllib.request.Request(url, headers={"User-Agent": "agent-sample/1.0"})
    with urllib.request.urlopen(request, timeout=20) as answer:
        return json.loads(answer.read().decode())


def _tool(name, description, **params):
    return {"type": "function", "function": {"name": name, "description": description, "parameters": {
        "type": "object", "required": list(params),
        "properties": {key: {"type": "string", "description": text} for key, text in params.items()}}}}


POLICY = "Refunds within 30 days with a receipt. Damaged items are replaced free. Shipping takes 3 to 5 days."


def _dispatch(name, args):
    if name == "read_policy":
        return POLICY
    if name == "get_rate":
        return json.dumps({"inr_per_usd": _get_json("https://open.er-api.com/v6/latest/USD")["rates"]["INR"]})
    return "no such tool"


def _agent_turn(prompt, tools):
    """The model picks a tool, the tool really runs, and the model reads the result."""
    messages = [{"role": "user", "content": prompt}]
    for _ in range(3):
        reply = client.chat.completions.create(model=MODEL, temperature=0, seed=7, max_tokens=220, messages=messages,
                                               tools=tools)
        message = reply.choices[0].message
        if not message.tool_calls:
            return message.content or ""
        messages.append({"role": "assistant", "content": message.content or "", "tool_calls": [
            {"id": c.id, "type": "function", "function": {"name": c.function.name, "arguments": c.function.arguments}}
            for c in message.tool_calls]})
        for call in message.tool_calls:
            args = json.loads(call.function.arguments or "{}")
            messages.append({"role": "tool", "tool_call_id": call.id, "content": _dispatch(call.function.name, args)})
    return "ran out of turns"


def classify_ticket(ticket):
    return _ask("Classify this ticket as refund, damage or shipping. One word.", ticket)


def read_mood(ticket):
    return _ask("How upset is this customer, from 1 to 5? Answer with the number and one word.", ticket)


def policy_agent(ticket):
    return _agent_turn(f"Use read_policy, then say what applies to: {ticket}",
                       [_tool("read_policy", "The returns policy", section="Any section")])


def price_agent(ticket):
    return _agent_turn(f"Use get_rate, then convert a 40 USD blender to INR for this customer: {ticket}",
                       [_tool("get_rate", "How many INR one USD buys", currency="Always USD")])


def draft_reply(kind, mood, policy, price):
    return _ask("Write a kind two-sentence reply to the customer.", f"{kind}, {mood}\n{policy}\n{price}")


def post_reply(reply):
    raise RuntimeError("the reply really went out")


def issue_refund(reply):
    raise RuntimeError("the money really moved")


def handle_ticket(ticket):
    kind = classify_ticket(ticket)
    mood = read_mood(ticket)
    policy = policy_agent(ticket)
    price = price_agent(ticket)
    reply = draft_reply(kind, mood, policy, price)
    post_reply(reply)
    issue_refund(reply)
    return reply
