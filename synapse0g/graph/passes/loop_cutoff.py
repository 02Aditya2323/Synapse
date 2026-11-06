"""`loop_cutoff` -- fixpoint detection.

Marks a `loop` region so the scheduler stops it when two successive iterations produce
the same state hash. docs/PASSES.md section 6.

The argument is short and total: if the state after iteration *k* equals the state after
*k-1*, the loop body is a function of state that has reached a fixpoint, so iteration
*k+1* must produce that same state again, and no further iteration can change the
result. Everything after the repeat is pure cost.

**Precondition.** The loop body contains no `external` or `irreversible` node -- a body
that sends an email has a side effect per iteration, and "the state did not change" says
nothing about whether the email should be sent again. And the state hash must cover
everything the continuation condition reads, which is why the scheduler hashes **every
output of every body node** rather than a declared subset.

**The real example.** In our corpus a QA agent emitted the identical note three times
across three iterations before answering -- three model calls and three retrievals that
could not change the outcome.

**How it goes wrong.** The state hash omits something the condition reads, so a loop
still making progress gets cut. The mitigation is the breadth of the hash: narrowing it
would need an explicit declaration, and there is deliberately no way to do that yet.
"""

from __future__ import annotations

from synapse0g.graph.core import effects
from synapse0g.graph.core.effects import Finding
from synapse0g.graph.core.sir import Graph, Profile, Region
from synapse0g.graph.passes.base import Estimate, Transformation, confidence_from, register

#: Iterations beyond the first repeat that we assume a spinning loop would have run,
#: when the profile has no measured iteration-count distribution to use instead.
ASSUMED_WASTED_ITERATIONS = 1.0


def _body_findings(graph: Graph, region: Region) -> list[Finding]:
    findings: list[Finding] = []
    for node_id in region.body:
        node = graph.node(node_id)
        if effects.is_side_effecting(node):
            findings.append(
                Finding(
                    node=node_id,
                    precondition="loop body has no observable effect",
                    message=(
                        f"{node.target} is {node.effect} and runs once per iteration of {region.id}; cutting the "
                        f"loop early would change how many times that effect happens, so this loop is left alone."
                    ),
                )
            )
    return findings


def cuttable(graph: Graph) -> list[Region]:
    return [
        r
        for r in graph.regions
        if r.kind == "loop" and not _body_findings(graph, r) and r.id not in (graph.meta.get("loop_cutoff") or [])
    ]


class LoopCutoff:
    name = "loop_cutoff"
    description = "Stop a loop once two successive iterations produce the same state."
    precondition = (
        "The loop body contains no external or irreversible node, and the state hash covers everything "
        "the continuation condition reads."
    )

    def preconditions(self, graph: Graph, profile: Profile) -> list[Finding]:
        findings: list[Finding] = []
        for region in graph.regions:
            if region.kind == "loop":
                findings.extend(_body_findings(graph, region))
        return findings

    def predict(self, graph: Graph, profile: Profile) -> Estimate:
        regions = cuttable(graph)
        if not regions:
            loops = [r for r in graph.regions if r.kind == "loop"]
            return Estimate(
                note=(
                    "this graph has no loop region"
                    if not loops
                    else "every loop here has an observable effect in its body"
                )
            )

        body_ids = [nid for r in regions for nid in r.body]
        measured = (profile.meta.get("iterations") or {}) if not profile.is_empty else {}
        wasted = sum(
            float(measured.get(r.id, ASSUMED_WASTED_ITERATIONS)) for r in regions
        ) / max(1, len(regions))

        per_iteration_ms = sum(profile.latency_of(nid) for nid in body_ids)
        per_iteration_tokens = sum(
            int((profile.node(nid).tokens_in_mean or 0) + (profile.node(nid).tokens_out_mean or 0))
            for nid in body_ids
            if profile.node(nid) is not None
        )
        costs = [profile.node(nid).cost_usd_mean for nid in body_ids if profile.node(nid) is not None]
        per_iteration_cost = None if any(c is None for c in costs) or not costs else sum(costs)

        return Estimate(
            d_latency_ms=-per_iteration_ms * wasted,
            d_tokens=int(-per_iteration_tokens * wasted),
            d_cost_usd=None if per_iteration_cost is None else -per_iteration_cost * wasted,
            risk=0.08,
            confidence=confidence_from(profile, body_ids),
            note=(
                f"{len(regions)} loop region(s) may stop at a fixpoint; saving estimated at "
                f"{wasted:.1f} iteration(s)"
                + ("" if measured else ", assumed because no iteration counts have been measured yet")
            ),
        )

    def apply(self, graph: Graph) -> tuple[Graph, list[Transformation]]:
        regions = cuttable(graph)
        if not regions:
            return graph, []

        marked = sorted({*(graph.meta.get("loop_cutoff") or []), *(r.id for r in regions)})
        transformations = [
            Transformation(
                pass_name=self.name,
                nodes_before=list(r.body),
                nodes_after=list(r.body),
                description=(
                    f"loop {r.id} now stops as soon as two successive iterations produce the same state hash: "
                    f"a body that cannot change its own state cannot change the result"
                ),
            )
            for r in regions
        ]
        return graph.copy_with(meta={"loop_cutoff": marked}), transformations


register(LoopCutoff())
