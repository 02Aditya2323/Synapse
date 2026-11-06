"""Plans a trip. Three model-and-tool steps run one after another, then it emails the plan."""
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
    if name == "get_weather":
        place = _get_json("https://geocoding-api.open-meteo.com/v1/search?count=1&name=" + urllib.parse.quote(args["city"]))["results"][0]
        daily = _get_json(f"https://api.open-meteo.com/v1/forecast?latitude={place['latitude']}&longitude={place['longitude']}"
                          "&daily=temperature_2m_max&forecast_days=3&timezone=auto")["daily"]
        return json.dumps({"country": place["country"], "max_c": daily["temperature_2m_max"]})
    if name == "get_rate":
        return json.dumps({"inr_per_usd": _get_json("https://open.er-api.com/v6/latest/USD")["rates"]["INR"]})
    if name == "read_wiki":
        return _get_json("https://en.wikipedia.org/api/rest_v1/page/summary/" + urllib.parse.quote(args["topic"]))["extract"]
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


def weather_agent(city):
    return _agent_turn(f"Use get_weather, then say in one sentence what to pack for {city}.",
                       [_tool("get_weather", "The three-day forecast for a city", city="The city")])


def budget_agent(city):
    return _agent_turn(f"Use get_rate, then give a one-line daily budget in INR for {city}, at 150 USD a day.",
                       [_tool("get_rate", "How many INR one USD buys", currency="Always USD")])


def culture_agent(city):
    return _agent_turn(f"Use read_wiki on {city}, then name one thing a first-time visitor should see.",
                       [_tool("read_wiki", "A Wikipedia summary", topic="The page title")])


def compose_plan(weather, budget, culture):
    return _ask("Write a short three-line travel plan.", f"{weather}\n{budget}\n{culture}")


def send_itinerary(plan):
    raise RuntimeError("the email really went out")


def plan_trip(city):
    weather = weather_agent(city)
    budget = budget_agent(city)
    culture = culture_agent(city)
    plan = compose_plan(weather, budget, culture)
    send_itinerary(plan)
    return plan
