"""What a tool does to the world, read from its name and description.

The order of checks matters: a tool that both reads and pays ("get_and_charge") is treated as one
that pays. A tool we cannot classify is treated as irreversible until a person labels it.

Labels decide what the optimizer may do. A model response that asks for anything other than a
`read` tool is never reused, because reusing it would repeat the action.
"""

from __future__ import annotations

import re

LEVELS = ("pure", "read", "write", "external", "irreversible")

# Verbs that move money or destroy something, anywhere in the name: always irreversible.
_SEVERE = r"refund|pay|purchase|charge|transfer|delete|remove|cancel|withdraw|sell|buy|wire|destroy|drop|trade|sign"
# Nouns that usually mean an irreversible action unless a read verb comes first ("get_order" reads).
_SEVERE_NOUNS = r"order|payment|booking|checkout|transaction"
_EXTERNAL = r"send|email|mail|post|notify|publish|message|tweet|sms|upload|deploy|invite"
# after a read verb, only these verbs mean the tool also acts ("get_and_send"); "find_by_email" just reads
_EXTERNAL_VERBS = r"send|post|notify|publish|tweet|upload|deploy|invite"
_WRITE = r"write|update|create|set|save|insert|put|add|edit|modify|store"
_READ = r"get|search|lookup|list|fetch|read|find|query|retrieve|check|view|show|calc|calculator|calculate|compute|math|wiki|look"


def _words(text: str) -> list[str]:
    # split snake_case and camelCase so "issueRefund" and "issue_refund" both match
    text = re.sub(r"([a-z])([A-Z])", r"\1 \2", text)
    return [w for w in re.split(r"[\s_\-./]+", text.lower()) if w]


def _has(words: list[str], pattern: str) -> str | None:
    for w in words:
        if re.fullmatch(rf"({pattern})\w*", w):
            return w
    return None


def _from_words(words: list[str], where: str) -> tuple[str, str] | None:
    if hit := _has(words, _SEVERE):
        return "irreversible", f"{where} contains '{hit}'"
    if words and re.fullmatch(rf"({_READ})\w*", words[0]):
        if hit := _has(words, _EXTERNAL_VERBS):
            return "external", f"{where} contains '{hit}'"
        return "read", f"{where} starts with the read verb '{words[0]}'"
    if hit := _has(words, _SEVERE_NOUNS):
        return "irreversible", f"{where} contains '{hit}'"
    if hit := _has(words, _EXTERNAL):
        return "external", f"{where} contains '{hit}'"
    if hit := _has(words, _WRITE):
        return "write", f"{where} contains '{hit}'"
    if _has(words, _READ):
        return "read", f"{where} says it only reads"
    return None


def classify(name: str, description: str = "", labels: dict[str, str] | None = None) -> tuple[str, str]:
    """Return (effect, reason)."""
    if labels and name in labels:
        return labels[name], "labelled by the developer"
    found = _from_words(_words(name), "tool name")
    if found:
        return found
    found = _from_words(_words(description)[:12], "tool description")
    if found:
        return found
    return "irreversible", "unknown tool: treated as irreversible until labelled"


def tool_effects(tools: list[dict] | None, labels: dict[str, str] | None = None) -> dict[str, tuple[str, str]]:
    out = {}
    for t in tools or []:
        fn = t.get("function") or t
        name = fn.get("name", "?")
        out[name] = classify(name, fn.get("description", ""), labels)
    return out
