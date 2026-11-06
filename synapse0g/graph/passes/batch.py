"""`batch` -- call fusion.

Fuses *N* calls to the same target into one provider round-trip. docs/PASSES.md
section 8.

**Precondition.** The target declares batch support in `models.yaml` (`batch: true`);
all the calls are `pure` or `read`; there is no effect edge between them; and batching
does not change per-call semantics -- no shared conversational state. The registry flag
is load-bearing: fusing calls to a provider that does not support it either errors or,
worse, silently returns one answer for N questions.

**What it marks.** Like `parallelize`, this pass records a decision rather than
rewriting the graph: the fused nodes keep their identities so the dashboard can still
show N nodes, and the runtime is free to issue them as one request. Keeping the IR
honest about what the workflow asked for matters more than making it look optimized.

**Why this stays marks-only.** A chat completion takes one conversation, so *N* separate
prompts cannot share a request without becoming one prompt with several questions in it,
which is a different call. So this pass never fires on `code_review`, whose calls are all
chat. The honest route is an embeddings endpoint, which takes a list and returns one vector
per input, but no workflow makes embedding calls, and the local tools run in-process, so
there is no round trip to save. Nothing here should be described as real batching.
"""

from __future__ import annotations

from synapse0g.graph.core import effects
from synapse0g.graph.core.effects import Finding
from synapse0g.graph.core.sir import Graph, Node, Profile
from synapse0g.graph.passes.base import Estimate, Transformation, confidence_from, register

#: A fused request still costs one round trip, so fusing two calls saves one. Below two
#: there is nothing to fuse.
MIN_GROUP = 2


def _batchable_target(target: str) -> bool:
    try:
        from synapse0g.graph.runtime.providers import model_config

        return bool(model_config(target).batch)
    except Exception:  # noqa: BLE001 - not in the registry means it does not declare support
        return False


def _fusable(node: Node) -> Finding | None:
    if finding := effects.may_batch(node):
        return finding
    if node.determinism == "stochastic":
        return Finding(
            node=node.id,
            precondition="determinism!=stochastic",
            message=(
                f"{node.target} is an unseeded stochastic call; fusing several samples into one request "
                f"changes how they are drawn."
            ),
        )
    if not _batchable_target(node.target):
        return Finding(
            node=node.id,
            precondition="target declares batch support",
            message=(
                f"{node.target} does not declare `batch: true` in models.yaml, so fusing calls to it would "
                f"either fail or quietly answer several questions with one answer."
            ),
        )
    return None


def groups(graph: Graph) -> list[list[str]]:
    """Sets of calls to the same target that may be issued as one request.

    Same target, all fusable, and mutually unreachable -- if one feeds another they are
    a chain, not a batch.
    """
    by_target: dict[str, list[Node]] = {}
    for node in graph.nodes:
        if _fusable(node) is None:
            by_target.setdefault(node.target, []).append(node)

    out: list[list[str]] = []
    for nodes in by_target.values():
        if len(nodes) < MIN_GROUP:
            continue
        group: list[str] = []
        for node in sorted(nodes, key=lambda n: graph.index(n.id)):
            if any(_connected(graph, node.id, other) for other in group):
                continue
            group.append(node.id)
        if len(group) >= MIN_GROUP:
            out.append(group)

    # A map region over a batchable target is the common real case: k identical calls,
    # independent by construction.
    for region in graph.regions:
        if region.kind != "map":
            continue
        members = [nid for nid in region.body if _fusable(graph.node(nid)) is None]
        if members and members not in out:
            out.append(members)
    return out


def _connected(graph: Graph, a: str, b: str) -> bool:
    return b in graph.successors(a) or a in graph.successors(b)


class Batch:
    name = "batch"
    description = "Fuse several calls to one target into a single provider round-trip."
    precondition = (
        "The target declares batch support in models.yaml; every call is pure or read; no effect edge "
        "between them; no shared conversational state."
    )

    def preconditions(self, graph: Graph, profile: Profile) -> list[Finding]:
        """Only explain refusals where there was something to fuse.

        A graph with one embedding call gets no finding -- there is no missed
        opportunity to account for.
        """
        by_target: dict[str, list[Node]] = {}
        for node in graph.nodes:
            if node.target != "workflow_input":
                by_target.setdefault(node.target, []).append(node)
        findings: list[Finding] = []
        for nodes in by_target.values():
            if len(nodes) < MIN_GROUP:
                continue
            for node in nodes:
                if finding := _fusable(node):
                    findings.append(finding)
        return findings

    def predict(self, graph: Graph, profile: Profile) -> Estimate:
        found = groups(graph)
        if not found:
            return Estimate(note="no two fusable calls to one batch-capable target in this graph")

        ids = [nid for g in found for nid in g]
        saved_round_trips = sum(len(g) - 1 for g in found)
        if profile.is_empty:
            return Estimate(
                risk=0.1,
                confidence=0.0,
                note=f"{saved_round_trips} round trip(s) could be saved, but nothing here has been measured yet",
            )
        # The saving is the round trips, not the work: a fused call still does N calls'
        # worth of computation at the provider.
        per_call_ms = [profile.latency_of(nid) for nid in ids if profile.latency_of(nid)]
        overhead_ms = min(per_call_ms) if per_call_ms else 0.0
        return Estimate(
            d_latency_ms=-overhead_ms * saved_round_trips,
            d_tokens=0,  # same prompts, one envelope
            d_cost_usd=0.0,
            risk=0.1,
            confidence=confidence_from(profile, ids),
            note=f"{saved_round_trips} fewer round trip(s) across {len(found)} group(s)",
        )

    def apply(self, graph: Graph) -> tuple[Graph, list[Transformation]]:
        found = groups(graph)
        if not found:
            return graph, []

        membership = {nid: i for i, g in enumerate(found) for nid in g}
        nodes = []
        for node in graph.nodes:
            copy = node.model_copy(deep=True)
            if copy.id in membership:
                copy.meta = {**copy.meta, "batch_group": f"b{membership[copy.id]}"}
            nodes.append(copy)

        transformations = [
            Transformation(
                pass_name=self.name,
                nodes_before=list(g),
                nodes_after=list(g),
                description=(
                    f"{len(g)} calls to {graph.node(g[0]).target} marked as one batch, which would save "
                    f"{len(g) - 1} round trip(s). The runtime does not fuse them yet, so nothing changes in a run."
                ),
            )
            for g in found
        ]
        return graph.copy_with(nodes=nodes, meta={"batch_groups": found}), transformations


register(Batch())
