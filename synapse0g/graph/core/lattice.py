"""The legal-transform table, computed by asking `effects.py`, so the page cannot drift from the rules.

Each effect tier gets a probe node, and each transform asks the predicate the passes use. A
transform is `cond` when the predicate says yes for one declaration of the node and no for another.
"""

from __future__ import annotations

from synapse0g.graph.core import effects
from synapse0g.graph.core.sir import Effect, Node

TRANSFORMS = ("CSE", "MEMOIZE", "REORDER", "PARALLELIZE", "BATCH", "SPECULATE", "ELIDE")


def _probe(effect: Effect, *, node_id: str = "probe", staleness_s: int | None = None,
           writes: tuple[str, ...] = ()) -> Node:
    return Node(id=node_id, kind="tool", target="this node", effect=effect, determinism="deterministic",
                staleness_s=staleness_s, writes=list(writes), outputs=["value"])


def _cell(allowed, refused) -> dict:
    """`allowed` is the finding for the friendliest declaration, `refused` for the strictest."""
    if allowed is not None:
        return {"status": "no", "why": allowed.message}
    if refused is not None:
        return {"status": "cond", "why": refused.precondition}
    return {"status": "yes", "why": None}


def _row(effect: Effect) -> dict:
    fresh = _probe(effect, staleness_s=3600 if effect == Effect.READ else None)
    bare = _probe(effect)
    sampled = _probe(effect, staleness_s=3600 if effect == Effect.READ else None)
    sampled.determinism = "stochastic"
    reuse = _cell(effects.may_reuse(fresh), effects.may_reuse(bare) or effects.may_reuse(sampled))
    writer = _probe(effect, writes=("a",))
    # `may_reorder` leaves writes to the key check, so that check is the rule for them.
    is_write = effect == Effect.WRITE
    disjoint = _probe(effect, node_id="other", writes=("b",)) if is_write else _probe(Effect.PURE, node_id="other")
    clashing = _probe(effect, node_id="other", writes=("a",)) if effect == Effect.WRITE else disjoint
    return {
        "effect": str(effect),
        "legal": {
            "CSE": reuse,
            "MEMOIZE": reuse,
            "REORDER": _cell(effects.may_reorder(fresh),
                             effects.may_run_concurrently(writer, clashing) if effect == Effect.WRITE else None),
            "PARALLELIZE": _cell(effects.may_run_concurrently(writer, disjoint),
                                 effects.may_run_concurrently(writer, clashing)),
            "BATCH": _cell(effects.may_batch(fresh), None),
            "SPECULATE": _cell(effects.may_speculate(fresh), None),
            # `may_eliminate` allows a write, but `dce` only removes one that nothing reads.
            "ELIDE": ({"status": "cond", "why": "nothing reads its keys"} if effect == Effect.WRITE
                      else _cell(effects.may_eliminate(fresh), None)),
        },
    }


def table() -> dict:
    return {"transforms": list(TRANSFORMS), "tiers": [_row(e) for e in Effect]}
