"""What the importers share: turning one recorded call into a `lift.Step`.

Recorded calls carry no explicit data flow, so it is inferred from their content.

**A prompt is kept as a hash.** `lift` drops `messages` from a node's arguments, so two
different prompts to one model would otherwise share a structural key and `cse` would
collapse them. `prompt_hash` keeps them apart and leaves identical prompts identical.

**Exports have no data flow, so it is inferred from content.** A step depends on an earlier
one if that step's output appears in its request. A missed edge is the dangerous direction,
since two nodes that look independent may be reordered, so a tool also depends on any
earlier model call that wrote one of its argument values.
"""

from __future__ import annotations

import json
import re
from typing import Any

from synapse0g.graph.core.hashing import fingerprint
from synapse0g.graph.frontend.lift import Step

MIN_FACT = 3
MIN_LEAF = 4
MIN_TEXT = 12
EMPTY = ("null", "[]", "{}", '""')


def text_of(value: Any) -> str:
    if value is None:
        return ""
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)


def parse_json(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return value
    return value


def leaves(value: Any, depth: int = 0) -> list[str]:
    """Distinctive leaf values of a result: strings and numbers, never booleans or None,
    which would link everything to everything."""
    if depth > 3 or isinstance(value, bool) or value is None:
        return []
    if isinstance(value, dict):
        return [x for v in value.values() for x in leaves(v, depth + 1)]
    if isinstance(value, list):
        return [x for v in value[:20] for x in leaves(v, depth + 1)]
    text = str(value)
    return [text] if len(text) >= MIN_LEAF else []


def facts_of(step: Step) -> list[str]:
    """Pieces of a step's output that a later step would carry along if it used them.

    A short answer ("refund", "yes") counts too: a classifier's one word is exactly what the next
    prompt carries, and missing that edge would let the two run out of order. It is matched as a
    whole word (see `_carried`), so linking too much is the only way this can err."""
    if step.kind == "llm":
        text = text_of(step.output).strip().rstrip(".")
        return [text[:80]] if len(text) >= MIN_FACT else []
    facts = [text_of(step.output), *leaves(step.output)]
    return [f for f in facts if len(f) >= MIN_FACT and f not in EMPTY]


TEMPLATE = 20  # characters two prompts must share to count as one template


def _without_shared(blob: str, source: str) -> str:
    """`blob` minus the stretches it shares with `source`: the words a prompt template supplies."""
    from difflib import SequenceMatcher

    keep, at = [], 0
    for m in SequenceMatcher(None, blob, source, autojunk=False).get_matching_blocks():
        if m.size >= TEMPLATE:
            keep.append(blob[at:m.a])
            at = m.a + m.size
    keep.append(blob[at:])
    return " ".join(keep)


def _carried(fact: str, blob: str, source: str) -> bool:
    """Did this request carry `fact` from the step whose request was `source`?

    A long fact is matched anywhere. A short one ("damage") is matched as a whole word, with its
    case, and only outside text the two requests share: "refund, damage or shipping" in a
    classifier's own prompt template is not that classifier's answer being passed on."""
    if len(fact) >= MIN_TEXT:
        return fact in blob
    return re.search(r"(?<![\w])" + re.escape(fact) + r"(?![\w])", _without_shared(blob, source)) is not None


def infer_parents(step: Step, earlier: list[Step]) -> list[int]:
    blob = json.dumps(step.request, ensure_ascii=False, default=str)
    args = leaves(step.request.get("args"))
    parents = []
    for other in earlier:
        source = json.dumps(other.request, ensure_ascii=False, default=str)
        carried = any(_carried(f, blob, source) for f in facts_of(other))
        chosen = other.kind == "llm" and any(a in text_of(other.output) for a in args)
        if carried or chosen:
            parents.append(other.idx)
    return parents


def model_step(model: str, messages: list | None, output: Any, **measured: Any) -> dict:
    """A model call. `messages` stays in the request so content inference can see it."""
    messages = messages or []
    return {"kind": "llm", "name": model, "output": output,
            "request": {"model_id": model, "messages": messages, "prompt_hash": fingerprint(messages)}, **measured}


def tool_step(name: str, args: Any, output: Any, **measured: Any) -> dict:
    return {"kind": "tool", "name": name, "output": output, "request": {"args": args}, **measured}


def retrieval_step(query: str, docs: Any, **measured: Any) -> dict:
    return {"kind": "retrieval", "name": "search", "output": docs, "request": {"args": {"query": query}}, **measured}


def build_steps(raw: list[dict]) -> list[Step]:
    """Number raw steps in execution order and infer who used whom."""
    steps: list[Step] = []
    for idx, fields in enumerate(raw):
        step = Step(idx=idx, **fields)
        step.parents = infer_parents(step, steps)
        steps.append(step)
    return steps
