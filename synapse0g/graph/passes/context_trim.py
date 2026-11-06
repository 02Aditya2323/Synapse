"""`context_trim`: send a model call less context, and let the firewall decide if that is safe.

`context_prune` only drops arguments a prompt never renders, and those tokens were never sent, so
it saves nothing. This pass cuts what is really sent: each non-system message is clipped to a
limit. That can change answers, so nothing static can prove it safe. Like `downgrade`, its
precondition is empirical, and it is off by default. It ships only with a firewall verdict.

The limit is `graph.meta["trim_chars"]`, or DEFAULT_LIMIT. The runtime applies the clip, and the
limit is part of the node's content hash, so a trimmed call never shares a cache entry with a full
one.
"""

from __future__ import annotations

from synapse0g.graph.core.effects import Finding
from synapse0g.graph.core.sir import Graph, Node, Profile
from synapse0g.graph.passes.base import Estimate, Transformation, confidence_from, register

DEFAULT_LIMIT = 1200
MARKER = " [...trimmed by Synapse]"


def clip_messages(messages: list[dict], limit: int) -> list[dict]:
    """Keep the system prompt whole and the start of every other message."""
    out = []
    for message in messages:
        content = message.get("content")
        if message.get("role") != "system" and isinstance(content, str) and len(content) > limit:
            message = {**message, "content": content[:limit] + MARKER}
        out.append(message)
    return out


def _limit(graph: Graph) -> int:
    return int(graph.meta.get("trim_chars", DEFAULT_LIMIT))


def candidates(graph: Graph) -> list[Node]:
    return [
        n for n in graph.nodes if n.kind == "model" and n.determinism != "stochastic" and "trim_chars" not in n.meta
    ]


class ContextTrim:
    name = "context_trim"
    description = "Clip the context sent to a model call, and keep the change only if the firewall approves."
    precondition = (
        "EMPIRICAL: the corpus still passes the equivalence relation with the clipped prompts. Checked by "
        "execution in correctness/, never by analysis. Never on an unseeded stochastic call."
    )

    def preconditions(self, graph: Graph, profile: Profile) -> list[Finding]:
        return [
            Finding(
                node=n.id,
                precondition="determinism!=stochastic",
                message=f"{n.target} is an unseeded stochastic call, so a clipped run could not be compared fairly.",
            )
            for n in graph.nodes
            if n.kind == "model" and n.determinism == "stochastic"
        ]

    def predict(self, graph: Graph, profile: Profile) -> Estimate:
        targets = candidates(graph)
        if not targets:
            return Estimate(note="no model call here can be clipped")
        return Estimate(
            risk=0.5,
            confidence=confidence_from(profile, [n.id for n in targets]),
            note=f"would clip {len(targets)} model call(s) to {_limit(graph)} characters per message. The saving "
            "depends on prompt size, so it is measured rather than predicted.",
        )

    def apply(self, graph: Graph) -> tuple[Graph, list[Transformation]]:
        targets = {n.id for n in candidates(graph)}
        if not targets:
            return graph, []
        limit = _limit(graph)
        nodes = []
        for node in graph.nodes:
            copy = node.model_copy(deep=True)
            if copy.id in targets:
                copy.meta = {**copy.meta, "trim_chars": limit}
            nodes.append(copy)
        return graph.copy_with(nodes=nodes), [
            Transformation(
                pass_name=self.name,
                nodes_before=sorted(targets),
                nodes_after=sorted(targets),
                description=f"{len(targets)} model call(s) now send at most {limit} characters per message. "
                "Unverified until the firewall approves it.",
            )
        ]


register(ContextTrim())
