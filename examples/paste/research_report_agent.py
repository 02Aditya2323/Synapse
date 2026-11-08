# A typical hand-written LangGraph research agent: plain openai client, no EFFECTS table.
from typing import TypedDict
from openai import OpenAI
from langgraph.graph import StateGraph, START, END

client = OpenAI()
MODEL = "qwen3.8-flash"

class State(TypedDict, total=False):
    topic: str
    outline: str
    facts: str
    risks: str
    draft: str
    final: str

def llm(system, user):
    r = client.chat.completions.create(model=MODEL, messages=[{"role": "system", "content": system}, {"role": "user", "content": user}])
    return r.choices[0].message.content

def outline(s: State):  return {"outline": llm("Outline writer.", s["topic"])}
def facts(s: State):    return {"facts": llm("Fact finder.", s["topic"])}
def risks(s: State):    return {"risks": llm("Risk analyst.", s["topic"])}
def draft(s: State):    return {"draft": llm("Writer.", s["outline"] + s["facts"] + s["risks"])}
def polish(s: State):   return {"final": llm("Editor.", s["draft"])}

g = StateGraph(State)
for f in (outline, facts, risks, draft, polish):
    g.add_node(f.__name__, f)
g.add_edge(START, "outline"); g.add_edge("outline", "facts"); g.add_edge("facts", "risks")
g.add_edge("risks", "draft"); g.add_edge("draft", "polish"); g.add_edge("polish", END)
app = g.compile()
