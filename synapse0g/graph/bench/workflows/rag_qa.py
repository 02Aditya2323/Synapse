"""rag_qa: multi-hop retrieval QA over HotpotQA. Showcases context_prune.

    plan(model)  [search(retrieval)  read(model)] x MIN_HOPS..MAX_HOPS  answer(model)

The loop is the standard RAG loop, and like most of them it hands every model call the
whole transcript so far: each hop re-sends every paragraph already read, so tokens grow
quadratically in the hop count though the notes alone carry what later hops need.
`graph` compiles MAX_HOPS hops as a chain, and the optional last hop sits in a branch
region on what the hop before it decided (see `bench/workflows/_llm.py`).
"""

from __future__ import annotations

from typing import Any

from synapse0g.graph.bench.tools.parsing import clip, field
from synapse0g.graph.bench.tools.search import search as bm25_search
from synapse0g.graph.bench.workflows._llm import declare, guard, last_given, prompt, transform
from synapse0g.graph.bench.workflows.research_fanout import Llm
from synapse0g.graph.core.sir import Effect, Graph
from synapse0g.graph.frontend.dsl import workflow as trace

MIN_HOPS = 2
MAX_HOPS = 3
DOCS_PER_SEARCH = 2
DOC_CHARS = 900

PLAN_SYSTEM = (
    "You plan searches over a small set of encyclopedia paragraphs to answer a question. "
    "Reply with exactly one line:\nSEARCH: <a few keywords naming the first thing to look up>"
)
READ_SYSTEM = (
    "You are answering a question step by step from encyclopedia paragraphs. "
    "First copy the facts from the new paragraphs that relate to the question: who, what, where, "
    "when, keeping exact names, dates and numbers. Then decide what to do next.\n"
    "Reply in exactly this format:\n"
    "NOTE: <one or two sentences of facts taken from the new paragraphs>\n"
    "NEXT: SEARCH <keywords for a fact that is still missing>\n"
    "If the notes are already enough to answer, the second line is instead:\n"
    "NEXT: ANSWER"
)
ANSWER_SYSTEM = (
    "Answer the question using only the transcript. If the question can be answered with yes or no, "
    "answer yes or no. Otherwise give the exact name, place, date or number. "
    "A few words only, no sentence, no explanation.\n"
    "Reply in exactly this format:\nANSWER: <answer>"
)


def plan_messages(question: str) -> list[dict]:
    return [{"role": "system", "content": PLAN_SYSTEM}, {"role": "user", "content": f"Question: {question}"}]


def parse_plan(text: str, question: str) -> str:
    return field(text, "SEARCH") or question


def search(query: str, paragraphs: list[dict], exclude: list[int]) -> list[dict]:
    return bm25_search(query, paragraphs, k=DOCS_PER_SEARCH, exclude=exclude)


def format_docs(docs: list[dict]) -> str:
    if not docs:
        return "(the search returned nothing)"
    return "\n".join(f"[{d['title']}] {clip(d['text'], DOC_CHARS)}" for d in docs)


def format_transcript(transcript: list[dict]) -> str:
    """Every earlier hop in full: what it searched, what came back, what was noted."""
    if not transcript:
        return "(nothing yet)"
    return "\n\n".join(f"Hop {i + 1}, search: {t['query']}\n{format_docs(t['docs'])}\nNote: {t['note']}"
                       for i, t in enumerate(transcript))


def read_messages(question: str, transcript: list[dict], docs: list[dict]) -> list[dict]:
    return [{"role": "system", "content": READ_SYSTEM},
            {"role": "user", "content": (f"Question: {question}\n\nTranscript so far:\n"
                                         f"{format_transcript(transcript)}\n\nNew paragraphs:\n{format_docs(docs)}")}]


def parse_read(text: str, question: str) -> tuple[str, str | None]:
    """(the note, the next query, None when the model is ready to answer)."""
    note = field(text, "NOTE") or clip(text, 300)
    nxt = field(text, "NEXT") or ""
    if not nxt.upper().startswith("SEARCH"):
        return note, None
    return note, nxt[len("SEARCH"):].strip(" :") or question


def answer_messages(question: str, transcript: list[dict]) -> list[dict]:
    return [{"role": "system", "content": ANSWER_SYSTEM},
            {"role": "user", "content": f"Question: {question}\n\nTranscript:\n{format_transcript(transcript)}"}]


def parse_answer(text: str) -> str:
    return field(text, "ANSWER") or clip(text, 200)


def run(task_input: dict[str, Any], llm: Llm) -> str:
    question, paragraphs = task_input["question"], task_input["paragraphs"]
    query = parse_plan(llm(plan_messages(question), 48), question)
    transcript: list[dict] = []
    seen: list[int] = []
    for hop in range(1, MAX_HOPS + 1):
        docs = search(query, paragraphs, seen)
        seen += [d["id"] for d in docs]
        note, next_query = parse_read(llm(read_messages(question, transcript, docs), 160), question)
        transcript.append({"query": query, "docs": docs, "note": note})
        if hop >= MIN_HOPS and next_query is None:
            break
        query = next_query or question
    return parse_answer(llm(answer_messages(question, transcript), 48))


@prompt("rag_qa.plan_prompt")
def plan_prompt(question: str) -> dict:
    return {"messages": plan_messages(question), "max_tokens": 48}


@transform("rag_qa.first_state", outputs=["state"])
def first_state(text: str, question: str) -> dict:
    return {"query": parse_plan(text, question), "transcript": [], "seen": []}


@declare("rag_qa.search", kind="retrieval", effect=Effect.READ, staleness_s=3600)
def hop_search(state: dict, paragraphs: list) -> list[dict]:
    return search(state["query"], paragraphs, state["seen"])


@prompt("rag_qa.read_prompt")
def read_prompt(question: str, state: dict, docs: list[dict]) -> dict:
    return {"messages": read_messages(question, state["transcript"], docs), "max_tokens": 160}


@transform("rag_qa.next_state", outputs=["state", "go"])
def next_state(text: str, question: str, state: dict, docs: list[dict]) -> dict:
    note, next_query = parse_read(text, question)
    return {"go": next_query is not None, "state": {
        "query": next_query or question,
        "transcript": state["transcript"] + [{"query": state["query"], "docs": docs, "note": note}],
        "seen": state["seen"] + [d["id"] for d in docs],
    }}


@prompt("rag_qa.final_prompt")
def final_prompt(question: str, second: dict, third: dict | None) -> dict:
    return {"messages": answer_messages(question, last_given(second, third)["transcript"]), "max_tokens": 48}


@transform("rag_qa.final_answer")
def final_answer(text: str) -> str:
    return parse_answer(text)


def graph() -> Graph:
    turns: list = []

    def hop(question, paragraphs, state):
        docs = hop_search(state, paragraphs)
        text = read_prompt(question, state, docs)
        after, go = next_state(text, question, state, docs)
        return [docs, text, after], after, go

    @trace(name="rag_qa")
    def rag(question: str, paragraphs: list) -> str:
        start = first_state(plan_prompt(question), question)
        _, one, _ = hop(question, paragraphs, start)
        _, two, go = hop(question, paragraphs, one)
        nodes, three, _ = hop(question, paragraphs, two)
        turns.append((nodes, go.ref))
        return final_answer(final_prompt(question, two, three))

    return guard(rag.compile(), turns)


workflow = graph  # `synapse compile bench.workflows.rag_qa:workflow`
