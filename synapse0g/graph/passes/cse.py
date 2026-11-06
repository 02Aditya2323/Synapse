"""`cse` -- common subexpression elimination.

Finds nodes that compute the same thing and collapses them into one, rewriting every
`Ref` to the survivor. docs/PASSES.md section 1.

**How two nodes are known to agree without running them.** At compile time there are no
values, so the key is *structural*: same kind, same target, same effect, and arguments
that are equal as structure -- literals equal by value, refs pointing at the same
(node, output). Because values are SSA and the nodes are deterministic or seeded, two
nodes with the same structural key must resolve to the same inputs, and therefore to the
same output. That is a fact about the graph, not a guess about the workload, which is
what makes this decidable rather than hopeful.

**Precondition.** Both nodes are `pure`, or both are `read` and their executions fall
inside `staleness_s`. Both must be `deterministic`, or `seeded` with the same seed --
collapsing two `stochastic` samples changes the output distribution even though neither
call touched the world.

**The real example.** A recorded trading-agent run in our corpus calls
`get_price("INFY")` twice with an identical input hash inside one execution
(docs/PORTING.md section 3). The fix is free and provable from the recording.

**How it goes wrong.** `cache_key_fields` set too narrow, so two genuinely different
calls key the same. This is the most dangerous bug in the system, because it produces a
plausible wrong answer rather than a crash. Three mitigations, none of them this file
alone: the default is every argument, narrowing is explicit in the DSL, and
`correctness/chaos.py` plants a narrowed key (U3) and asserts the firewall catches it.
Static analysis cannot see that bug -- only running both versions can.
"""

from __future__ import annotations

from synapse0g.graph.core import effects
from synapse0g.graph.core.effects import Finding
from synapse0g.graph.core.hashing import fingerprint
from synapse0g.graph.core.sir import Graph, Literal_, Node, Profile, Ref
from synapse0g.graph.passes.base import (
    Estimate,
    Transformation,
    confidence_from,
    latency_saved,
    register,
    sum_cost,
    tokens_of,
)

MAX_ROUNDS = 8  # collapsing one pair can expose another; this bounds the fixpoint


def structural_key(node: Node) -> str:
    """What this node computes, independent of which node it is.

    Two nodes with the same key resolve to the same inputs, so they produce the same
    output. `cache_key_fields` narrows it exactly as it narrows the runtime content
    hash, so a node that excludes a request id from its cache key also has that id
    excluded here -- the two must agree or CSE and the cache would disagree about what
    "the same call" means.
    """
    fields = node.cache_key_fields if node.cache_key_fields is not None else sorted(node.args)
    shape: dict[str, object] = {}
    for name in sorted(fields):
        arg = node.args.get(name)
        if isinstance(arg, Ref):
            shape[name] = {"ref": [arg.node, arg.output]}
        elif isinstance(arg, Literal_):
            shape[name] = {"literal": arg.value}
    return fingerprint(
        {
            "kind": node.kind,
            "target": node.target,
            # Must match `node_hash`: two nodes on the same model building different
            # prompts are not the same work, however alike their arguments look.
            "impl": node.meta.get("impl"),
            "trim": node.meta.get("trim_chars"),
            "effect": str(node.effect),
            "determinism": node.determinism,
            "outputs": node.outputs,
            "region": node.region,
            "args": shape,
        }
    )


def _collapsible(node: Node) -> Finding | None:
    """Whether this node may stand in for another execution of itself."""
    return effects.may_reuse(node, transform="collapse")


def _candidates(graph: Graph) -> dict[str, list[Node]]:
    """Groups of two or more nodes that compute the same thing and may be collapsed."""
    groups: dict[str, list[Node]] = {}
    for node in graph.nodes:
        if _collapsible(node) is not None:
            continue
        groups.setdefault(structural_key(node), []).append(node)
    return {key: nodes for key, nodes in groups.items() if len(nodes) > 1}


class CSE:
    name = "cse"
    description = "Collapse nodes that compute the same thing into one."
    precondition = (
        "Both nodes are pure, or both are read and within staleness_s; both deterministic or "
        "identically seeded. Never external or irreversible."
    )

    def preconditions(self, graph: Graph, profile: Profile) -> list[Finding]:
        """Name every duplicate this pass is *not* allowed to collapse.

        A duplicate that is merely unique gets no finding -- there is nothing to
        explain. A duplicate that exists and is refused does, because "n9 appears twice
        and Synapse left it alone" is a question the dashboard must be able to answer.
        """
        findings: list[Finding] = []
        by_shape: dict[str, list[Node]] = {}
        for node in graph.nodes:
            by_shape.setdefault(structural_key(node), []).append(node)
        for nodes in by_shape.values():
            if len(nodes) < 2:
                continue
            for node in nodes:
                if finding := _collapsible(node):
                    findings.append(finding)
        return findings

    def predict(self, graph: Graph, profile: Profile) -> Estimate:
        groups = _candidates(graph)
        if not groups:
            return Estimate(note="no two nodes in this graph compute the same thing")

        doomed = [n.id for nodes in groups.values() for n in nodes[1:]]
        saved_ms = sum(latency_saved(profile, nid) for nid in doomed)
        return Estimate(
            # Latency only materialises if the duplicate was on the critical path; a
            # duplicate with slack costs tokens but not time, and `latency_saved`
            # consults the measured slack rather than assuming.
            d_latency_ms=-saved_ms,
            d_tokens=-sum(tokens_of(profile, nid) for nid in doomed),
            d_cost_usd=_negate(sum_cost(profile, doomed)),
            risk=0.05,  # the lowest of any pass: the inputs are provably identical
            confidence=confidence_from(profile, doomed),
            note=f"{len(doomed)} duplicate node(s) across {len(groups)} group(s)",
        )

    def apply(self, graph: Graph) -> tuple[Graph, list[Transformation]]:
        transformations: list[Transformation] = []
        current = graph
        elided: dict[str, str] = dict(current.meta.get("elided") or {})

        for _ in range(MAX_ROUNDS):
            groups = _candidates(current)
            if not groups:
                break
            mapping: dict[str, str] = {}
            round_removed: list[tuple[Node, Node]] = []
            for nodes in groups.values():
                ordered = sorted(nodes, key=lambda n: current.index(n.id))
                survivor = ordered[0]
                for duplicate in ordered[1:]:
                    mapping[duplicate.id] = survivor.id
                    round_removed.append((duplicate, survivor))
            if not mapping:
                break
            current = current.rewrite_refs(mapping).without(mapping)
            for duplicate, survivor in round_removed:
                reason = f"cse: collapsed into {survivor.id}"
                elided[duplicate.id] = reason
                transformations.append(
                    Transformation(
                        pass_name=self.name,
                        nodes_before=[survivor.id, duplicate.id],
                        nodes_after=[survivor.id],
                        description=(
                            f"{duplicate.id} had the same content hash as {survivor.id} "
                            f"({duplicate.target}{_args_hint(duplicate)}); collapsed"
                        ),
                    )
                )

        if not transformations:
            return graph, []
        return current.copy_with(meta={"elided": elided}), transformations


def _args_hint(node: Node) -> str:
    """A short, readable reminder of which call this was, for the UI's hover text."""
    literals = [repr(a.value) for a in node.args.values() if isinstance(a, Literal_)]
    return f" {', '.join(literals[:2])}" if literals else ""


def _negate(value: float | None) -> float | None:
    return None if value is None else -value


register(CSE())
