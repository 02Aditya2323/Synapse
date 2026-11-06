"""`dce` -- dead node elimination.

Removes nodes whose outputs nothing reachable from the workflow's declared outputs
consumes. docs/PASSES.md section 7.

**Precondition.** No node in the removed cone has effect `write`, `external` or
`irreversible`. **A node that sends an email is never dead, however unused its return
value is** -- its value is not the point, its effect is. This is the clearest small
example of why the IR types effects rather than just tracking data.

A `write` node is a middle case and worth being explicit about: mutating workflow-
internal state that nothing afterwards reads genuinely is dead, because the mutation is
not observable outside the execution. So `write` is eliminable, and the permission table
in docs/IR_SPEC.md section 4.1 says so.

**Typical win.** Modest on hand-written workflows, meaningful on lifted traces, where
agents routinely compute things they then ignore.
"""

from __future__ import annotations

from synapse0g.graph.core import effects
from synapse0g.graph.core.effects import Finding
from synapse0g.graph.core.sir import Graph, Profile
from synapse0g.graph.passes.base import (
    Estimate,
    Transformation,
    confidence_from,
    latency_saved,
    register,
    sum_cost,
    tokens_of,
)


def _live(graph: Graph) -> set[str]:
    """Everything the outputs depend on, plus everything that must stay for its effect.

    A side-effecting node is live by definition, and so is everything it depends on --
    you cannot keep the `place_order` call and delete the lookup that computed its
    argument.
    """
    live = graph.reachable_from_outputs()
    frontier = [n.id for n in graph.nodes if effects.is_side_effecting(n)]
    while frontier:
        nid = frontier.pop()
        if nid in live or not graph.has(nid):
            continue
        live.add(nid)
        frontier.extend(graph.node(nid).depends_on())
    # The input node is never dead: it is where the task enters the graph.
    live |= {n.id for n in graph.nodes if n.target == "workflow_input"}
    return live


def dead_nodes(graph: Graph) -> list[str]:
    live = _live(graph)
    return [n.id for n in graph.nodes if n.id not in live and effects.may_eliminate(n) is None]


class DCE:
    name = "dce"
    description = "Remove nodes whose results nothing uses."
    precondition = "No node in the removed cone is external or irreversible (an unused side effect is not dead)."

    def preconditions(self, graph: Graph, profile: Profile) -> list[Finding]:
        """Name every unused node that is kept anyway because of its effect."""
        live = graph.reachable_from_outputs()
        findings: list[Finding] = []
        for node in graph.nodes:
            if node.id in live or node.target == "workflow_input":
                continue
            if finding := effects.may_eliminate(node):
                findings.append(finding)
        return findings

    def predict(self, graph: Graph, profile: Profile) -> Estimate:
        doomed = dead_nodes(graph)
        if not doomed:
            return Estimate(note="every node contributes to an output or to an effect")
        return Estimate(
            d_latency_ms=-sum(latency_saved(profile, nid) for nid in doomed),
            d_tokens=-sum(tokens_of(profile, nid) for nid in doomed),
            d_cost_usd=_negate(sum_cost(profile, doomed)),
            risk=0.0,  # removing work nothing reads cannot change an output
            confidence=confidence_from(profile, doomed),
            note=f"{len(doomed)} node(s) compute nothing anyone reads",
        )

    def apply(self, graph: Graph) -> tuple[Graph, list[Transformation]]:
        current = graph
        transformations: list[Transformation] = []
        elided: dict[str, str] = dict(current.meta.get("elided") or {})

        # A fixpoint: removing a node can make its own inputs dead in turn.
        while True:
            doomed = dead_nodes(current)
            if not doomed:
                break
            for nid in doomed:
                node = current.node(nid)
                elided[nid] = "dce: nothing reads its result"
                transformations.append(
                    Transformation(
                        pass_name=self.name,
                        nodes_before=[nid],
                        nodes_after=[],
                        description=(
                            f"{nid} ({node.target}) computed a value nothing reads, and has effect "
                            f"{node.effect}, so removing it cannot change the result"
                        ),
                    )
                )
            current = current.without(doomed)

        if not transformations:
            return graph, []
        return current.copy_with(meta={"elided": elided}), transformations


def _negate(value: float | None) -> float | None:
    return None if value is None else -value


register(DCE())
