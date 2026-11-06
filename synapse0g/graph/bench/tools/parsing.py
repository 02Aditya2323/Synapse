"""Reading the short 'LABEL: value' lines our workflows ask models for.

Small models wrap output in markdown, repeat the label or add chatter. A line we cannot
parse is a path the workflow handles, never an exception.
"""

from __future__ import annotations

import re


def field(text: str, label: str, *, first: bool = False) -> str | None:
    """Value of the last 'LABEL: value' line in `text` (the first, with first=True), or None."""
    pattern = re.compile(rf"^[\s>*#`-]*\**{re.escape(label)}\**\s*[:=-]\s*(.*)$", re.IGNORECASE | re.MULTILINE)
    matches = pattern.findall(text or "")
    if not matches:
        return None
    value = matches[0 if first else -1].strip().strip("`*\"' ").strip()
    return value or None


def fields(text: str, label: str) -> list[str]:
    """Every non-empty 'LABEL: value' line in `text`, in order."""
    pattern = re.compile(rf"^[\s>*#`-]*(?:\d+[.)]\s*)?\**{re.escape(label)}\**[ \t]*[:=-][ \t]*(.*)$",
                         re.IGNORECASE | re.MULTILINE)
    values = [m.strip().strip("`*\"' ").strip() for m in pattern.findall(text or "")]
    return [v for v in values if v]


def clip(text: str, limit: int) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 3].rstrip() + "..."
