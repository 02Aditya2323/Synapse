# Same idea, with an EFFECTS table and a step that sends an email (external) and one that charges a card.
from typing import TypedDict
from openai import OpenAI
from langgraph.graph import StateGraph, START, END

client = OpenAI()

class State(TypedDict, total=False):
    ticket: str
    category: str
    sentiment: str
    history: str
    reply: str
    sent: dict
    refund: dict

def ask(sys_, user):
    return client.chat.completions.create(model="zg-main", messages=[{"role": "system", "content": sys_}, {"role": "user", "content": user}]).choices[0].message.content

def classify(s): return {"category": ask("Classify the ticket.", s["ticket"])}
def sentiment(s): return {"sentiment": ask("Sentiment of the ticket.", s["ticket"])}
def lookup_history(s): return {"history": "3 previous orders"}
def write_reply(s): return {"reply": ask("Support agent.", s["ticket"] + s["category"] + s["sentiment"] + s["history"])}
def send_email(s): return {"sent": {"to": "customer", "body": s["reply"]}}
def refund_card(s): return {"refund": {"amount": 20}}

g = StateGraph(State)
for f in (classify, sentiment, lookup_history, write_reply, send_email, refund_card):
    g.add_node(f.__name__, f)
order = ["classify", "sentiment", "lookup_history", "write_reply", "send_email", "refund_card"]
g.add_edge(START, order[0])
for a, b in zip(order, order[1:]):
    g.add_edge(a, b)
g.add_edge(order[-1], END)
builder = g

EFFECTS = {"classify": "pure", "sentiment": "pure", "lookup_history": {"effect": "read", "staleness_s": 600},
           "write_reply": "pure", "send_email": "external", "refund_card": "irreversible"}
