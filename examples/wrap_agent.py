"""A small support agent on the plain OpenAI SDK, with one added line: `synapse0g.wrap`.

    python examples/wrap_agent.py

It answers three tickets on 0G. It wastes work the way real agents do: every ticket asks for the
same policy summary again, and each ticket's two lookups wait for each other though neither reads
the other. The wrapper records the calls; at exit they are lifted into a graph and the passes
show both. The report lands in reports/observed/ and the dashboard's Recorded runs page shows it.
"""

from __future__ import annotations

import os

from openai import OpenAI

import synapse0g
from synapse0g.registry import load_env

load_env()
client = synapse0g.wrap(OpenAI(base_url="https://router-api.0g.ai/v1", api_key=os.environ.get("0G_API_KEY") or os.environ["ZG_API_KEY"]),
                        workflow="support_desk")  # the one added line
MODEL = "qwen3.7-flash"
POLICY = "Refunds within 30 days with a receipt. Damaged items are replaced free. Shipping takes 3 to 5 days."
TICKETS = ["My blender arrived cracked. Can I get a new one?", "I bought shoes 45 days ago. Can I return them?",
           "Where is my order? It has been 2 days."]


def ask(prompt: str) -> str:
    reply = client.chat.completions.create(model=MODEL, temperature=0, max_tokens=150, extra_body={"enable_thinking": False},
                                           messages=[{"role": "user", "content": prompt}])
    return reply.choices[0].message.content or ""


for ticket in TICKETS:
    category = ask(f"Classify this ticket as refund, damage or shipping. One word.\nTicket: {ticket}")
    summary = ask(f"Summarize this policy in one sentence: {POLICY}")  # the same request, every ticket
    print(ask(f"Category: {category}\nPolicy: {summary}\nWrite a two-sentence reply to: {ticket}")[:100])
