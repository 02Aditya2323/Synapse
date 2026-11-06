"""Checks live prices and asks three analysts. Its last step places an order, which is never run in a test."""
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


def _dispatch(name, args):
    if name == "get_price":
        return json.dumps(_get_json(f"https://api.coinbase.com/v2/prices/{args['coin']}-USD/spot")["data"])
    if name == "get_rate":
        return json.dumps({"inr_per_usd": _get_json("https://open.er-api.com/v6/latest/USD")["rates"]["INR"]})
    if name == "search_news":
        hits = _get_json("https://hn.algolia.com/api/v1/search?hitsPerPage=5&query=" + urllib.parse.quote(args["query"]))["hits"]
        return json.dumps([h.get("title") for h in hits])
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


def price_analyst(coin):
    return _agent_turn(f"Use get_price for {coin}, then state its price in one sentence.",
                       [_tool("get_price", "The live spot price in USD", coin="The ticker, like BTC")])


def currency_analyst(coin):
    return _agent_turn(f"Use get_rate, then say in one sentence how the rupee affects an Indian buyer of {coin}.",
                       [_tool("get_rate", "How many INR one USD buys", currency="Always USD")])


def news_analyst(coin):
    return _agent_turn(f"Use search_news on {coin}, then give the mood of the headlines in one sentence.",
                       [_tool("search_news", "Recent Hacker News titles", query="What to search for")])


def decide_trade(price, currency, news):
    return _ask("You advise a cautious investor. Answer BUY or HOLD, then one sentence why.", f"{price}\n{currency}\n{news}")


def place_order(decision):
    raise RuntimeError("the order was really placed")


def trade_desk(coin):
    price = price_analyst(coin)
    currency = currency_analyst(coin)
    news = news_analyst(coin)
    decision = decide_trade(price, currency, news)
    place_order(decision)
    return decision
