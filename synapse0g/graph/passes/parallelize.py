"""`parallelize` -- concurrent scheduling.

Finds work with no path between it in the combined graph and lets it run at the same
time, up to a global and a per-provider limit. docs/PASSES.md section 3.

**This pass changes a schedule, not a shape.** It adds no nodes and removes none: it
records a concurrency limit and the groups that may overlap in `graph.meta`, and
`runtime/scheduler.py` is what actually runs them side by side. That split is
deliberate -- the scheduler re-derives the effect edges from the graph and refuses to
violate them whatever this pass wrote, so a bug here produces a schedule that is merely
no faster, never one that is unsafe.

**Precondition.** No data edge and no effect edge between the nodes. `write` nodes may
overlap only when their `writes` sets are disjoint. `external` and `irreversible` nodes
never participate at all.

**The win.** On `research_fanout` with *k* independent sub-queries the critical path
drops from roughly `2k + 2` node latencies to 4. That is the headline number, and it is
honest because the "before" graph has `max_concurrency` 1 -- the obvious implementation,
not a hobbled one.

**How it goes wrong.** The concurrency limit or a provider rate limit turns out to be
the real bottleneck, and the pass fires for nothing. A pass that fires and wins nothing
is worse than one that declines, because it adds risk for no return -- so the estimate
simulates the schedule under the limit and the pass declines when the simulation says
the width is 1.

**A server that changes answers under load.** Two model calls with no edge between them are
independent in the graph, but some servers answer differently when requests overlap, even at
temperature 0 with a seed (`correctness/audit.py`). No static check sees that. When the stored
audit for a model says so, this pass caps that model's provider at one call at a time, so its
calls never overlap, and says why on each of its nodes. Tools and other providers still overlap.

**Note for the pitch.** Every team will implement this. Implementing it *provably
safely*, by reading effect edges rather than assuming independence, is the difference.
"""

from __future__ import annotations

import math

from synapse0g.graph.core import effects
from synapse0g.graph.core.effects import Finding
from synapse0g.graph.core.sir import Graph, Node, Profile
from synapse0g.graph.passes.base import Estimate, Transformation, confidence_from, register


def serialized(graph: Graph) -> dict[str, dict]:
    """Model nodes whose server must not see overlapping calls: node id -> its stored audit."""
    from synapse0g.graph.correctness.audit import unstable

    out: dict[str, dict] = {}
    for node in graph.nodes:
        if node.kind == "model" and (record := unstable(graph, node.target)):
            out[node.id] = record
    return out


def _provider(node: Node, record: dict) -> str:
    if record.get("provider"):
        return str(record["provider"])
    from synapse0g.graph.runtime.executor import provider_of

    return provider_of(node)


def _audit_message(node: Node, record: dict) -> str:
    when = str(record.get("recorded_at", ""))[:10] or "an earlier run"
    if record.get("varied_one_at_a_time"):
        what = (f"changed answers on {record['varied_one_at_a_time']} of {record.get('prompts', '?')} prompts even "
                "one at a time")
    else:
        what = (f"changed answers on {record.get('varied_under_concurrency', '?')} of {record.get('prompts', '?')} "
                f"prompts when requests overlapped (concurrency {record.get('concurrency', '?')})")
    return (f"{node.target} {what}, in the audit of {when}. Its calls run one at a time, so this "
            "optimization cannot change its answers by overlapping them. Tools still overlap.")


def _schedulable(graph: Graph) -> list[Node]:
    return [n for n in graph.nodes if not effects.is_side_effecting(n) and n.target != "workflow_input"]


def independent_groups(graph: Graph) -> list[list[str]]:
    """Sets of nodes that may run at the same time.

    Two nodes may overlap when neither can reach the other through data *or* effect
    edges, and when the lattice permits them side by side (disjoint writes). The groups
    are the levels of the dependency graph: everything at one level is mutually
    unreachable by construction.
    """
    reachable = _transitive_closure(graph)
    levels: dict[str, int] = {}
    for nid in graph.topo_order():
        preds = graph.predecessors(nid)
        levels[nid] = 1 + max((levels[p] for p in preds if p in levels), default=-1)

    groups: list[list[str]] = []
    by_level: dict[int, list[str]] = {}
    for nid, level in levels.items():
        by_level.setdefault(level, []).append(nid)

    for level in sorted(by_level):
        members = [nid for nid in by_level[level] if any(n.id == nid for n in _schedulable(graph))]
        group: list[str] = []
        for nid in sorted(members, key=graph.index):
            if any(other in reachable[nid] or nid in reachable[other] for other in group):
                continue
            if any(effects.may_run_concurrently(graph.node(nid), graph.node(other)) for other in group):
                continue
            group.append(nid)
        if len(group) > 1:
            groups.append(group)
    return groups


def map_widths(graph: Graph) -> dict[str, int | None]:
    """Map regions, which are where the large wins are.

    The width is not known statically -- it is however many elements the collection
    turns out to have -- so this reports `None` and the estimate uses the measured
    fan-out from the profile where there is one. `map` regions are independent by
    construction (docs/IR_SPEC.md section 6), which is why `research_fanout` and
    `code_review` are in the benchmark suite.
    """
    return {r.id: None for r in graph.regions if r.kind == "map"}


def _transitive_closure(graph: Graph) -> dict[str, set[str]]:
    """Who can reach whom, through data and effect edges together. The combined graph is
    the one that matters: a data edge alone can be broken by having the value already,
    an effect edge never can."""
    reachable: dict[str, set[str]] = {n.id: set() for n in graph.nodes}
    for nid in reversed(graph.topo_order()):
        out: set[str] = set()
        for succ in graph.successors(nid):
            out.add(succ)
            out |= reachable.get(succ, set())
        reachable[nid] = out
    return reachable


def _concurrency_budget(graph: Graph) -> int:
    try:
        from synapse0g.graph.runtime.providers import load_registry

        return load_registry().max_concurrency
    except Exception:  # noqa: BLE001 - no registry on this machine: a sane default
        return 8


class Parallelize:
    name = "parallelize"
    description = "Run independent work concurrently, up to a global and per-provider limit."
    precondition = (
        "No data edge and no effect edge between the nodes; write nodes only with disjoint write "
        "sets; external and irreversible nodes never participate."
    )

    def preconditions(self, graph: Graph, profile: Profile) -> list[Finding]:
        """Name the nodes that cannot be scheduled beside anything.

        For `shop_ops` this is the whole point of the workflow: `send_email` and
        `place_order` each get a finding saying their position is fixed, and the
        dashboard shows Synapse declining to touch them with the reason
        (demo beat 6).
        """
        findings: list[Finding] = []
        for node in graph.nodes:
            if finding := effects.may_reorder(node):
                findings.append(finding)
        # Writes that collide are a separate, more specific explanation.
        writers = [n for n in graph.nodes if n.effect == "write"]
        for i, a in enumerate(writers):
            for b in writers[i + 1 :]:
                if finding := effects.may_run_concurrently(a, b):
                    findings.append(finding)
        # A measured fact about the server, not about the graph, so it is said per node.
        for node_id, record in serialized(graph).items():
            node = graph.node(node_id)
            findings.append(Finding(node=node_id, pass_name=self.name, precondition="server_stable_under_concurrency",
                                    message=_audit_message(node, record)))
        return findings

    def predict(self, graph: Graph, profile: Profile) -> Estimate:
        groups = independent_groups(graph)
        maps = map_widths(graph)
        if not groups and not maps:
            return Estimate(note="nothing in this graph can overlap: every node depends on the one before it")

        budget = _concurrency_budget(graph)
        if profile.is_empty and maps:  # no run yet, so the real width is unknown: assume the limit
            width = budget
        else:
            width = max([len(g) for g in groups] + [_map_width(graph, profile, rid) for rid in maps] or [1])
        width = min(max(width, 1), budget)
        if width <= 1:
            return Estimate(
                note=(
                    "the only independent work is one node wide, so concurrency would win nothing "
                    "and the pass declines rather than add risk for no return"
                )
            )

        saved = _simulate(graph, profile, width)
        return Estimate(
            d_latency_ms=-saved,
            d_tokens=0,  # pure latency: the same calls happen, at the same time
            d_cost_usd=0.0,
            risk=0.02,  # the scheduler re-checks every effect edge independently
            confidence=confidence_from(profile, [n.id for n in _schedulable(graph)]),
            note=(
                f"up to {width} concurrent (limit {budget}); {len(groups)} independent group(s), "
                f"{len(maps)} map region(s)"
            ),
        )

    def apply(self, graph: Graph) -> tuple[Graph, list[Transformation]]:
        groups = independent_groups(graph)
        maps = map_widths(graph)
        if not groups and not maps:
            return graph, []

        budget = _concurrency_budget(graph)
        width = min(max([len(g) for g in groups] + [budget if maps else 1] or [1]), budget)
        if width <= 1:
            return graph, []

        transformations: list[Transformation] = []
        for region_id in maps:
            region = graph.region(region_id)
            transformations.append(
                Transformation(
                    pass_name=self.name,
                    nodes_before=list(region.body),
                    nodes_after=list(region.body),
                    description=(
                        f"{len(region.body)} node(s) in map region {region_id} now run concurrently per element "
                        f"(limit {width}); the elements are independent by construction"
                    ),
                )
            )
        for group in groups:
            transformations.append(
                Transformation(
                    pass_name=self.name,
                    nodes_before=list(group),
                    nodes_after=list(group),
                    description=(
                        f"{', '.join(group)} have no data or effect edge between them and now run concurrently "
                        f"(limit {width})"
                    ),
                )
            )

        serial = serialized(graph)
        limits = dict(graph.meta.get("per_provider") or {})
        for node_id, record in serial.items():
            node = graph.node(node_id)
            provider = _provider(node, record)
            limits[provider] = 1
            transformations.append(
                Transformation(
                    pass_name=self.name,
                    nodes_before=[node_id],
                    nodes_after=[node_id],
                    description=(f"{node_id}: model calls on {provider} stay one at a time. "
                                 + _audit_message(node, record)),
                )
            )
        meta = {"max_concurrency": width, "parallel_groups": groups, "parallel_maps": sorted(maps)}
        if serial:
            meta["per_provider"] = limits
        after = graph.copy_with(meta=meta)
        return after, transformations


def _measured_map_width(profile: Profile, region_id: str) -> int:
    """The fan-out we actually observed, if we have run this graph. Absent any
    measurement the pass assumes the limit, and the firewall measures the truth."""
    observed = (profile.meta.get("map_widths") or {}).get(region_id)
    return int(observed) if observed else 0


def _map_width(graph: Graph, profile: Profile, region_id: str) -> int:
    """Elements per run: stated in the profile, else samples per execution of the region's nodes."""
    stated = _measured_map_width(profile, region_id)
    if stated or profile.is_empty:
        return stated
    samples = [p.samples for nid in graph.region(region_id).body if (p := profile.node(nid))]
    return round(max(samples) / profile.executions) if samples and profile.executions else 0


def _simulate(graph: Graph, profile: Profile, width: int) -> float:
    """Critical path now, versus the sum of the work, under a concurrency limit.

    A real list-scheduling simulation lives in `profile/costmodel.py` (Aditya's
    scheduler simulator). This is the honest cheap version: with no measurements it
    returns 0.0 and says so through `confidence`, rather than producing a number that
    looks like a prediction.
    """
    if profile.is_empty:
        return 0.0
    serial = serialized(graph)
    saved = 0.0
    for region_id in map_widths(graph):
        elements = _map_width(graph, profile, region_id)
        if elements > 1:
            # A chain per element, one after another, against one at a time per `width` slots. Model
            # calls the audit serializes still run one after another across elements.
            body = graph.region(region_id).body
            chain = sum(profile.latency_of(nid) for nid in body)
            one_at_a_time = sum(profile.latency_of(nid) for nid in body if nid in serial)
            after = max(elements * one_at_a_time, math.ceil(elements / width) * chain)
            saved += max(0.0, elements * chain - after)
    for group in independent_groups(graph):
        # Independent nodes at one level: the slowest still has to finish, and serialized model
        # calls add up instead of overlapping.
        free = [profile.latency_of(nid) for nid in group if nid not in serial]
        held = [profile.latency_of(nid) for nid in group if nid in serial]
        total = sum(free) + sum(held)
        saved += max(0.0, total - max(max(free, default=0.0), sum(held)))
    return saved


register(Parallelize())
