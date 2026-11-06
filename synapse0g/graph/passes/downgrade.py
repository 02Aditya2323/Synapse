"""`downgrade` -- cheaper model selection.

Moves a model call to a cheaper registry model. docs/PASSES.md section 5.
For each model call, this pass proposes a cheaper model and keeps the change only if the task still passes.

**The precondition is empirical, and this pass says so out loud.** There is no static
argument that a smaller model can do a particular job. The precondition is "re-running
the workflow on the corpus with only this node downgraded still passes the equivalence
relation", and that is checked by execution, in `correctness/`, not here. What this file
does is propose the candidate, predict the measured price difference, and record that
the claim is unverified until the firewall says otherwise.

That honesty is the point of including it. Every other pass can defend itself from the
graph; this one cannot, and pretending it could would make the other ten less
believable.

**What it predicts.** The measured price difference x the measured tokens. Returns
`d_cost_usd=None` when either model lacks a price in `models.yaml`, rather than guessing
(CLAUDE.md rule 5).

**How it goes wrong.** Nodes downgraded together without being tested together: two
individually safe downgrades can interact. So the pass proposes **one node at a time**,
in order of how much that node costs, and the result note says the combination needs its
own confirmation.
"""

from __future__ import annotations

from synapse0g.graph.core.effects import Finding
from synapse0g.graph.core.sir import Graph, Node, Profile
from synapse0g.graph.passes.base import Estimate, Transformation, register


def _registry():
    from synapse0g.graph.runtime.providers import load_registry

    return load_registry()


def target_model(graph: Graph) -> str | None:
    """The cheaper model to try, from `defaults.downgrade_target` in models.yaml.

    Only models.yaml names a model (CLAUDE.md rule 8), including this one.
    """
    override = graph.meta.get("downgrade_target")
    if override:
        return str(override)
    try:
        return _registry().defaults.get("downgrade_target")
    except Exception:  # noqa: BLE001 - no registry here: nothing to propose
        return None


def _price(model_id: str) -> dict | None:
    try:
        return _registry().models[model_id].price_per_mtok
    except Exception:  # noqa: BLE001
        return None


def _provider(model_id: str) -> str | None:
    try:
        return _registry().models[model_id].provider
    except Exception:  # noqa: BLE001 - not in the registry: nothing to compare
        return None


def _crosses_provider(graph: Graph, node: Node, cheap: str) -> bool:
    """A cheaper price says nothing about a different server. The registry default is one
    model for every graph, so a graph on a remote provider would land on a slow local model
    (measured: code_review on Groq went 1.46 s -> 4.34 s and 11 -> 6 passed). An override
    set on the graph is a deliberate choice, and the firewall still judges it."""
    if graph.meta.get("downgrade_target"):
        return False
    here, there = _provider(node.target), _provider(cheap)
    return here is not None and there is not None and here != there


def _is_model_node(node: Node) -> bool:
    return node.kind == "model" and node.target != "workflow_input"


def candidates(graph: Graph) -> list[Node]:
    """Model nodes not already on the cheap model, in program order.

    Program order, not cost order, and deliberately so. `apply()` receives no profile --
    the `Pass` protocol gives it only a graph -- so if `predict()` ranked candidates by
    measured cost the two methods could disagree about which node the estimate was
    about. One node is proposed per application, the application marks it, and a greedy
    manager that runs the pass repeatedly works down the list with a firewall verdict
    between each step. This keeps each node individually tested.
    """
    cheap = target_model(graph)
    if cheap is None:
        return []
    return [n for n in graph.nodes if _is_model_node(n) and n.target != cheap and not n.meta.get("downgraded")
            and not _crosses_provider(graph, n, cheap)]


class Downgrade:
    name = "downgrade"
    description = "Move a model call to a cheaper registry model."
    precondition = (
        "EMPIRICAL: re-running the corpus with only this node downgraded still passes the equivalence "
        "relation. Checked by execution in correctness/, never by analysis."
    )

    def preconditions(self, graph: Graph, profile: Profile) -> list[Finding]:
        """One finding per proposal, saying the claim is not yet proven.

        Unusually for this catalogue, these findings do not describe a refusal: they
        describe a transformation whose justification is pending. The dashboard shows
        them so nobody reads a predicted cost saving as a verified one.
        """
        cheap = target_model(graph)
        if cheap is None:
            return [
                Finding(
                    node="",
                    precondition="models.yaml names defaults.downgrade_target",
                    message=(
                        "no cheaper model is configured in models.yaml (defaults.downgrade_target), so there is "
                        "nothing to propose."
                    ),
                )
            ]
        findings = [
            Finding(
                node=node.id,
                precondition="same provider as defaults.downgrade_target",
                message=(
                    f"{node.target} runs on {_provider(node.target)} and {cheap} on {_provider(cheap)}: a different "
                    f"server can be far slower, so the default target is not proposed here."
                ),
            )
            for node in graph.nodes
            if _is_model_node(node) and _crosses_provider(graph, node, cheap)
        ]
        for node in candidates(graph):
            findings.append(
                Finding(
                    node=node.id,
                    precondition="equivalence run on the corpus",
                    message=(
                        f"moving {node.id} from {node.target} to {cheap} is a proposal, not a proof: a smaller "
                        f"model's competence cannot be read off the graph. The firewall's verdict on the test "
                        f"split decides whether it ships."
                    ),
                )
            )
            if _price(node.target) is None or _price(cheap) is None:
                findings.append(
                    Finding(
                        node=node.id,
                        precondition="both models priced in models.yaml",
                        message=(
                            f"{node.target} or {cheap} has no price_per_mtok, so the saving is reported in tokens "
                            f"only and the cost delta stays unknown rather than estimated."
                        ),
                    )
                )
        return findings

    def predict(self, graph: Graph, profile: Profile) -> Estimate:
        cheap = target_model(graph)
        targets = candidates(graph)
        if cheap is None or not targets:
            return Estimate(note="no model call here can move to a cheaper model")

        chosen = targets[0]
        p = profile.node(chosen.id)
        expensive_price, cheap_price = _price(chosen.target), _price(cheap)

        d_cost: float | None = None
        if expensive_price and cheap_price and p and p.tokens_in_mean is not None and p.tokens_out_mean is not None:
            d_cost = -(
                p.tokens_in_mean * (expensive_price["in"] - cheap_price["in"])
                + p.tokens_out_mean * (expensive_price["out"] - cheap_price["out"])
            ) / 1e6

        return Estimate(
            # Smaller models are frequently faster, but by how much is a measurement, so
            # the latency claim is left to the comparison run.
            d_latency_ms=0.0,
            d_tokens=0,  # the same prompt, a different model
            d_cost_usd=d_cost,
            # The highest risk in the catalogue, and correctly so: this is the one pass
            # whose precondition cannot be checked without running the corpus.
            risk=0.35,
            confidence=0.0 if p is None or p.samples == 0 else min(1.0, p.samples / 10.0),
            note=(
                f"proposes {chosen.id}: {chosen.target} -> {cheap}, one node at a time. Each node is tested "
                f"alone; downgrading several at once needs one more confirming run."
            ),
        )

    def apply(self, graph: Graph) -> tuple[Graph, list[Transformation]]:
        cheap = target_model(graph)
        targets = candidates(graph)
        if cheap is None or not targets:
            return graph, []

        chosen = targets[0]
        nodes = []
        for node in graph.nodes:
            copy = node.model_copy(deep=True)
            if copy.id == chosen.id:
                was = copy.target
                copy.target = cheap
                copy.determinism = _determinism_of(cheap, copy.determinism)
                copy.meta = {**copy.meta, "downgraded": {"from": was, "to": cheap}}
            nodes.append(copy)

        transformation = Transformation(
            pass_name=self.name,
            nodes_before=[chosen.id],
            nodes_after=[chosen.id],
            description=(
                f"{chosen.id} moved from {chosen.target} to {cheap}; tested alone, and unverified until the "
                f"firewall approves it on the test split"
            ),
        )
        return graph.copy_with(nodes=nodes), [transformation]


def _determinism_of(model_id: str, fallback: str) -> str:
    """The cheaper model's own determinism, not the old one's.

    Downgrading to an unseeded model would silently make a CSE-able node stochastic, and
    a later pass would then be reasoning about the wrong thing.
    """
    try:
        return _registry().models[model_id].determinism
    except Exception:  # noqa: BLE001
        return fallback


register(Downgrade())
