"""An ordinary LangGraph graph, written as one chain, compiled and optimized by Synapse.


The graph runs facts -> risks -> history -> write, one after another, the way such graphs are often
written. `risks` and `history` never read what `facts` wrote. Synapse measures each node's reads
and writes, finds that, and lets the three run at the same time. `publishing_graph` adds a node that
changes the outside world: Synapse never traces or moves it.
"""

from __future__ import annotations

import time
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from synapse0g.graph.runtime import providers
from synapse0g.graph.runtime.providers import LLMResult

MODEL = "agent-small"

#: What each node does to the world. Model calls at temperature 0 with a seed are `seeded`.
NODES = {
    "facts": {"effect": "pure", "determinism": "seeded"},
    "risks": {"effect": "pure", "determinism": "seeded"},
    "history": {"effect": "pure", "determinism": "seeded"},
    "write": {"effect": "pure", "determinism": "seeded"},
    # Posts to a website: external, so never traced, reordered, cached or run outside a live run.
    "publish": {"effect": "external", "updates": ["published"]},
}

TASKS = [{"id": f"lg-{t.replace(' ', '-')}", "input": {"topic": t}} for t in (
    "honey bees", "tidal energy", "sourdough", "coral reefs", "solar sails", "urban trees")]


def check(task_id: str, output: object) -> bool:
    """The article names its topic and draws on all three research notes."""
    topic = next(t["input"]["topic"] for t in TASKS if t["id"] == task_id)
    article = str((output or {}).get("article", "")).lower()
    return topic in article and all(f"<{s}:" in article for s in ("facts", "risks", "history"))


class State(TypedDict, total=False):
    topic: str
    facts: str
    risks: str
    history: str
    article: str
    published: str


def _ask(prompt: str) -> str:
    return providers.chat(MODEL, [{"role": "user", "content": prompt}]).text


def facts(state: State) -> dict:
    return {"facts": _ask(f"List the key facts about {state['topic']}.")}


def risks(state: State) -> dict:
    return {"risks": _ask(f"List the main risks of {state['topic']}.")}


def history(state: State) -> dict:
    return {"history": _ask(f"Summarise the history of {state['topic']}.")}


def write(state: State) -> dict:
    notes = "\n".join(state[k] for k in ("facts", "risks", "history"))
    return {"article": _ask(f"Write an article about {state['topic']} from these notes:\n{notes}")}


def publish(state: State) -> dict:
    return {"published": f"posted: {state['article'][:40]}"}


def _chain(*names: str) -> StateGraph:
    g = StateGraph(State)
    funcs = {"facts": facts, "risks": risks, "history": history, "write": write, "publish": publish}
    for name in names:
        g.add_node(name, funcs[name])
    g.add_edge(START, names[0])
    for a, b in zip(names, names[1:], strict=False):
        g.add_edge(a, b)
    g.add_edge(names[-1], END)
    return g


def graph() -> StateGraph:
    return _chain("facts", "risks", "history", "write")


def publishing_graph() -> StateGraph:
    return _chain("facts", "risks", "history", "write", "publish")


def install_fake_model(latency_s: float = 0.4) -> None:
    """A deterministic stand-in for `agent-small`. Its timings are scheduling only, never reportable."""

    def answer(messages, tools, params):
        time.sleep(latency_s)
        prompt = messages[-1]["content"]
        topic = prompt.split(" about ")[-1].split(" of ")[-1].split(" from ")[0].split(".")[0].split(":")[0]
        if prompt.startswith("Write"):
            used = [s for s in ("facts", "risks", "history") if f"<{s}:" in prompt]
            text = f"An article about {topic}. " + " ".join(f"<{s}: cited>" for s in used)
        else:
            kind = "facts" if "facts" in prompt else "risks" if "risks" in prompt else "history"
            text = f"<{kind}: {topic}>"
        return LLMResult(text=text, tool_calls=[], finish_reason="stop", tokens_in=len(prompt) // 4,
                         tokens_out=len(text) // 4, provider="fake", model_tag="fake", model_id=MODEL)

    providers.register_fake(MODEL, answer)
