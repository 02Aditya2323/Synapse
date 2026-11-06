"""`constfold` -- constant folding.

Evaluates `deterministic` `pure` `transform` nodes whose arguments are all literals,
and replaces them with their result. docs/PASSES.md section 10.

**Precondition.** kind is `transform`, effect is `pure`, determinism is
`deterministic`, every argument is a literal. All four, together: a `tool` node with
literal arguments may still talk to the world, and a `stochastic` transform may not
give the same answer twice.

**Typical win.** Small. It is in the catalogue because it is textbook, nearly free to
implement, and it makes the pass list legibly a compiler's pass list -- which is part of
the argument that this is a compiler and not a scheduler with opinions.

One honest limitation, stated here rather than discovered on stage: the node is folded
by **executing its implementation at compile time**. That is sound for a pure
deterministic transform, and the pass refuses anything else; but it does mean the
implementation has to be importable, so a folded node on a lifted trace is only folded
when we have its code. Where we do not, the pass declines and says so.
"""

from __future__ import annotations

from synapse0g.graph.core.effects import Finding
from synapse0g.graph.core.sir import Graph, Literal_, Node, Profile, Ref
from synapse0g.graph.passes.base import (
    Estimate,
    Transformation,
    confidence_from,
    latency_saved,
    register,
    tokens_of,
)


def _is_foldable_shape(node: Node) -> bool:
    return (
        node.kind == "transform"
        and node.effect == "pure"
        and node.determinism == "deterministic"
        and bool(node.args)
        and node.all_args_literal()
        and len(node.outputs) == 1
        and node.target != "workflow_input"
    )


def _implementation(node: Node):
    from synapse0g.graph.frontend import dsl

    spec = dsl.spec_for(node.target)
    return None if spec is None else spec.impl


def foldable(graph: Graph) -> list[Node]:
    """Nodes this pass may fold, minus any it has already declined.

    The `constfold_declined` marker is what keeps `apply()`'s fixpoint loop finite: a
    node that cannot be folded (it raised, or the workflow returns it) must not be
    offered again on the next round.
    """
    return [
        n
        for n in graph.nodes
        if _is_foldable_shape(n) and not n.meta.get("constfold_declined") and _implementation(n) is not None
    ]


class ConstFold:
    name = "constfold"
    description = "Evaluate pure deterministic transforms over literals at compile time."
    precondition = "kind == transform, effect == pure, determinism == deterministic, every argument a literal."

    def preconditions(self, graph: Graph, profile: Profile) -> list[Finding]:
        findings: list[Finding] = []
        for node in graph.nodes:
            if node.target == "workflow_input" or not node.args or not node.all_args_literal():
                continue
            if node.kind != "transform" or node.effect != "pure":
                findings.append(
                    Finding(
                        node=node.id,
                        precondition="transform and pure",
                        message=(
                            f"{node.target} has only literal arguments, but it is a {node.kind} node with effect "
                            f"{node.effect}; evaluating it at compile time could do something observable."
                        ),
                    )
                )
            elif node.determinism != "deterministic":
                findings.append(
                    Finding(
                        node=node.id,
                        precondition="determinism==deterministic",
                        message=(
                            f"{node.target} is {node.determinism}, so the value folded in now is only one of the "
                            f"answers it could give."
                        ),
                    )
                )
            elif _implementation(node) is None:
                findings.append(
                    Finding(
                        node=node.id,
                        precondition="implementation available",
                        message=(
                            f"{node.target} is foldable in principle, but its implementation is not registered in "
                            f"this process (lifted traces often carry no code), so there is nothing to evaluate."
                        ),
                    )
                )
        return findings

    def predict(self, graph: Graph, profile: Profile) -> Estimate:
        targets = foldable(graph)
        if not targets:
            return Estimate(note="no pure deterministic transform here has all-literal arguments")
        ids = [n.id for n in targets]
        return Estimate(
            d_latency_ms=-sum(latency_saved(profile, nid) for nid in ids),
            d_tokens=-sum(tokens_of(profile, nid) for nid in ids),
            d_cost_usd=0.0,  # a local transform costs no provider money
            risk=0.0,
            confidence=confidence_from(profile, ids),
            note=f"{len(ids)} node(s) can be evaluated now",
        )

    def apply(self, graph: Graph) -> tuple[Graph, list[Transformation]]:
        current = graph
        transformations: list[Transformation] = []
        elided: dict[str, str] = dict(current.meta.get("elided") or {})

        while True:
            targets = foldable(current)
            if not targets:
                break
            node = targets[0]
            impl = _implementation(node)
            assert impl is not None
            try:
                value = impl(**node.literal_args())
            except Exception as exc:  # noqa: BLE001 - a fold that raises is left alone, loudly
                # Not swallowed: the node stays, and the reason is on the graph where the
                # dashboard and the next session can both see it.
                elided.setdefault(
                    node.id, f"constfold: declined, evaluating it raised {type(exc).__name__}: {exc}"
                )
                current = _mark_unfoldable(current, node.id)
                continue

            consumers = current.consumers(node.id)
            nodes = []
            for other in current.nodes:
                if other.id == node.id:
                    continue
                copy = other.model_copy(deep=True)
                copy.args = {
                    name: (Literal_(value=value) if isinstance(arg, Ref) and arg.node == node.id else arg)
                    for name, arg in copy.args.items()
                }
                nodes.append(copy)

            if any(ref.node == node.id for ref in current.outputs.values()):
                # Folding a node the workflow returns would leave the output with nothing
                # to resolve to. Leave it in place; a one-node graph is not worth a
                # special case in the IR.
                current = _mark_unfoldable(current, node.id)
                continue

            elided[node.id] = f"constfold: evaluated at compile time to {value!r}"
            transformations.append(
                Transformation(
                    pass_name=self.name,
                    nodes_before=[node.id],
                    nodes_after=[nid for nid, _ in consumers],
                    description=(
                        f"{node.id} ({node.target}) had only literal arguments; evaluated at compile time to "
                        f"{_clip(value)} and folded into {len(consumers)} consumer(s)"
                    ),
                )
            )
            current = current.copy_with(nodes=nodes, regions=current.regions)

        if not transformations:
            return graph, []
        return current.copy_with(meta={"elided": elided}), transformations


def _mark_unfoldable(graph: Graph, node_id: str) -> Graph:
    nodes = []
    for n in graph.nodes:
        copy = n.model_copy(deep=True)
        if copy.id == node_id:
            copy.meta = {**copy.meta, "constfold_declined": True}
        nodes.append(copy)
    return graph.copy_with(nodes=nodes)


def _clip(value: object, n: int = 60) -> str:
    text = repr(value)
    return text if len(text) <= n else text[: n - 1] + "…"


register(ConstFold())
