"""The pass protocol: preconditions, predict, apply.

docs/PASSES.md section 0 is the specification of this file.

**A pass that cannot state its precondition does not ship** (docs/CORRECTNESS.md). Every
pass implements `preconditions(graph, profile) -> list[Finding]`. An empty list means
"legal on this graph, and I can defend why". A non-empty list is a set of reasons it is
not, each naming a node, the rule that failed, and a sentence a human can read.

**Findings are a feature, not an error state.** The dashboard's "why was this not
optimized?" panel renders them directly (docs/API_FOR_UI.md section 3), and being able
to explain a refusal is a large part of why this looks like a compiler rather than a bag
of heuristics. Most teams can only show their wins.

Two universal invariants are enforced here, for every pass, by `run_pass()` rather than
by the passes themselves:

1. `apply()` returns a graph that passes `Graph.validate_graph()`.
2. `apply()` never changes the relative order of two `external` or `irreversible`
   nodes, and never removes, duplicates or caches one.

Both are re-checked independently of what the pass claims, and a pass that violates
either has its result **discarded** rather than reported. Defence in depth: a buggy pass
must produce a slow run, never an unsafe one.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, Field

from synapse0g.graph.core import effects
from synapse0g.graph.core.effects import Finding
from synapse0g.graph.core.sir import Graph, Profile

__all__ = [
    "Estimate",
    "Finding",
    "Pass",
    "PassResult",
    "Transformation",
    "all_passes",
    "get_pass",
    "register",
    "run_pass",
]


class Estimate(BaseModel):
    """What a pass expects to gain here, before anything runs.

    `d_cost_usd` is `None` when a price is unknown -- the estimate says "I cannot tell
    you" rather than guessing (docs/CORRECTNESS.md). `confidence` is how much measured
    profile data backs the prediction: a pass asked to optimize a graph that has never
    run returns `confidence=0.0`, and the manager treats that as "no evidence", not as
    "no gain".
    """

    d_latency_ms: float = 0.0  # negative = faster
    d_tokens: int = 0  # negative = fewer
    d_cost_usd: float | None = None
    risk: float = 0.0  # 0..1, prior probability of breaking equivalence
    confidence: float = 0.0  # 0..1, how much profile data backs this
    declines: bool = False  # the pass itself judged this not worth applying here
    note: str = ""

    @property
    def is_worthwhile(self) -> bool:
        return self.d_latency_ms < 0 or self.d_tokens < 0 or (self.d_cost_usd or 0) < 0

    def beats_noise(self, profile: Profile, baseline_ms: float | None = None) -> bool:
        """Whether a predicted latency gain is bigger than the measured run-to-run
        spread. A "gain" inside the noise band is not a gain, and applying a pass for
        one adds risk for no return (docs/CORRECTNESS.md section 4.3)."""
        if profile.noise_band_pct is None:
            return True  # nothing measured yet: the firewall is what decides later
        wall = baseline_ms if baseline_ms is not None else profile.total.wall_p50_ms
        if not wall:
            return True
        return abs(self.d_latency_ms) > wall * (profile.noise_band_pct / 100.0)


class Transformation(BaseModel):
    """One concrete change, for the UI's transformation list.

    `description` is shown verbatim and is hoverable to highlight `nodes_before` /
    `nodes_after` on the graph, so it is written for a reader, not for a log: "n6 had the
    same content hash as n2 (get_price INFY); collapsed", not "cse applied".
    """

    pass_name: str
    nodes_before: list[str] = Field(default_factory=list)
    nodes_after: list[str] = Field(default_factory=list)
    description: str


class PassResult(BaseModel):
    """The outcome of running one pass: what it did, or why it did not."""

    pass_name: str
    applied: bool
    graph: Graph | None = None
    transformations: list[Transformation] = Field(default_factory=list)
    findings: list[Finding] = Field(default_factory=list)
    estimate: Estimate = Field(default_factory=Estimate)
    refused: str | None = None  # set when the pass produced an unsafe or invalid graph

    @property
    def unsafe(self) -> bool:
        return self.refused is not None


@runtime_checkable
class Pass(Protocol):
    """What every pass implements. Three methods, in the order the manager calls them."""

    name: str
    description: str
    precondition: str  # the rule, in words, for `GET /api/passes` and the UI

    def preconditions(self, graph: Graph, profile: Profile) -> list[Finding]:
        """Every opportunity on this graph this pass must decline, and why.

        Empty means "nothing here is forbidden to me". A finding is not an error and
        does not stop the pass: it names one node the pass left alone, so the dashboard
        can say *why* n7 was not cached rather than silently doing less.
        """
        ...

    def predict(self, graph: Graph, profile: Profile) -> Estimate:
        """What it expects to gain, from measured data where there is any."""
        ...

    def apply(self, graph: Graph) -> tuple[Graph, list[Transformation]]:
        """Do it. Returns a new graph; never mutates the input."""
        ...


# --------------------------------------------------------------------- registry

_PASSES: dict[str, Pass] = {}

#: The order used when there is no time to search. Rationale from docs/PASSES.md
#: section 12: shrink the graph first (cheap, structural), then remove redundancy, then
#: schedule what remains, then choose models last -- because model choice depends on
#: which nodes ended up on the critical path.
DEFAULT_PIPELINE: tuple[str, ...] = (
    "constfold",
    "dce",
    "context_prune",
    "cse",
    "loop_cutoff",
    "memoize",
    "parallelize",
    "downgrade",
)

#: Passes that are implemented and demonstrated but **off by default**.
#: `speculate` spends real quota on calls that may be discarded (our OpenRouter free
#: tier is 50 requests/day); `early_exit` trades accuracy for cost by design and must
#: never be switched on without its accuracy delta beside it.
#: `batch` and `speculate` only mark nodes so far: the runtime does not fuse or start them early.
OFF_BY_DEFAULT: frozenset[str] = frozenset({"speculate", "early_exit", "batch", "context_trim"})

#: Passes whose mark the runtime does not act on yet. Applying one changes no execution.
MARKS_ONLY: frozenset[str] = frozenset({"batch"})


def register(pass_obj: Pass) -> Pass:
    if pass_obj.name in _PASSES:
        raise ValueError(f"pass {pass_obj.name!r} is registered twice")
    _PASSES[pass_obj.name] = pass_obj
    return pass_obj


def all_passes() -> list[Pass]:
    """Every registered pass, in the default pipeline order first."""
    _load()
    order = {name: i for i, name in enumerate(DEFAULT_PIPELINE)}
    return sorted(_PASSES.values(), key=lambda p: (order.get(p.name, len(order)), p.name))


def get_pass(name: str) -> Pass:
    _load()
    if name not in _PASSES:
        raise KeyError(f"no pass {name!r}. Known: {', '.join(sorted(_PASSES))}")
    return _PASSES[name]


def default_pipeline() -> list[str]:
    _load()
    return [name for name in DEFAULT_PIPELINE if name in _PASSES]


def catalogue() -> list[dict]:
    """`GET /api/passes` (docs/API_FOR_UI.md section 8)."""
    return [
        {
            "name": p.name,
            "description": p.description,
            "precondition": p.precondition,
            "on_by_default": p.name not in OFF_BY_DEFAULT and p.name in DEFAULT_PIPELINE,
            "takes_effect_at_runtime": p.name not in MARKS_ONLY,
        }
        for p in all_passes()
    ]


_loaded = False


def _load() -> None:
    """Import the pass modules so their `register()` calls run.

    Deferred rather than done at import time so `passes.base` stays importable on its
    own, which the tests of the protocol itself rely on.
    """
    global _loaded
    if _loaded:
        return
    _loaded = True
    from synapse0g.graph.passes import (  # noqa: F401  - imported for the side effect of registering
        batch,
        constfold,
        context_prune,
        context_trim,
        cse,
        dce,
        downgrade,
        early_exit,
        loop_cutoff,
        memoize,
        parallelize,
        speculate,
    )


# ------------------------------------------------------------------- the driver


def run_pass(pass_obj: Pass, graph: Graph, profile: Profile | None = None) -> PassResult:
    """Run one pass the way the optimizer does, with both universal invariants checked.

    This is the only correct way to invoke a pass. A pass called directly skips the
    safety re-check, which is why `tests/test_passes.py` drives every pass through here
    -- "a tool isn't built until it has been called" (docs/CORRECTNESS.md), and
    import-time correctness proves nothing.
    """
    profile = profile or Profile.empty(graph.id)
    findings = [f.tagged(pass_obj.name) for f in pass_obj.preconditions(graph, profile)]
    estimate = pass_obj.predict(graph, profile)
    if estimate.declines:
        return PassResult(pass_name=pass_obj.name, applied=False, findings=findings, estimate=estimate)

    # Findings do not block the pass: they explain the opportunities it declined. A pass
    # may legally collapse n2 and n6 while refusing n9 for being unseeded, and
    # docs/API_FOR_UI.md section 3 shows exactly that -- findings sitting beside applied
    # transformations, powering the "why was this not optimized?" panel. A pass that
    # declines everything simply produces no transformations.
    after, transformations = pass_obj.apply(graph)

    if not transformations:
        # Legal, but there was nothing here to do. Not a failure, and not a result.
        return PassResult(pass_name=pass_obj.name, applied=False, findings=findings, estimate=estimate)

    # Invariant 1: the graph must still be a graph.
    try:
        after.validate_graph()
    except Exception as exc:  # noqa: BLE001 - reported as a refusal, never swallowed
        return PassResult(
            pass_name=pass_obj.name,
            applied=False,
            findings=findings,
            estimate=estimate,
            refused=f"{pass_obj.name} produced an invalid graph and was discarded: {exc}",
        )

    # Invariant 2: observable effects are untouched, whatever the pass claimed.
    violations = effects.check_preserved(graph, after)
    if violations:
        return PassResult(
            pass_name=pass_obj.name,
            applied=False,
            findings=findings + [v.tagged(pass_obj.name) for v in violations],
            estimate=estimate,
            refused=(
                f"{pass_obj.name} changed observable behaviour and was discarded: {violations[0].message}"
            ),
        )

    after = after.copy_with(
        pipeline=[*graph.pipeline, pass_obj.name],
        derived_from=graph.derived_from or graph.id,
    )
    return PassResult(
        pass_name=pass_obj.name,
        applied=True,
        graph=after,
        transformations=transformations,
        findings=findings,
        estimate=estimate,
    )


def run_pipeline(
    graph: Graph,
    profile: Profile | None = None,
    names: list[str] | None = None,
) -> tuple[Graph, list[PassResult]]:
    """Apply passes in order and return the final graph with every pass's result.

    This is *application*, not *selection*. Choosing which passes to run, in what order,
    from a measured profile -- greedy, beam search, the learned policy -- is
    `passes/manager.py`, which is Aditya's. This function is what the CLI, the API and
    the pass tests need: "apply these, in this order, safely, and tell me what happened".

    A pass that refuses or finds nothing is kept in the results and simply does not
    advance the graph, so the caller can show the full story -- including the passes
    that declined and why.
    """
    current = graph
    results: list[PassResult] = []
    for name in names or default_pipeline():
        result = run_pass(get_pass(name), current, profile)
        results.append(result)
        if result.applied and result.graph is not None:
            current = result.graph
    return current, results


def collect_findings(results: Iterable[PassResult]) -> list[Finding]:
    """Every pass's findings, in pipeline order, for the UI's "why not?" panel."""
    return [f for r in results for f in r.findings]


def collect_transformations(results: Iterable[PassResult]) -> list[Transformation]:
    return [t for r in results for t in r.transformations]


# -------------------------------------------------------------------- utilities
# Shared helpers, here rather than copied into each pass.


def node_ids(nodes: Iterable) -> list[str]:
    return [n.id if hasattr(n, "id") else str(n) for n in nodes]


def slack_of(profile: Profile, node_id: str) -> float | None:
    """Distance from the critical path. A latency gain on a node with slack buys
    nothing, which is why `cse` consults this before claiming one."""
    return profile.slack_of(node_id)


def on_critical_path(profile: Profile, node_id: str) -> bool:
    """With no profile at all, assume yes: better to over-report the opportunity to the
    manager, which re-measures, than to silently skip it."""
    if profile.is_empty or not profile.critical_path:
        return True
    return node_id in set(profile.critical_path_ids())


def latency_saved(profile: Profile, node_id: str) -> float:
    """What removing this node would save on the critical path -- zero if it has slack
    to spare, because the path would simply be bounded by something else."""
    measured = profile.latency_of(node_id)
    if not measured:
        return 0.0
    if not on_critical_path(profile, node_id):
        slack = profile.slack_of(node_id)
        if slack is None:
            return 0.0
        return max(0.0, measured - slack)
    return measured


def tokens_of(profile: Profile, node_id: str) -> int:
    p = profile.node(node_id)
    if p is None:
        return 0
    return int((p.tokens_in_mean or 0) + (p.tokens_out_mean or 0))


def cost_of(profile: Profile, node_id: str) -> float | None:
    p = profile.node(node_id)
    return None if p is None else p.cost_usd_mean


def sum_cost(profile: Profile, ids: Iterable[str]) -> float | None:
    """Summed cost, or `None` if any of them has no price. One unknown makes the total
    unknown; filling it with zero would understate the saving and call it measured."""
    values = []
    for nid in ids:
        c = cost_of(profile, nid)
        if c is None:
            return None
        values.append(c)
    return sum(values) if values else None


def confidence_from(profile: Profile, ids: Iterable[str]) -> float:
    """How much measurement backs a claim about these nodes: the share that have
    samples, scaled by how many samples the thinnest of them has."""
    ids = list(ids)
    if not ids or profile.is_empty:
        return 0.0
    measured = [profile.node(nid) for nid in ids]
    present = [m for m in measured if m is not None and m.samples > 0]
    if not present:
        return 0.0
    coverage = len(present) / len(ids)
    depth = min(1.0, min(m.samples for m in present) / 10.0)
    return round(coverage * depth, 3)
