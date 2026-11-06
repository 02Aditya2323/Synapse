"""code_review: map over files -> reduce to one finding. Showcases map parallelism.

    [review(model)] x files  conclude(model)

Map/reduce over a repo is embarrassingly parallel and usually written serially. Each file
is reviewed alone, so the reviews share nothing, and only `conclude` sees them all. The
`batch` pass does not fire here, because no chat model in models.yaml declares batch
support; say so rather than implying a batching win.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any

from synapse0g.graph.bench.tools.parsing import clip, field
from synapse0g.graph.bench.workflows._llm import prompt, transform
from synapse0g.graph.bench.workflows.research_fanout import Llm
from synapse0g.graph.core.sir import Graph
from synapse0g.graph.frontend.dsl import workflow as trace

REVIEW_SYSTEM = (
    "You review one Python function for a logic bug: a wrong comparison, a wrong boundary, or a wrong "
    "boolean operator. Reply in exactly this format:\n"
    "VERDICT: BUG or OK\n"
    "WHY: <one short sentence>"
)
CONCLUDE_SYSTEM = (
    "A reviewer read several files. Exactly one of them has a planted bug. Pick the single file most "
    "likely to have it. Reply in exactly this format:\nBUG: <file name>"
)


def review_messages(file: dict) -> list[dict]:
    return [{"role": "system", "content": REVIEW_SYSTEM},
            {"role": "user", "content": f"File {file['name']}:\n{file['code']}"}]


def note_of(text: str, name: str) -> str:
    return f"{name}: {field(text, 'VERDICT') or '?'}. {field(text, 'WHY') or clip(text, 160)}"


def conclude_messages(notes: list[str]) -> list[dict]:
    listed = "\n".join(f"- {n}" for n in notes)
    return [{"role": "system", "content": CONCLUDE_SYSTEM}, {"role": "user", "content": f"Review notes:\n{listed}"}]


def finding_of(text: str) -> str:
    return field(text, "BUG") or clip(text, 200)


def run(task_input: dict[str, Any], llm: Llm, map_fn: Callable[[Callable, Iterable], Iterable] = map) -> str:
    """The workflow as written, the plain Python form."""

    def review(file: dict) -> str:
        return note_of(llm(review_messages(file), 64), file["name"])

    notes = list(map_fn(review, task_input["files"]))
    return finding_of(llm(conclude_messages(notes), 32))


@prompt("code_review.review_prompt")
def review_prompt(file: dict) -> dict:
    return {"messages": review_messages(file), "max_tokens": 64}


@transform("code_review.parse_review")
def parse_review(text: str, file: dict) -> str:
    return note_of(text, file["name"])


@prompt("code_review.conclude_prompt")
def conclude_prompt(notes: list[str]) -> dict:
    return {"messages": conclude_messages(notes), "max_tokens": 32}


@transform("code_review.parse_finding")
def parse_finding(text: str) -> str:
    return finding_of(text)


@trace(name="code_review")
def review_files(files: list) -> str:
    notes = [parse_review(review_prompt(f), f) for f in files]
    return parse_finding(conclude_prompt(notes))


def graph() -> Graph:
    return review_files.compile()


workflow = graph  # `synapse compile bench.workflows.code_review:workflow`
