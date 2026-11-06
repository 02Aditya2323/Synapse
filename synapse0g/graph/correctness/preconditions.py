"""Independently callable precondition checks and post-transform safety audit.

Delegates all effect-safety predicates to synapse.core.effects (Aryan's lattice),
so passes ask the same question in the same way rather than reimplementing it.
"""

from __future__ import annotations

from itertools import combinations

from synapse0g.graph.core.effects import (
    Finding,
    check_preserved,
    is_side_effecting,
    may_batch,
    may_eliminate,
    may_reuse,
    may_run_concurrently,
    may_speculate,
)
from synapse0g.graph.core.sir import Effect, Graph


def check_request(graph: Graph, pass_name: str, node_ids: list[str], *, ages_s=None) -> list[Finding]:
    """Evaluate static preconditions for `pass_name` on the listed nodes.

    Returns a list of Findings; an empty list means all static checks pass.
    This is called independently by the UI's 'why was this not optimised?' panel.
    """
    by_id = {n.id: n for n in graph.nodes}
    out: list[Finding] = []

    for nid in node_ids:
        if nid not in by_id:
            out.append(
                Finding(
                    node=nid,
                    precondition="node_exists",
                    message="Transformation references an absent node",
                    pass_name=pass_name,
                )
            )
            continue
        n = by_id[nid]

        if pass_name in {"cse", "memoize"}:
            # may_reuse covers: irreversible, external, write, read-without-staleness,
            # stochastic-without-seed cases — all the right reasons.
            f = may_reuse(n, transform=pass_name)
            if f:
                out.append(f.tagged(pass_name))
            elif pass_name == "memoize":
                age = (ages_s or {}).get(nid, 0)
                if n.staleness_s is not None and age > n.staleness_s:
                    out.append(Finding(
                        node=nid,
                        precondition="determinism_and_freshness",
                        message=f"Cached result is {age:.1f}s old; staleness window is {n.staleness_s}s",
                        pass_name=pass_name,
                    ))

        elif pass_name == "dce":
            f = may_eliminate(n)
            if f:
                out.append(f.tagged(pass_name))

        elif pass_name == "batch":
            f = may_batch(n)
            if f:
                out.append(f.tagged(pass_name))

        elif pass_name == "speculate":
            f = may_speculate(n)
            if f:
                out.append(f.tagged(pass_name))

        elif pass_name == "constfold":
            if n.kind != "transform" or n.effect != Effect.PURE or n.determinism != "deterministic":
                out.append(Finding(
                    node=nid,
                    precondition="constant_pure_transform",
                    message="Constant folding needs a deterministic pure transform",
                    pass_name=pass_name,
                ))

    if pass_name in {"parallelize", "batch"}:
        valid = [n for n in node_ids if n in by_id]
        for a, b in combinations(valid, 2):
            f = may_run_concurrently(by_id[a], by_id[b])
            if f:
                out.append(f.tagged(pass_name))
            # Data dependency check via topo order
            try:
                graph.topo_order()
                preds_b = graph.predecessors(b)
                preds_a = graph.predecessors(a)
                if a in preds_b or b in preds_a:
                    out.append(Finding(
                        node=b,
                        precondition="independent",
                        message=f"{a} and {b} have a data/effect ordering dependency",
                        pass_name=pass_name,
                    ))
            except Exception:  # noqa: BLE001
                pass

    return out


def evaluate(pass_, graph, profile) -> list[Finding]:
    """Run preconditions on deep copies so a precondition cannot mutate the candidate."""
    return pass_.preconditions(graph.model_copy(deep=True), profile.model_copy(deep=True))


def audit_transform(before: Graph, after: Graph, pass_name: str, transformations) -> list[Finding]:
    """Post-transform structural audit — the universal invariant every pass must satisfy.

    Uses check_preserved() from core.effects (the same check Aryan's passes use)
    plus Aditya-specific checks for state-write removal and early_exit in strict mode.
    """
    out: list[Finding] = []

    # Validate the candidate graph structure first
    try:
        after.validate_graph()
    except Exception as exc:  # noqa: BLE001
        out.append(Finding(node="*", precondition="valid_graph", message=str(exc), pass_name=pass_name))
        return out

    # early_exit is always blocked in strict mode
    if pass_name == "early_exit":
        out.append(Finding(
            node="*",
            precondition="strict_mode",
            message="Accuracy-trading early exit is not permitted by the strict firewall",
            pass_name=pass_name,
        ))

    # Universal invariant: side-effect order preserved, nothing duplicated or removed
    for f in check_preserved(before, after):
        out.append(f.tagged(pass_name))

    b, a = {n.id: n for n in before.nodes}, {n.id: n for n in after.nodes}

    # State writes cannot be silently removed without a liveness proof
    for nid in b.keys() - a.keys():
        if b[nid].effect == Effect.WRITE:
            out.append(Finding(
                node=nid,
                precondition="preserve_state_writes",
                message="State writes cannot be removed without a liveness proof",
                pass_name=pass_name,
            ))

    # Passes cannot introduce new state writes or external actions
    for nid in a.keys() - b.keys():
        if is_side_effecting(a[nid]) or a[nid].effect == Effect.WRITE:
            out.append(Finding(
                node=nid,
                precondition="no_new_effects",
                message="An optimizer cannot introduce state writes or external actions",
                pass_name=pass_name,
            ))

    # Declared effects cannot be silently changed
    for nid in b.keys() & a.keys():
        if b[nid].effect != a[nid].effect or b[nid].reads != a[nid].reads or b[nid].writes != a[nid].writes:
            out.append(Finding(
                node=nid,
                precondition="effects_unchanged",
                message="An optimizer cannot erase or change declared effects",
                pass_name=pass_name,
            ))
        # If memoize flag was added, run static reuse checks
        if a[nid].meta.get("memoize") and not b[nid].meta.get("memoize"):
            out.extend(check_request(before, "memoize", [nid]))
        # Protected nodes cannot change their target
        if (b[nid].effect in {Effect.EXTERNAL, Effect.IRREVERSIBLE}
                and b[nid].target != a[nid].target):
            out.append(Finding(
                node=nid,
                precondition="protected_unchanged",
                message=f"Protected node {nid!r} target changed from {b[nid].target!r} to {a[nid].target!r}",
                pass_name=pass_name,
            ))

    # Parallel groups declared in meta must satisfy independence
    for group in after.meta.get("parallel_groups", []):
        out.extend(check_request(after, "parallelize", group))

    # State writes to the same key cannot be reordered (WAW hazard)
    before_pos = {n.id: i for i, n in enumerate(before.nodes)}
    after_pos = {n.id: i for i, n in enumerate(after.nodes)}
    before_writers: dict[str, list[str]] = {}
    for n in before.nodes:
        if n.effect == Effect.WRITE:
            for key in n.writes:
                before_writers.setdefault(key, []).append(n.id)
    for key, writers in before_writers.items():
        surviving = [nid for nid in writers if nid in after_pos]
        if len(surviving) > 1:
            original_order = sorted(surviving, key=lambda nid: before_pos[nid])
            candidate_order = sorted(surviving, key=lambda nid: after_pos[nid])
            if candidate_order != original_order:
                out.append(Finding(
                    node=surviving[0],
                    precondition="write_order_preserved",
                    message=f"Writes to state key {key!r} were reordered; WAW hazard",
                    pass_name=pass_name,
                ))

    return out
