"""`speculate` -- prefetch and early start. **Off by default.**

Starts a node before it is known to be needed, inside a budget, and discards the result
if it turns out not to be. docs/PASSES.md section 9.

**Precondition.** The node is `pure` or `read`; a speculation budget is configured; the
discard path is free. Never `write`, `external` or `irreversible` -- the whole idea rests
on being able to throw the result away, and you cannot throw away an email.

**At run time.** The scheduler starts a marked node once its inputs exist and a slot is free,
while the branch is undecided. If the branch is taken, the guess becomes the result. If not,
a finished guess keeps its measured spend on the execution, and a running one is cancelled.

**Why it is off.** Wasted calls are real spend. Our OpenRouter free tier is 50 requests
per day per account, which makes speculation a poor trade during this build. docs/PASSES.md
is explicit: implement it, demonstrate it, leave it off. So this pass is complete, tested,
and absent from `DEFAULT_PIPELINE`, and it refuses to apply unless a budget is set
deliberately.

That is a defensible position to state rather than a gap to hide: the pass exists, the
estimate shows the trade (expected latency saved against expected wasted cost), and the
decision not to run it in the benchmark is a budget decision we made on purpose.
"""

from __future__ import annotations

from synapse0g.graph.core import effects
from synapse0g.graph.core.effects import Finding
from synapse0g.graph.core.sir import Graph, Node, Profile, Region
from synapse0g.graph.passes.base import Estimate, Transformation, register

#: Branch-taken probability assumed when the profile has measured none. Deliberately
#: 0.5 -- a coin flip is the honest prior, and it makes the expected-waste term large
#: enough that the pass rarely looks attractive without evidence.
ASSUMED_P_NEEDED = 0.5


def branch_regions(graph: Graph) -> list[Region]:
    return [r for r in graph.regions if r.kind == "branch"]


def _speculatable(graph: Graph, node: Node) -> Finding | None:
    if finding := effects.may_speculate(node):
        return finding
    region = graph.region_of(node.id)
    if region is None or region.kind != "branch":
        return Finding(
            node=node.id,
            precondition="inside a branch region",
            message=(
                f"{node.id} always runs, so there is nothing to speculate: speculation only pays where a node "
                f"might turn out not to be needed."
            ),
        )
    return None


def _budget(graph: Graph) -> float | None:
    """The speculation budget, in USD of expected waste per execution.

    `None` means none configured, which is the default and makes the pass decline.
    """
    budget = graph.meta.get("speculation_budget_usd")
    return None if budget is None else float(budget)


def candidates(graph: Graph) -> list[Node]:
    if _budget(graph) is None:
        return []
    return [
        n
        for n in graph.nodes
        if _speculatable(graph, n) is None and not n.meta.get("speculate")
    ]


def p_needed(profile: Profile, region_id: str) -> float:
    """Measured branch-taken frequency, else the honest coin flip."""
    measured = (profile.meta.get("branch_frequency") or {}).get(region_id)
    return float(measured) if measured is not None else ASSUMED_P_NEEDED


class Speculate:
    name = "speculate"
    description = "Start a node before it is known to be needed, inside a budget, and discard it if not."
    precondition = (
        "pure or read; inside a branch region; a speculation budget is configured; the discard path is "
        "free. Never write, external or irreversible."
    )

    def preconditions(self, graph: Graph, profile: Profile) -> list[Finding]:
        if _budget(graph) is None:
            return [
                Finding(
                    node="",
                    precondition="speculation budget configured",
                    message=(
                        "speculation is off: no budget is set. Wasted calls are real spend, and the OpenRouter "
                        "free tier in this build is 50 requests a day, so this pass is demonstrated rather than "
                        "enabled."
                    ),
                )
            ]
        findings: list[Finding] = []
        for region in branch_regions(graph):
            for node_id in region.body:
                if finding := effects.may_speculate(graph.node(node_id)):
                    findings.append(finding)
        return findings

    def predict(self, graph: Graph, profile: Profile) -> Estimate:
        budget = _budget(graph)
        if budget is None:
            return Estimate(note="speculation is off by default; set meta.speculation_budget_usd to enable it")
        targets = candidates(graph)
        if not targets:
            return Estimate(note="no branch-region node here is pure or read")

        saved_ms = 0.0
        wasted_cost: float | None = 0.0
        for node in targets:
            region = graph.region_of(node.id)
            assert region is not None
            p = p_needed(profile, region.id)
            saved_ms += profile.latency_of(node.id) * p
            cost = profile.node(node.id).cost_usd_mean if profile.node(node.id) else None
            if cost is None:
                wasted_cost = None
            elif wasted_cost is not None:
                wasted_cost += cost * (1.0 - p)

        over_budget = wasted_cost is not None and wasted_cost > budget
        return Estimate(
            d_latency_ms=-saved_ms,
            d_tokens=0,
            # The cost delta is *positive*: speculation spends money to save time, and
            # showing that as a saving would be a lie about the trade.
            d_cost_usd=wasted_cost,
            risk=0.15,
            confidence=0.0 if profile.is_empty else 0.5,
            declines=over_budget,
            note=(
                f"expected saving {saved_ms:.0f} ms against expected waste "
                f"{'unknown' if wasted_cost is None else f'${wasted_cost:.5f}'} per execution"
                + (f"; over the ${budget:.5f} budget, so it declines" if over_budget else "")
            ),
        )

    def apply(self, graph: Graph) -> tuple[Graph, list[Transformation]]:
        budget = _budget(graph)
        targets = candidates(graph)
        if budget is None or not targets:
            return graph, []

        marked = {n.id for n in targets}
        nodes = []
        for node in graph.nodes:
            copy = node.model_copy(deep=True)
            if copy.id in marked:
                copy.meta = {**copy.meta, "speculate": True}
            nodes.append(copy)

        transformations = [
            Transformation(
                pass_name=self.name,
                nodes_before=[n.id],
                nodes_after=[n.id],
                description=(
                    f"{n.id} ({n.target}) may now start before its branch is resolved, within a "
                    f"${budget:.5f} budget; its result is discarded if the branch is not taken"
                ),
            )
            for n in targets
        ]
        return graph.copy_with(nodes=nodes), transformations


register(Speculate())
