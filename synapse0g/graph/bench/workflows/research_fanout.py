"""research_fanout: plan -> K independent sub-queries -> synthesize. The M1 gate workflow.

    plan(model)  [search(retrieval)  summarize(model)] x K  synthesize(model)

Deep-research agents are written exactly like this, and written sequentially, which is the
inefficiency the problem statement describes. K is fixed so the graph is static: 2K + 2
nodes, and the critical path through it is four nodes deep however large K is.

The functions here are the node bodies. Each takes everything it depends on as an
argument and keeps no state, so every dependency stays explicit when they are lifted to
SIR. `research` is the same workflow in the DSL, and `run` is the plain Python form the
pilot uses. Calling a decorated function outside a trace just calls it.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any

from synapse0g.graph.bench.tools.parsing import clip, field, fields
from synapse0g.graph.bench.tools.search import search as bm25_search
from synapse0g.graph.bench.workflows._llm import declare, prompt, transform
from synapse0g.graph.core.sir import Effect, Graph
from synapse0g.graph.frontend.dsl import workflow as trace

K = 6
DOCS_PER_SEARCH = 2
DOC_CHARS = 700

PLAN_SYSTEM = (
    f"You plan research over a small set of encyclopedia paragraphs. Break the question into {K} separate "
    "lookups: each names one person, place, work or fact worth finding. Different lines must look up "
    f"different things. Reply with exactly {K} lines and nothing else:\nSEARCH: <a few keywords>"
)
SUMMARIZE_SYSTEM = (
    "You take notes for a research question. Copy from the paragraphs only the facts that relate to the "
    "question: who, what, where, when, keeping exact names, dates and numbers. Reply in exactly this "
    "format:\nNOTE: <one or two sentences of facts>"
)
SYNTHESIZE_SYSTEM = (
    "Answer the question using only the notes. If it can be answered with yes or no, answer yes or no. "
    "Otherwise give the exact name, place, date or number from the notes. A few words only, no sentence, "
    "no explanation.\nReply in exactly this format:\nANSWER: <answer>"
)

Llm = Callable[[list[dict], int], str]  # (messages, max_tokens) -> text


def plan_messages(question: str) -> list[dict]:
    return [{"role": "system", "content": PLAN_SYSTEM}, {"role": "user", "content": f"Question: {question}"}]


@transform("research_fanout.parse_plan")
def parse_plan(text: str, question: str) -> list[str]:
    """Exactly K sub-queries. A model that returns fewer falls back on the question itself."""
    queries = fields(text, "SEARCH")[:K]
    return queries + [question] * (K - len(queries))


@declare("research_fanout.search", kind="retrieval", effect=Effect.READ, staleness_s=3600)
def search(query: str, paragraphs: list[dict]) -> list[dict]:
    return bm25_search(query, paragraphs, k=DOCS_PER_SEARCH)


def _format_docs(docs: list[dict]) -> str:
    if not docs:
        return "(the search returned nothing)"
    return "\n".join(f"[{d['title']}] {clip(d['text'], DOC_CHARS)}" for d in docs)


def summarize_messages(question: str, docs: list[dict]) -> list[dict]:
    return [{"role": "system", "content": SUMMARIZE_SYSTEM},
            {"role": "user", "content": f"Question: {question}\n\nParagraphs:\n{_format_docs(docs)}"}]


@transform("research_fanout.parse_note")
def parse_note(text: str) -> str:
    return field(text, "NOTE") or clip(text, 300)


def synthesize_messages(question: str, notes: list[str]) -> list[dict]:
    listed = "\n".join(f"- {n}" for n in notes)
    return [{"role": "system", "content": SYNTHESIZE_SYSTEM},
            {"role": "user", "content": f"Question: {question}\n\nNotes:\n{listed}"}]


@transform("research_fanout.parse_answer")
def parse_answer(text: str) -> str:
    return field(text, "ANSWER") or clip(text, 200)


@prompt("research_fanout.plan_prompt")
def plan_prompt(question: str) -> dict:
    return {"messages": plan_messages(question), "max_tokens": 96}


@prompt("research_fanout.summarize_prompt")
def summarize_prompt(question: str, docs: list[dict]) -> dict:
    return {"messages": summarize_messages(question, docs), "max_tokens": 96}


@prompt("research_fanout.synthesize_prompt")
def synthesize_prompt(question: str, notes: list[str]) -> dict:
    return {"messages": synthesize_messages(question, notes), "max_tokens": 48}


@trace(name="research_fanout")
def research(question: str, paragraphs: list) -> str:
    queries = parse_plan(plan_prompt(question), question)
    notes = [parse_note(summarize_prompt(question, search(q, paragraphs))) for q in queries]
    return parse_answer(synthesize_prompt(question, notes))


def graph() -> Graph:
    return research.compile()


workflow = graph  # `synapse compile bench.workflows.research_fanout:workflow`


def run(task_input: dict[str, Any], llm: Llm,
        map_fn: Callable[[Callable, Iterable], Iterable] = map) -> str:
    """One execution. `map_fn` is how the K branches run: the builtin map is the workflow as
    written; a thread pool's map shows what independence is worth."""
    question, paragraphs = task_input["question"], task_input["paragraphs"]
    queries = parse_plan(llm(plan_messages(question), 96), question)

    def branch(query: str) -> str:
        return parse_note(llm(summarize_messages(question, search(query, paragraphs)), 96))

    notes = list(map_fn(branch, queries))
    return parse_answer(llm(synthesize_messages(question, notes), 48))
