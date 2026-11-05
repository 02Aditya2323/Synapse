"""Call-level optimization: what changes in one model request, and when it is allowed.

An agent's model calls are grouped into *steps* by call site: the same system prompt and the
same tool list are the same step, however many turns of a loop it runs. A policy says, per step,
which techniques apply. The tuner (`tune.py`) decides the policy; this module only applies it.

Techniques:
  reasoning_off  switch the model's hidden thinking off for this step
  route          send this step to a cheaper model on the same provider
  compact        shorten tool results older than the last `keep_tool_results`
  cap            cap the output tokens of this step
  reuse          answer an identical earlier request from memory, within one run
  cache_marks    mark the system prompt and the newest messages for the provider's prompt cache,
                 so the next turn of a loop pays the cached price for everything before it.
                 The text the model sees does not change.

None of them touches a tool call. A response that asks for a tool that is not `read` is never
reused, because returning it again would repeat the action (see effects.py).
"""

from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import asdict, dataclass, replace

from synapse0g.registry import Model, Registry

TRIM_NOTE = " [... shortened by synapse0g]"


@dataclass(frozen=True)
class StepPolicy:
    reasoning_off: bool = False
    route_to: str | None = None
    keep_tool_results: int | None = None  # None: keep every tool result whole
    trim_chars: int = 300
    max_tokens: int | None = None
    reuse: bool = False
    cache_marks: bool = False

    def techniques(self) -> list[str]:
        out = []
        if self.reasoning_off:
            out.append("reasoning_off")
        if self.route_to:
            out.append(f"route:{self.route_to}")
        if self.keep_tool_results is not None:
            out.append(f"compact:{self.keep_tool_results}")
        if self.max_tokens:
            out.append(f"cap:{self.max_tokens}")
        if self.reuse:
            out.append("reuse")
        if self.cache_marks:
            out.append("cache_marks")
        return out

    def merged(self, **changes) -> StepPolicy:
        return replace(self, **changes)


EMPTY = StepPolicy()


@dataclass
class Policy:
    steps: dict[str, StepPolicy]

    @classmethod
    def empty(cls) -> Policy:
        return cls({})

    def for_step(self, step: str) -> StepPolicy:
        return self.steps.get(step) or self.steps.get("*") or EMPTY

    def with_change(self, step: str, **changes) -> Policy:
        steps = dict(self.steps)
        steps[step] = self.for_step(step).merged(**changes)
        return Policy(steps)

    def as_dict(self) -> dict:
        return {k: {f: v for f, v in asdict(sp).items() if v not in (None, False)} for k, sp in self.steps.items()}

    def describe(self) -> list[str]:
        return [f"{step}: {', '.join(sp.techniques()) or 'unchanged'}" for step, sp in self.steps.items()]


def _system_text(messages: list[dict]) -> str:
    for m in messages:
        if m.get("role") in ("system", "developer"):
            c = m.get("content")
            return c if isinstance(c, str) else json.dumps(c)
    return ""


def step_of(body: dict) -> tuple[str, str]:
    """(step key, readable label) for a request: its call site, not its contents."""
    system = _system_text(body.get("messages") or [])
    tools = sorted(((t.get("function") or t).get("name", "?")) for t in body.get("tools") or [])
    raw = json.dumps([body.get("model"), system, tools])
    key = "s-" + hashlib.sha256(raw.encode()).hexdigest()[:8]
    words = " ".join(system.split()[:6]) or "(no system prompt)"
    label = f"{words}" + (f" [tools: {', '.join(tools)}]" if tools else "")
    return key, label


def request_hash(body: dict) -> str:
    return hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()


def old_tool_chars(messages: list[dict], keep: int = 2) -> int:
    """Characters in tool results older than the last `keep`: what compaction could trim."""
    tool_idx = [i for i, m in enumerate(messages) if m.get("role") == "tool"]
    old = tool_idx[:-keep] if keep else tool_idx
    return sum(len(str(messages[i].get("content") or "")) for i in old)


def compact(messages: list[dict], keep: int, trim_chars: int) -> tuple[list[dict], int]:
    """Shorten tool results older than the last `keep`. Returns (messages, characters removed).

    Every message stays, so tool-call ids still pair with their results; only old result text
    is shortened.
    """
    tool_idx = [i for i, m in enumerate(messages) if m.get("role") == "tool"]
    old = set(tool_idx[:-keep] if keep else tool_idx)
    removed = 0
    out = []
    for i, m in enumerate(messages):
        content = m.get("content")
        if i in old and isinstance(content, str) and len(content) > trim_chars + len(TRIM_NOTE):
            m = dict(m)
            m["content"] = content[:trim_chars] + TRIM_NOTE
            removed += len(content) - len(m["content"])
        out.append(m)
    return out, removed


CACHE_MARK = {"type": "ephemeral"}


def _marked(content):
    """The same text, with a cache mark on its last part."""
    if isinstance(content, str):
        return [{"type": "text", "text": content, "cache_control": CACHE_MARK}]
    if isinstance(content, list) and content and isinstance(content[-1], dict):
        return content[:-1] + [{**content[-1], "cache_control": CACHE_MARK}]
    return content


def add_cache_marks(messages: list[dict], recent: int = 2) -> tuple[list[dict], int]:
    """Mark the system prompt and the last `recent` non-assistant messages.

    The previous turn's last message is marked again, so its cached prefix is found; the newest
    message is marked so the next turn finds this one. Returns (messages, marks added).
    """
    targets = [i for i, m in enumerate(messages) if m.get("role") in ("system", "developer")][:1]
    others = [i for i, m in enumerate(messages) if m.get("role") in ("user", "tool") and m.get("content")]
    targets += others[-recent:]
    out = list(messages)
    for i in set(targets):
        out[i] = {**out[i], "content": _marked(out[i].get("content"))}
    return out, len(set(targets))


def prepare(body: dict, sp: StepPolicy, registry: Registry) -> tuple[dict, Model, list[str]]:
    """The request to send upstream, the model that will answer, and the techniques applied.

    `body["model"]` is a registry id. The upstream body uses the provider's model tag.
    """
    asked = registry.model(body["model"])
    target = asked
    applied: list[str] = []
    out = copy.deepcopy(body)
    out.pop("stream_options", None)

    if sp.route_to and sp.route_to != asked.id:
        candidate = registry.model(sp.route_to)
        if candidate.provider != asked.provider:
            raise ValueError(f"routing {asked.id} -> {candidate.id} would cross providers")
        target = candidate
        applied.append(f"route:{candidate.id}")
    if sp.reasoning_off and target.reasoning_off:
        out.update(target.reasoning_off)
        applied.append("reasoning_off")
    if sp.keep_tool_results is not None:
        out["messages"], removed = compact(out.get("messages") or [], sp.keep_tool_results, sp.trim_chars)
        if removed:
            applied.append(f"compact:{sp.keep_tool_results}")
    if sp.cache_marks and target.explicit_cache:
        out["messages"], marks = add_cache_marks(out.get("messages") or [])
        if marks:
            applied.append("cache_marks")
    if sp.max_tokens:
        current = out.get("max_tokens") or out.get("max_completion_tokens")
        out.pop("max_completion_tokens", None)
        out["max_tokens"] = min(current, sp.max_tokens) if current else sp.max_tokens
        applied.append(f"cap:{out['max_tokens']}")
    out["model"] = target.tag
    return out, target, applied
