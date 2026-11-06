"""The effect lattice and the safety predicates: what a pass may legally do.

docs/CORRECTNESS.md: **effects are law.** No pass may reorder, re-run, cache, speculate,
batch or elide a node whose effect is `external` or `irreversible`. Ever. Not behind a
flag, not in a benchmark, not "just for the demo".

This module owns those predicates so passes **ask** rather than reimplement the check.
Eleven passes each writing `if node.effect in ("external", "irreversible")` is eleven
chances to get it wrong, and the failure mode is an agent re-sending an email or
re-placing an order. This safety rule matters because a replay must never re-execute a
`place_order` call; the `irreversible` tier is that lesson promoted into the type
system.

The permission table is docs/IR_SPEC.md section 4.1, reproduced here as code. Each
predicate returns either `None` (permitted) or a `Finding` saying which rule failed and
why, in a sentence the dashboard can render -- being able to explain a refusal is a
large part of why this looks like a compiler rather than a bag of heuristics.
"""

from __future__ import annotations

from pydantic import BaseModel

from synapse0g.graph.core.sir import Effect, Graph, Node

# Most permissive first. `RANK` lets a pass say "effect <= read" and mean it.
ORDER: tuple[Effect, ...] = (Effect.PURE, Effect.READ, Effect.WRITE, Effect.EXTERNAL, Effect.IRREVERSIBLE)
RANK: dict[str, int] = {e.value: i for i, e in enumerate(ORDER)}


class Finding(BaseModel):
    """Why a transformation is not legal here.

    Findings are a feature, not an error state (docs/PASSES.md section 0). The
    dashboard's "why was this not optimized?" panel renders them directly, and each one
    highlights its node on the graph.
    """

    node: str
    precondition: str  # the rule that failed, e.g. "effect<=read"
    message: str  # a human sentence for the dashboard
    pass_name: str | None = None  # filled in by the pass that produced it

    def tagged(self, pass_name: str) -> Finding:
        return self.model_copy(update={"pass_name": pass_name})


def rank(effect: Effect | str) -> int:
    return RANK[str(effect)]


def at_most(effect: Effect | str, ceiling: Effect | str) -> bool:
    """True when `effect` is no more restrictive than `ceiling`. `effect <= ceiling`."""
    return rank(effect) <= rank(ceiling)


def join(a: Effect | str, b: Effect | str) -> Effect:
    """The least permissive of two effects: the effect of doing both."""
    return ORDER[max(rank(a), rank(b))]


# --------------------------------------------------------------- the three tiers


def is_side_effecting(node: Node) -> bool:
    """Touches the world outside the workflow. Nothing may be re-run, re-ordered,
    cached, speculated, batched or elided here."""
    return node.effect in (Effect.EXTERNAL, Effect.IRREVERSIBLE)


def is_irreversible(node: Node) -> bool:
    """Money-moving or destructive. As `external`, and additionally never executed at
    all during optimization search, equivalence runs or benchmarks."""
    return node.effect == Effect.IRREVERSIBLE


def is_observable(node: Node) -> bool:
    """Has an effect someone outside this process could notice."""
    return is_side_effecting(node) or node.effect == Effect.WRITE


# ------------------------------------------------- per-transformation predicates
# Each returns None when the transformation is legal on this node, else a Finding.


def may_reuse(node: Node, *, transform: str = "reuse") -> Finding | None:
    """CSE and memoization: may this node's result stand in for another execution of it?

    `pure` yes. `read` yes, but only inside `staleness_s` -- and `staleness_s` must be
    set, because "how stale may this observation be?" is a decision the workflow author
    has to make, not one we may default (docs/PASSES.md section 2).
    """
    if is_irreversible(node):
        return Finding(
            node=node.id,
            precondition="effect<=read",
            message=f"{node.target} is irreversible (it moves money or destroys data); "
            f"it is never cached, re-ordered or re-run.",
        )
    if node.effect == Effect.EXTERNAL:
        return Finding(
            node=node.id,
            precondition="effect<=read",
            message=f"{node.target} changes the world outside the workflow; re-using a previous result "
            f"would skip that effect, so it is never {transform}d.",
        )
    if node.effect == Effect.WRITE:
        return Finding(
            node=node.id,
            precondition="effect<=read",
            message=f"{node.target} mutates workflow state ({', '.join(node.writes) or 'unspecified keys'}); "
            f"re-using a result would skip the write.",
        )
    if node.effect == Effect.READ and node.staleness_s is None:
        return Finding(
            node=node.id,
            precondition="read requires staleness_s",
            message=f"{node.target} observes outside state but declares no staleness_s, so there is no "
            f"window in which a previous observation is known to still hold.",
        )
    if node.determinism == "stochastic":
        return Finding(
            node=node.id,
            precondition="determinism!=stochastic",
            message=f"{node.target} is an unseeded stochastic call; collapsing two samples into one would "
            f"change the output distribution even though neither call touched the world.",
        )
    return None


def may_reorder(node: Node) -> Finding | None:
    """Parallel scheduling and reordering. `write` is handled separately, by key."""
    if is_side_effecting(node):
        return Finding(
            node=node.id,
            precondition="effect<=write",
            message=f"{node.target} has an observable side effect; its position relative to other "
            f"side-effecting nodes is fixed and never reordered.",
        )
    return None


def may_run_concurrently(a: Node, b: Node) -> Finding | None:
    """Two nodes side by side. `write` nodes may overlap only on disjoint key sets."""
    for n in (a, b):
        if finding := may_reorder(n):
            return finding
    if a.effect == Effect.WRITE and b.effect == Effect.WRITE:
        shared = set(a.writes) & set(b.writes)
        if shared:
            return Finding(
                node=b.id,
                precondition="disjoint writes",
                message=f"{a.id} and {b.id} both write {sorted(shared)[0]!r}; running them concurrently "
                f"would make the final value depend on timing.",
            )
    if a.effect == Effect.WRITE and set(a.writes) & set(b.reads):
        return Finding(
            node=b.id,
            precondition="disjoint writes",
            message=f"{b.id} reads {sorted(set(a.writes) & set(b.reads))[0]!r} while {a.id} writes it.",
        )
    if b.effect == Effect.WRITE and set(b.writes) & set(a.reads):
        return Finding(
            node=a.id,
            precondition="disjoint writes",
            message=f"{a.id} reads {sorted(set(b.writes) & set(a.reads))[0]!r} while {b.id} writes it.",
        )
    return None


def may_speculate(node: Node) -> Finding | None:
    """Starting a node before it is known to be needed. Only `pure` and `read`:
    the discard path has to be free, and it is not free if anything happened."""
    if not at_most(node.effect, Effect.READ):
        return Finding(
            node=node.id,
            precondition="effect<=read",
            message=f"{node.target} has effect {node.effect}; if the speculation turns out not to be needed "
            f"the effect cannot be taken back, so it is never started early.",
        )
    return None


def may_batch(node: Node) -> Finding | None:
    """Fusing several calls into one provider round-trip."""
    if not at_most(node.effect, Effect.READ):
        return Finding(
            node=node.id,
            precondition="effect<=read",
            message=f"{node.target} has effect {node.effect}; fusing side-effecting calls would change "
            f"how many times the effect happens.",
        )
    return None


def may_eliminate(node: Node) -> Finding | None:
    """Dead node elimination. A node that sends an email is never dead, however unused
    its return value is (docs/PASSES.md section 7)."""
    if is_side_effecting(node):
        return Finding(
            node=node.id,
            precondition="effect<=write",
            message=f"{node.target} is {node.effect}: its value is unused but its effect is the point, "
            f"so it is never removed as dead.",
        )
    return None


def may_execute_during_search(node: Node) -> Finding | None:
    """Optimization search and equivalence runs re-execute workflows many times.
    `external` nodes are stubbed there; `irreversible` nodes are never run at all."""
    if is_irreversible(node):
        return Finding(
            node=node.id,
            precondition="effect!=irreversible",
            message=f"{node.target} is irreversible; Synapse never executes it during optimization search, "
            f"equivalence runs or benchmarks, regardless of what the caller asked for.",
        )
    return None


# --------------------------------------------- the names profile/ and correctness/ use
# `profile/`, `correctness/` and `passes/manager.py` were written against these three
# names. They are kept here, on this module's semantics, so there is exactly one
# implementation of the lattice in the codebase (docs/CORRECTNESS.md). Writing them twice
# is how two parts of a compiler come to disagree about what is safe.

#: The tiers that touch the world. Nothing here is re-run, re-ordered, cached,
#: speculated, batched or elided.
PROTECTED = frozenset({Effect.EXTERNAL.value, Effect.IRREVERSIBLE.value})


def conflicts(a: Node, b: Node) -> bool:
    """Whether two nodes touch the same state key with at least one writer."""
    return bool(set(a.writes) & (set(b.reads) | set(b.writes)) or set(b.writes) & set(a.reads))


def dependencies(graph: Graph) -> dict[str, set[str]]:
    """node id -> everything that must complete first: data edges plus effect edges.

    One deliberate difference from a first pass at this, worth stating because it
    changes how much parallelism survives. A side-effecting node is **not** a barrier
    against everything that follows it -- only against other side-effecting nodes, plus
    whatever a state key genuinely orders. That is what docs/IR_SPEC.md section 3.2
    specifies: "every `external` or `irreversible` node gets ordering edges to all
    `external` and `irreversible` nodes before and after it".

    Treating a side effect as a full barrier is safe but costly: one `send_email` in the
    middle of a workflow would serialise every pure node after it, and `shop_ops` would
    show no parallelism anywhere rather than showing precisely which two nodes are
    pinned and why.
    """
    return {nid: graph.predecessors(nid) for nid in graph.node_ids}


def reusable(node: Node, *, age_s: float = 0.0) -> bool:
    """Whether a previous result of this node may stand in for running it again.

    The boolean form of `may_reuse`, for callers that only need the decision. Prefer
    `may_reuse` where the answer is shown to a person: it carries the reason, and a
    refusal nobody can explain is most of what makes this look like a bag of heuristics
    rather than a compiler.
    """
    if may_reuse(node) is not None:
        return False
    if node.effect == Effect.READ:
        return node.staleness_s is not None and 0 <= age_s <= node.staleness_s
    return True


# ------------------------------------------------------------- graph-level checks


def side_effect_order(graph: Graph) -> list[str]:
    """The side-effecting nodes in program order. Two graphs that disagree on this list
    are not equivalent, whatever else is true of them."""
    return [n.id for n in graph.nodes if is_side_effecting(n)]


def check_preserved(before: Graph, after: Graph) -> list[Finding]:
    """The universal invariant every pass must satisfy, re-checked independently of what
    the pass claims (docs/PASSES.md section 0).

    `apply()` must never change the relative order of two `external` or `irreversible`
    nodes, and must never remove, duplicate or cache one. Defence in depth: a buggy pass
    should produce a slow run, never an unsafe one. The pass manager runs this after
    every pass and the scheduler enforces the same constraints again at execution time.
    """
    findings: list[Finding] = []
    before_order = side_effect_order(before)
    after_order = side_effect_order(after)

    removed = [nid for nid in before_order if nid not in after_order]
    for nid in removed:
        node = before.node(nid)
        findings.append(
            Finding(
                node=nid,
                precondition="side effects preserved",
                message=f"the pass removed {node.target} ({node.effect}), which has an observable effect. "
                f"Side-effecting nodes are never elided.",
            )
        )

    counts: dict[str, int] = {}
    for nid in after_order:
        counts[nid] = counts.get(nid, 0) + 1
    for nid, n in counts.items():
        if n > 1:
            findings.append(
                Finding(
                    node=nid,
                    precondition="side effects preserved",
                    message=f"the pass left {n} copies of side-effecting node {nid}; its effect would happen "
                    f"{n} times instead of once.",
                )
            )

    kept = [nid for nid in before_order if nid in after_order]
    reordered = [nid for nid in after_order if nid in kept]
    if reordered != kept:
        findings.append(
            Finding(
                node=reordered[0] if reordered else "",
                precondition="side effects preserved",
                message=f"the pass changed the order of side-effecting nodes: {kept} became {reordered}. "
                f"Observable effects keep their relative order, always.",
            )
        )

    for nid in after_order:
        if not after.has(nid):
            continue
        node = after.node(nid)
        if node.meta.get("memoized") or node.meta.get("cached") or node.meta.get("speculated"):
            findings.append(
                Finding(
                    node=nid,
                    precondition="side effects preserved",
                    message=f"the pass marked side-effecting node {nid} as cached or speculated.",
                )
            )

    return findings
