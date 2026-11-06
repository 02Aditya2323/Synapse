"""Paste agent code (or give a path) and get its graph, before and after the passes. Nothing runs.

The code is read, never executed: `graph.frontend.automap` works out each function's effect from
what its body calls and builds the graph from the entry function's statements. Every label is
inferred, and each comes with the evidence for it.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from synapse0g.registry import ROOT

SAMPLES = Path(__file__).resolve().parent / "samples"
MAX_CODE = 200_000


def samples() -> list[dict]:
    listed = json.loads((SAMPLES / "samples.json").read_text(encoding="utf-8"))
    return [{**s, "code": (SAMPLES / s["file"]).read_text(encoding="utf-8")} for s in listed]


def _passes(results) -> list[dict]:
    return [{k: v for k, v in r.model_dump(mode="json").items() if k not in ("graph", "before", "after")} for r in results]


def inspect_code(*, code: str | None = None, path: str | None = None, entry: str | None = None) -> dict:
    from synapse0g.graph.core.sir import Profile
    from synapse0g.graph.frontend import automap
    from synapse0g.graph.passes.base import run_pipeline

    if code is not None:
        if len(code) > MAX_CODE:
            raise ValueError(f"the code is {len(code):,} characters; the limit is {MAX_CODE:,}")
        target = Path(tempfile.mkdtemp(prefix="synapse0g-map-")) / "agent.py"
        target.write_text(code, encoding="utf-8")
        shown = "pasted code"
    elif path:
        target = Path(path).expanduser()
        if not target.is_absolute():
            target = ROOT / target
        shown = str(path)
    else:
        raise ValueError("send code or a path")
    found = automap.scan(target, entry)
    out = {
        "source": shown,
        "functions": len(found.functions),
        "entry": found.entry.name if found.entry else None,
        "refusal": found.refusal,
        "tools": found.tools,
        "steps": automap.explain_table(found),
        "report": automap.report(found).replace(str(target), shown),
        "before": None, "after": None, "passes": [],
    }
    if found.graph is not None:
        after, results = run_pipeline(found.graph, Profile.empty(found.graph.id), None)
        out.update(before=found.graph.to_ui(), after=after.to_ui(), passes=_passes(results))
    return out
