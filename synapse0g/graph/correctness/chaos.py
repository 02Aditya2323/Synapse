"""Unsafe-optimization experiments adapted from Black Box replay/chaos.py (7af6e69).

Every row invokes a real precondition or paired execution check. Exceptions are errors,
never 'caught'. Local deterministic handlers require no provider or live side effects.
"""

from __future__ import annotations

from pydantic import BaseModel

from synapse0g.graph.core.sir import Graph, Literal_, Node, Ref
from synapse0g.graph.correctness.equivalence import compare
from synapse0g.graph.correctness.firewall import verify_pairs
from synapse0g.graph.correctness.preconditions import audit_transform, check_request
from synapse0g.graph.correctness.runner import Task, ValidationRunner, run_pairs
from synapse0g.graph.profile.collect import NoiseBand


class ChaosRow(BaseModel):
    id: str
    planted: str
    expected_layer: str
    caught_by: str | None
    verdict: str
    reason: str


def _node(nid, target="double", **kw):
    return Node(id=nid, kind="transform", target=target, effect=kw.pop("effect", "pure"), **kw)


def _graph(nodes, output=None):
    return Graph(id="original", name="chaos", nodes=nodes, outputs=output or {"answer": Ref(node=nodes[-1].id)})


async def run_chaos(on_event=None) -> list[ChaosRow]:
    emit = on_event or (lambda event: None)
    rows = []

    def record(id, planted, layer, findings):
        reason = "; ".join(f.message for f in findings)
        rows.append(
            ChaosRow(
                id=id,
                planted=planted,
                expected_layer=layer,
                caught_by=layer if findings else None,
                verdict="BLOCK" if findings else "MISSED",
                reason=reason,
            )
        )

    g = _graph([_node("a", determinism="stochastic")])
    record("U1", "Merge unseeded stochastic calls", "L1", check_request(g, "cse", ["a"]))
    g = _graph([_node("a", effect="read", staleness_s=1)])
    record("U2", "Reuse a read beyond its freshness window", "L1", check_request(g, "memoize", ["a"], ages_s={"a": 2}))
    g = _graph([_node("a", effect="write", writes=["cart"]), _node("b", effect="write", writes=["cart"])])
    record("U4", "Parallelize writes to the same key", "L1", check_request(g, "parallelize", ["a", "b"]))
    g = _graph([_node("a", effect="external"), _node("b", effect="external")])
    changed = g.model_copy(deep=True)
    changed.nodes.reverse()
    record("U5", "Reorder external actions", "L1", audit_transform(g, changed, "parallelize", []))
    g = _graph([_node("a", target="place_order", effect="irreversible")])
    record("U6", "Cache an irreversible order", "L1", check_request(g, "memoize", ["a"]))

    async def differential(id, planted, original, candidate, handlers):
        try:
            pairs = await run_pairs(
                original, candidate, [Task(id=id, split="test")], ValidationRunner(handlers), include_warm=False
            )
            checked = compare(*pairs[0], relation="exact")
            caught = checked.status == "broken"
            rows.append(
                ChaosRow(
                    id=id,
                    planted=planted,
                    expected_layer="L2",
                    caught_by="L2" if caught else None,
                    verdict="BLOCK" if caught else "ERROR" if checked.status == "crashed" else "MISSED",
                    reason=checked.error or f"Original {pairs[0][0].output!r}; candidate {pairs[0][1].output!r}",
                )
            )
        except Exception as exc:  # noqa: BLE001 - record plugin/handler failures in the returned report
            rows.append(
                ChaosRow(
                    id=id,
                    planted=planted,
                    expected_layer="L2",
                    caught_by=None,
                    verdict="ERROR",
                    reason=f"{type(exc).__name__}: {exc}",
                )
            )

    # Real cache collision: all fields -> empty field set; a different argument now reuses a's output.
    original = _graph(
        [
            _node("a", args={"x": Literal_(value=1)}, meta={"memoize": True}),
            _node("b", args={"x": Literal_(value=2)}, meta={"memoize": True}),
        ],
        {"a": Ref(node="a"), "b": Ref(node="b")},
    )
    candidate = original.model_copy(deep=True)
    candidate.id = "bad_cache"
    for n in candidate.nodes:
        n.cache_key_fields = []
    await differential(
        "U3",
        "Narrow cache key so different inputs collide",
        original,
        candidate,
        {"double": lambda args, task: args["x"] * 2},
    )
    original = _graph([_node("a", target="answer", args={"context": Literal_(value="required fact")})])
    candidate = original.model_copy(deep=True)
    candidate.id = "bad_prune"
    candidate.nodes[0].args = {}
    await differential(
        "U7",
        "Prune context that determines the answer",
        original,
        candidate,
        {"answer": lambda args, task: args.get("context", "unknown")},
    )
    original = _graph([_node("a", target="capable")])
    candidate = original.model_copy(deep=True)
    candidate.id = "bad_model"
    candidate.nodes[0].target = "incapable"
    await differential(
        "U8",
        "Downgrade to an incapable handler",
        original,
        candidate,
        {"capable": lambda args, task: 42, "incapable": lambda args, task: 0},
    )
    # Expanded loop iterations: visible answer stays 0 while remaining changes 2 -> 1 -> 0.
    # Hashing only answer falsely identifies a fixpoint before the condition reaches 0.
    original = _graph(
        [
            _node("a", target="iterate", args={"state": Literal_(value={"answer": 0, "remaining": 2})}),
            _node("b", target="iterate", args={"state": Ref(node="a")}),
        ]
    )
    candidate = original.model_copy(deep=True)
    candidate.id = "bad_cutoff"
    candidate.outputs = {"answer": Ref(node="a")}

    def iterate(args, task):
        s = args["state"]
        return {"answer": 0, "remaining": s["remaining"] - 1}

    await differential(
        "U9", "Cut expanded loop using a hash that omits condition state", original, candidate, {"iterate": iterate}
    )
    g = _graph([_node("a")])
    pairs = await run_pairs(
        g, g, [Task(id="U10", split="test")], ValidationRunner({"double": lambda a, t: 1}), include_warm=False
    )
    report = verify_pairs(pairs, noise=NoiseBand(latency_pct=0, samples=1), relation="exact", calibration_split="test")
    caught = "threshold must be calibrated" in report.verdict.reason
    rows.append(
        ChaosRow(
            id="U10",
            planted="Calibrate early exit on test tasks",
            expected_layer="L3",
            caught_by="L3" if caught else None,
            verdict="BLOCK" if caught else "MISSED",
            reason=report.verdict.reason,
        )
    )
    rows.sort(key=lambda r: int(r.id[1:]))
    for row in rows:
        emit({"type": "chaos", **row.model_dump()})
    return rows


def format_report(rows: list[ChaosRow]) -> str:
    lines = ["| Fault | Planted | Caught by | Verdict |", "|---|---|---|---|"]
    lines.extend(f"| {r.id} | {r.planted} | {r.caught_by or '—'} | {r.verdict} |" for r in rows)
    return "\n".join(lines)
