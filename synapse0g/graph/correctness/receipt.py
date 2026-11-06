"""A receipt: one file that says what was optimized, how it was judged, and what happened.

The hash catches accidents and casual edits, with no key: anyone who rewrites the file can also
rewrite its hash. Only the signature, or a re-run, stands up to someone deliberately faking it.
Setting SYNAPSE_RECEIPT_KEY adds a signature, which only a holder of that key can check.
Neither shows the numbers were true. Re-running is what tests those, and demo workflows can be
re-run from the receipt. Timings will differ on another machine. The verdict should not.
"""

from __future__ import annotations

import hashlib
import hmac
import os
from typing import Any

from synapse0g.graph.core.hashing import canonical
from synapse0g.graph.core.store import StoreProtocol

RECEIPT_VERSION = 1


def _digest(body: dict) -> str:
    return hashlib.sha256(canonical(body).encode("utf-8")).hexdigest()


def _sign(digest: str) -> str | None:
    key = os.environ.get("SYNAPSE_RECEIPT_KEY")
    return hmac.new(key.encode(), digest.encode(), hashlib.sha256).hexdigest() if key else None


def build(store: StoreProtocol, graph_id: str) -> dict:
    """A receipt for an optimized graph that has a recorded firewall verdict."""
    result: Any = store.get_artifact("verdict", graph_id)
    if result is None:
        raise ValueError(f"{graph_id} has no firewall verdict yet, so there is nothing to certify. Verify it first.")
    candidate = store.load_graph(graph_id)
    original = store.load_graph(candidate.derived_from)
    body = {
        "receipt_version": RECEIPT_VERSION,
        "workflow": original.name,
        "original_graph": original.id,
        "optimized_graph": candidate.id,
        "original_sha256": _digest(original.model_dump(mode="json")),
        "optimized_sha256": _digest(candidate.model_dump(mode="json")),
        "pipeline": list(candidate.pipeline),
        "verdict": result["verdict"],
        "per_task": [{"task_id": t["task_id"], "status": t["status"]} for t in result["per_task"]],
        "fake_provider": bool(result.get("demo")),
    }
    digest = _digest(body)
    return {"body": body, "sha256": digest, "signature": _sign(digest)}


def check(receipt: dict) -> dict:
    """Is the file intact? `signature` is None when there is none, or no key here to check it."""
    digest = _digest(receipt["body"])
    intact = hmac.compare_digest(digest, receipt.get("sha256", ""))
    signed = receipt.get("signature")
    expected = _sign(digest)
    signature = None if not signed or expected is None else hmac.compare_digest(signed, expected)
    return {"intact": intact, "signature_valid": signature}


def rerun(store: StoreProtocol, receipt: dict) -> dict:
    """Run the firewall again, and compare the verdict with the one on the receipt."""
    from synapse0g.graph.correctness.verify import verify_graph

    body = receipt["body"]
    candidate = store.load_graph(body["optimized_graph"])
    if body.get("optimized_sha256") and _digest(candidate.model_dump(mode="json")) != body["optimized_sha256"]:
        return {"claimed": body["verdict"]["decision"], "now": None, "agrees": False,
                "note": "The stored graph is not the one this receipt describes."}
    again = verify_graph(store, body["optimized_graph"])["verdict"]
    return {
        "claimed": body["verdict"]["decision"],
        "now": again["decision"],
        "agrees": again["decision"] == body["verdict"]["decision"],
        "d_latency_pct_now": again.get("d_latency_pct"),
    }
