"""Four reviewers read one snippet, one of them with a real tool. Then it posts the review."""
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
    if name == "count_lines":
        return json.dumps({"lines": len(args["code"].split("\n")), "characters": len(args["code"])})
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


def bug_reviewer(code):
    return _ask("Find the most likely bug in this code. One sentence.", code)


def security_reviewer(code):
    return _ask("Find the most likely security problem in this code. One sentence.", code)


def style_reviewer(code):
    return _ask("Suggest one readability change to this code. One sentence.", code)


def size_reviewer(code):
    return _agent_turn(f"Use count_lines on this code, then say in one sentence if it is too long: {code}",
                       [_tool("count_lines", "Counts lines and characters", code="The code")])


def decide(bugs, security, style, size):
    return _ask("Answer SHIP or FIX, then one sentence why.", f"{bugs}\n{security}\n{style}\n{size}")


def post_review(decision):
    raise RuntimeError("the review really went out")


def review(code):
    bugs = bug_reviewer(code)
    security = security_reviewer(code)
    style = style_reviewer(code)
    size = size_reviewer(code)
    decision = decide(bugs, security, style, size)
    post_review(decision)
    return decision
