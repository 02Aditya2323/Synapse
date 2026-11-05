"""Real agents to optimize: LangGraph's prebuilt ReAct agent, unchanged, with real tools.

The agents know nothing about synapse0g. They are pointed at the proxy with `base_url` and a
session token as `api_key`, exactly as a developer would point them at any OpenAI-compatible
provider. That is the whole integration.
"""

from __future__ import annotations

import ast
import math
import operator
import re
import time
from collections import Counter
from dataclasses import dataclass, field

from synapse0g.tasks import Task

RESEARCH_PROMPT = (
    "You are a research assistant. Answer the user's question using the search tool, which "
    "searches a small library of encyclopedia paragraphs. Questions often need two searches: find "
    "the first fact, then search for the second. When you know the answer, reply with one line: "
    "'Answer: <short answer>'. Use a few words, or yes/no for yes/no questions."
)

MATH_PROMPT = (
    "You solve maths word problems. Work step by step, and use the calculator tool for every "
    "arithmetic operation. Finish with one line: 'Answer: <number>'."
)


@dataclass
class AgentResult:
    answer: str
    error: str | None
    wall_s: float
    model_calls: int
    extra: dict = field(default_factory=dict)


def _chat_model(base_url: str, token: str, model: str):
    from langchain_openai import ChatOpenAI

    return ChatOpenAI(model=model, base_url=base_url, api_key=token, temperature=0, max_retries=0, timeout=300)


def _run(agent, question: str, limit: int) -> AgentResult:
    t0 = time.perf_counter()
    try:
        out = agent.invoke({"messages": [("user", question)]}, config={"recursion_limit": limit})
    except Exception as e:  # recorded as a crashed task, never as a slow or failed answer
        return AgentResult("", f"{type(e).__name__}: {e}"[:500], time.perf_counter() - t0, 0)
    messages = out["messages"]
    calls = sum(1 for m in messages if m.type == "ai")
    content = messages[-1].content
    if isinstance(content, list):
        content = " ".join(c.get("text", "") if isinstance(c, dict) else str(c) for c in content)
    return AgentResult(str(content), None, time.perf_counter() - t0, calls)


# ---- research agent -------------------------------------------------------------
_WORD = re.compile(r"[a-z0-9]+")
_STOP = set(["the", "a", "an", "of", "in", "on", "at", "to", "for", "and", "or", "is", "was", "were", "be", "by", "with", "from", "as", "that", "this", "which", "who", "what", "when", "where"])


def _terms(text: str) -> list[str]:
    return [w for w in _WORD.findall(text.lower()) if w not in _STOP]


def make_search(context: tuple):
    docs = [(title, text, Counter(_terms(title + " " + title + " " + text))) for title, text in context]
    df = Counter(t for _, _, c in docs for t in c)
    n = len(docs)

    def search(query: str) -> str:
        """Search the encyclopedia. Returns the three most relevant paragraphs."""
        q = _terms(query)
        scored = []
        for title, text, counts in docs:
            score = sum(math.log(1 + n / df[t]) * (1 + math.log(counts[t])) for t in q if counts[t])
            scored.append((score, title, text))
        scored.sort(key=lambda s: -s[0])
        return "\n\n".join(f"# {title}\n{text}" for score, title, text in scored[:3] if score > 0) or "No results."

    return search


def run_research(task: Task, base_url: str, token: str, model: str) -> AgentResult:
    from langchain_core.tools import tool
    from langgraph.prebuilt import create_react_agent

    search = tool(make_search(task.context))
    agent = create_react_agent(_chat_model(base_url, token, model), [search], prompt=RESEARCH_PROMPT)
    return _run(agent, task.question, limit=14)


# ---- maths agent ----------------------------------------------------------------
_OPS = {
    ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod, ast.Pow: operator.pow,
    ast.USub: operator.neg, ast.UAdd: operator.pos,
}


def _eval(node):
    if isinstance(node, ast.Expression):
        return _eval(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, int | float):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
        if isinstance(node.op, ast.Pow) and abs(_eval(node.right)) > 100:
            raise ValueError("exponent too large")
        return _OPS[type(node.op)](_eval(node.left), _eval(node.right))
    if isinstance(node, ast.UnaryOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_eval(node.operand))
    raise ValueError("only numbers and + - * / // % ** are allowed")


def calculator(expression: str) -> str:
    """Evaluate an arithmetic expression, for example '(12 + 3) * 4 / 5'."""
    try:
        value = _eval(ast.parse(expression.replace(",", "").replace("^", "**"), mode="eval"))
    except Exception as e:
        return f"error: {e}"
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return str(round(value, 6) if isinstance(value, float) else value)


def run_math(task: Task, base_url: str, token: str, model: str) -> AgentResult:
    from langchain_core.tools import tool
    from langgraph.prebuilt import create_react_agent

    agent = create_react_agent(_chat_model(base_url, token, model), [tool(calculator)], prompt=MATH_PROMPT)
    return _run(agent, task.question, limit=20)


AGENTS = {"research": run_research, "math": run_math}
