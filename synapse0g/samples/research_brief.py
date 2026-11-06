"""Researches a topic from two live sources, has a skeptic read them, and publishes the brief."""
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
    if name == "read_wiki":
        return _get_json("https://en.wikipedia.org/api/rest_v1/page/summary/" + urllib.parse.quote(args["topic"]))["extract"]
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


def encyclopedia_agent(topic):
    return _agent_turn(f"Use read_wiki on {topic}, then state the key fact in one sentence.",
                       [_tool("read_wiki", "A Wikipedia summary", topic="The page title")])


def news_agent(topic):
    return _agent_turn(f"Use search_news on {topic}, then say in one sentence what people are discussing.",
                       [_tool("search_news", "Recent Hacker News titles", query="What to search for")])


def skeptic_agent(topic):
    return _ask("You are a skeptic. In one sentence, name the most common misconception.", topic)


def compose_brief(facts, news, doubts):
    return _ask("Write a three-sentence brief.", f"Facts: {facts}\nNews: {news}\nDoubts: {doubts}")


def publish_brief(brief):
    raise RuntimeError("the brief really went out")


def research(topic):
    facts = encyclopedia_agent(topic)
    news = news_agent(topic)
    doubts = skeptic_agent(topic)
    brief = compose_brief(facts, news, doubts)
    publish_brief(brief)
    return brief
