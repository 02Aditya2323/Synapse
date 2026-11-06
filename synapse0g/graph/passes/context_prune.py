"""`context_prune` -- drop context a model call does not use.

Removes arguments from a model node that the node's prompt never actually references.
docs/PASSES.md section 4.

**Why this is the most under-appreciated pass in the catalogue.** Agent loops habitually
append every prior step to the next model call, so token count grows quadratically in
step count. In our recorded corpus a step at index 8 consumed the outputs of steps 0, 1,
2, 4, 5, 6 and 7. Most of that was never referenced. This is
a measured fact from a real trace, not a constructed example, and it is the slide where
the cost axis moves.

**How "unreferenced" is decided.** The node's implementation is rendered with sentinel
values in place of each argument, and the pass checks which sentinels appear in the
resulting prompt. An argument whose sentinel never reaches the prompt cannot affect the
model's input, so dropping it cannot change the output. That is a check against the
actual prompt builder rather than against a guess about it -- but it is still only as
good as the prompt being a function of its arguments, which is why:

**This pass carries a higher `risk` than `cse`, deliberately.** A model can rely on
context the static analysis cannot see -- few-shot conditioning rather than explicit
reference. docs/PASSES.md says this pass is validated on the corpus and not trusted from
analysis alone, so its estimate reports a real risk and the firewall is what approves
it. Planted case U7 ("prune a context entry the model actually relies on") is expected
to be caught by **layer 2**, not by this file.

**Never prunes a stochastic node's context**: changing the prompt of an unseeded call
changes its sampling, so the difference would be unattributable.
"""

from __future__ import annotations

from synapse0g.graph.core.effects import Finding
from synapse0g.graph.core.sir import Graph, Literal_, Node, Profile, Ref
from synapse0g.graph.passes.base import Estimate, Transformation, confidence_from, register

SENTINEL = "__synapse_probe_{name}__"

#: Below this, pruning is not worth the risk: the firewall still has to approve it, and
#: spending a corpus run to save forty tokens is a poor trade.
MIN_TOKENS_TO_BOTHER = 50


def _implementation(node: Node):
    from synapse0g.graph.frontend import dsl

    spec = dsl.spec_for(node.target)
    return None if spec is None else spec.impl


def _rendered_prompt(node: Node) -> str | None:
    """The prompt this node builds, with each argument replaced by a unique sentinel.

    Returns `None` when the prompt cannot be built -- no implementation, or it raised.
    A pass that cannot see the prompt declines rather than guesses.
    """
    impl = _implementation(node)
    if impl is None:
        return None
    probes = {name: SENTINEL.format(name=name) for name in node.args}
    try:
        rendered = impl(**probes)
    except Exception:  # noqa: BLE001 - an implementation that needs real types declines the pass
        return None
    return repr(rendered)


def unreferenced_args(node: Node) -> list[str] | None:
    """Arguments whose value never reaches the prompt. `None` means "cannot tell"."""
    rendered = _rendered_prompt(node)
    if rendered is None:
        return None
    return [name for name in node.args if SENTINEL.format(name=name) not in rendered]


def _prunable(node: Node) -> bool:
    return (
        node.kind == "model"
        and node.determinism != "stochastic"
        and len(node.args) > 1  # a single-argument call with nothing referenced is a bug, not an opportunity
    )


def opportunities(graph: Graph) -> dict[str, list[str]]:
    """node id -> the arguments it never reads."""
    out: dict[str, list[str]] = {}
    for node in graph.nodes:
        if not _prunable(node):
            continue
        unused = unreferenced_args(node)
        if unused and len(unused) < len(node.args):
            out[node.id] = unused
    return out


class ContextPrune:
    name = "context_prune"
    description = "Drop arguments a model call's prompt never references."
    precondition = (
        "The dropped argument is not reachable in the data flow from this node's prompt template, and "
        "the node is not stochastic. Verified against the corpus before it ships."
    )

    def preconditions(self, graph: Graph, profile: Profile) -> list[Finding]:
        findings: list[Finding] = []
        for node in graph.nodes:
            if node.kind != "model" or len(node.args) <= 1:
                continue
            if node.determinism == "stochastic":
                findings.append(
                    Finding(
                        node=node.id,
                        precondition="determinism!=stochastic",
                        message=(
                            f"{node.target} is an unseeded stochastic call; changing its prompt changes its "
                            f"sampling, so a pruned run could not be compared with the original."
                        ),
                    )
                )
            elif _rendered_prompt(node) is None:
                findings.append(
                    Finding(
                        node=node.id,
                        precondition="prompt is inspectable",
                        message=(
                            f"{node.target}'s prompt could not be rendered with probe values in this process, "
                            f"so which arguments it reads is unknown and nothing is pruned."
                        ),
                    )
                )
        return findings

    def predict(self, graph: Graph, profile: Profile) -> Estimate:
        found = opportunities(graph)
        if not found:
            return Estimate(note="every model call in this graph reads all of its arguments")

        ids = list(found)
        saved_tokens = 0
        saved_cost: float | None = 0.0
        for nid, unused in found.items():
            p = profile.node(nid)
            if p is None or p.tokens_in_mean is None:
                continue
            # The share of the prompt the dropped arguments account for, approximated by
            # their share of the argument count. Approximate, and said to be: the real
            # number is measured by the comparison run, which is what gets reported.
            share = len(unused) / max(1, len(graph.node(nid).args))
            saved_tokens += int(p.tokens_in_mean * share)
            if p.cost_usd_mean is None:
                saved_cost = None
            elif saved_cost is not None:
                saved_cost += p.cost_usd_mean * share

        return Estimate(
            # Token-bound calls get faster too, but how much depends on the provider, so
            # the latency claim is left to the measurement rather than predicted here.
            d_latency_ms=0.0,
            d_tokens=-saved_tokens,
            d_cost_usd=None if saved_cost is None else -saved_cost,
            # Higher than cse and memoize: a model can lean on context it never names.
            risk=0.25,
            confidence=confidence_from(profile, ids),
            note=(
                f"{sum(len(v) for v in found.values())} unreferenced argument(s) across {len(ids)} model call(s); "
                f"the token saving is approximated from argument share and measured by the comparison run"
            ),
        )

    def apply(self, graph: Graph) -> tuple[Graph, list[Transformation]]:
        found = opportunities(graph)
        if not found:
            return graph, []

        nodes = []
        transformations: list[Transformation] = []
        for node in graph.nodes:
            copy = node.model_copy(deep=True)
            unused = found.get(copy.id)
            if unused:
                dropped = {name: copy.args[name] for name in unused}
                copy.args = {name: arg for name, arg in copy.args.items() if name not in unused}
                # The cache key follows the arguments: leaving a dropped argument in the
                # key would mean two now-identical calls never collapse.
                if copy.cache_key_fields is not None:
                    copy.cache_key_fields = [f for f in copy.cache_key_fields if f not in unused]
                copy.meta = {**copy.meta, "pruned_args": sorted(unused)}
                transformations.append(
                    Transformation(
                        pass_name=self.name,
                        nodes_before=[copy.id, *_producers(dropped)],
                        nodes_after=[copy.id],
                        description=(
                            f"dropped {len(unused)} unreferenced context entr{'y' if len(unused) == 1 else 'ies'} "
                            f"from the {copy.id} prompt ({', '.join(sorted(unused))}); the prompt never reads them"
                        ),
                    )
                )
            nodes.append(copy)

        return graph.copy_with(nodes=nodes), transformations


def _producers(dropped: dict[str, object]) -> list[str]:
    """Which nodes fed the dropped arguments -- so the UI can highlight them, and so
    `dce` can notice that some of them are now dead."""
    return sorted({arg.node for arg in dropped.values() if isinstance(arg, Ref)})


def _is_literal(arg: object) -> bool:
    return isinstance(arg, Literal_)


register(ContextPrune())
