"""Lift a recorded execution trace into SIR.

The second frontend (SYSTEM.md section 4.2). The DSL is for workflows written with
Synapse in mind; this is for the ones that were not — which is the more interesting
claim, because it means the optimizer works on agents whose authors had never heard of
us.

**Unknown effects default to `external`, always.** A lifted tool is an unrecognized
tool, and an unrecognized tool is assumed to move money until someone says otherwise.
The consequence is a lifted graph that optimizes less than it could, which is slow and
correct; the opposite default is fast and occasionally catastrophic. `effect_overrides`
and the `@node` registry are the two ways to tell Synapse what a tool really does, and
both require someone to make that claim deliberately.

**Data edges come from the recorded last-writer relation, not from guessing.** A step
that read memory key `k` depends on the most recent step that wrote `k`. Recorded
`reads`, `writes` and `parents` make these traces richer than OpenTelemetry's.

Lifting one trace gives one execution path. `lift_many` folds several traces of the same
workflow into one graph: steps that appear in every trace are unconditional, steps that
repeat within a trace are a loop body, and steps that appear in only some traces are
branches. That is how a static graph is recovered from dynamic evidence, and it is why
having a representative set of recorded runs matters.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from typing import Any

from pydantic import BaseModel, Field

from synapse0g.graph.core.hashing import fingerprint
from synapse0g.graph.core.sir import Effect, Graph, Literal_, Node, NodeKind, Ref, Region

#: Workflow step kinds -> SIR node kinds. `final` was a recorded marker for the step
#: that produced the answer; in SIR that is an output edge, not a kind.
KIND_MAP: dict[str, NodeKind] = {
    "llm": "model",
    "model": "model",
    "tool": "tool",
    "retrieval": "retrieval",
    "state": "transform",
    "transform": "transform",
    "final": "transform",
    "io": "io",
    "control": "control",
}

#: Tool names that are irreversible wherever they appear. Defined by the workflow tool policy.
#: `money_moving_tools`, which exists because an agent once re-ran a `place_order` call
#: during a replay. Matched as a substring, deliberately: `place_order_v2` is also an
#: order.
MONEY_MOVING = (
    "place_order", "submit_order", "buy", "sell", "charge", "payment", "pay_",
    "refund", "transfer", "withdraw", "delete_", "drop_", "purge",
)


class Step(BaseModel):
    """One recorded step. The subset of the workflow step that SIR needs.

    Importers (`frontend/importers/otel.py`, `langfuse.py`, `blackbox.py` — Radhesh's)
    normalise their formats into this, so this module never learns about a vendor.
    """

    idx: int
    kind: str
    name: str  # registry model id / tool name
    request: dict = Field(default_factory=dict)
    output: Any = None
    reads: list[str] = Field(default_factory=list)
    writes: list[str] = Field(default_factory=list)
    parents: list[int] = Field(default_factory=list)  # recorded data flow, where available
    input_hash: str | None = None
    latency_ms: float | None = None
    tokens_in: int | None = None
    tokens_out: int | None = None
    error: str | None = None
    meta: dict = Field(default_factory=dict)


class Trace(BaseModel):
    run_id: str
    workflow: str = "lifted"
    steps: list[Step] = Field(default_factory=list)
    task_input: dict = Field(default_factory=dict)
    meta: dict = Field(default_factory=dict)


def infer_effect(step: Step, overrides: dict[str, str] | None = None) -> Effect:
    """What this recorded step is allowed to have done.

    The order of these checks is the whole policy, so it is worth reading:

    1. An explicit override wins. Someone took responsibility for that claim.
    2. A name that moves money or destroys data is `irreversible`, whatever anyone said.
       This check is *after* overrides deliberately — an override may only make a tool
       more restrictive, never less. A declaration cannot make `place_order` safe.
    3. A tool declared with `@node` in this process uses that declaration.
    4. A step that wrote memory and nothing else is `write`; a retrieval or an
       obviously-reading tool is `read`; a model call that touched no state is `pure`.
    5. Anything left is `external`.
    """
    name = step.name.lower()

    declared = (overrides or {}).get(step.name)
    if any(token in name for token in MONEY_MOVING):
        return Effect.IRREVERSIBLE
    if declared:
        return Effect(declared)

    from synapse0g.graph.frontend import dsl

    spec = dsl.spec_for(step.name)
    if spec is not None:
        return spec.effect

    kind = KIND_MAP.get(step.kind, "tool")
    if step.writes:
        return Effect.WRITE
    if kind == "retrieval":
        return Effect.READ
    if kind in ("model", "transform"):
        # A model call or a local transform observes nothing outside the workflow. Its
        # *determinism* is what decides whether two samples may be collapsed, and that
        # is set separately.
        #
        # Unless it read state. A recording says which keys a step read but not whether
        # that state came from inside the workflow or from the world, and `read` is the
        # more restrictive of the two readings -- so a step with a `reads` set is lifted
        # as `read`, not `pure`. It also keeps the write-after-read ordering edges that
        # would otherwise disappear, since a `pure` node may not declare reads at all.
        return Effect.READ if step.reads else Effect.PURE
    return Effect.EXTERNAL


def infer_determinism(step: Step) -> str:
    """Seeded only when the recording proves it. Everything else a model did is
    stochastic, because assuming otherwise is what lets a pass collapse two samples it
    had no right to."""
    if KIND_MAP.get(step.kind) != "model":
        return "deterministic"
    request = step.request or {}
    temperature = request.get("temperature")
    if "seed" in request and temperature in (0, 0.0, None):
        return "seeded"
    if temperature in (0, 0.0):
        return "deterministic"
    try:
        from synapse0g.graph.runtime.providers import model_config

        return model_config(step.name).determinism
    except Exception:  # noqa: BLE001 - an unrecorded model is stochastic until proven otherwise
        return "stochastic"


def _last_writers(steps: list[Step]) -> dict[int, set[int]]:
    """For each step, the most recent earlier step that wrote each key it reads.

    This is the data-flow relation, recovered. A recorded `parents` list is used when
    the trace has one when recorded; otherwise it is reconstructed here.
    """
    writers: dict[str, int] = {}
    deps: dict[int, set[int]] = {}
    for step in steps:
        parents = set(step.parents)
        for key in step.reads:
            if key in writers:
                parents.add(writers[key])
        deps[step.idx] = parents
        for key in step.writes:
            writers[key] = step.idx
    return deps


def lift(
    trace: Trace,
    *,
    effect_overrides: dict[str, str] | None = None,
    staleness_s: float | None = 3600.0,
) -> Graph:
    """One recorded execution -> one SIR graph.

    `staleness_s` is applied to every inferred `read` node, because a `read` with no
    staleness window can never be cached and the lifted graph would show no memoization
    opportunity at all. It is an assumption, so it is a named argument with a visible
    default rather than a constant buried here, and it is recorded in `graph.meta`.
    """
    by_idx = {s.idx: s for s in trace.steps}
    deps = _last_writers(trace.steps)
    nodes: list[Node] = []

    for step in trace.steps:
        effect = infer_effect(step, effect_overrides)
        node_id = f"n{step.idx}"
        args: dict[str, Any] = {}

        # Recorded request fields become literal arguments, except the ones that are
        # about the call rather than its content.
        for key, value in (step.request or {}).items():
            if key in ("messages", "model", "model_id", "seed", "temperature", "max_tokens"):
                continue
            args[key] = Literal_(value=value)

        # Data edges, from the recovered last-writer relation.
        for parent in sorted(deps.get(step.idx, set())):
            if parent in by_idx:
                args[f"from_n{parent}"] = Ref(node=f"n{parent}", output="value")

        nodes.append(
            Node(
                id=node_id,
                kind=KIND_MAP.get(step.kind, "tool"),
                target=step.name,
                args=args,
                outputs=["value"],
                effect=effect,
                determinism=infer_determinism(step),
                reads=list(step.reads),
                writes=list(step.writes) if effect != Effect.PURE else [],
                staleness_s=staleness_s if effect == Effect.READ else None,
                origin={"run": trace.run_id, "step": step.idx},
                meta={
                    k: v
                    for k, v in {
                        "recorded_input_hash": step.input_hash,
                        "recorded_latency_ms": step.latency_ms,
                        "recorded_tokens_in": step.tokens_in,
                        "recorded_tokens_out": step.tokens_out,
                        "recorded_error": step.error,
                    }.items()
                    if v is not None
                },
            )
        )

    outputs = {"result": Ref(node=nodes[-1].id, output="value")} if nodes else {}
    graph = Graph(
        id=f"g_{trace.workflow}_{fingerprint([trace.run_id, [s.name for s in trace.steps]])[:10]}",
        name=trace.workflow,
        nodes=nodes,
        inputs={k: type(v).__name__ for k, v in (trace.task_input or {}).items()},
        outputs=outputs,
        meta={
            "frontend": "lift",
            "lifted_from": trace.run_id,
            "n_steps": len(trace.steps),
            "assumed_staleness_s": staleness_s,
            "effect_overrides": sorted(effect_overrides or {}),
            "note": (
                "Lifted from a recording. Effects were inferred, and anything unrecognised defaulted to "
                "`external`, so this graph optimizes less than a declared one would."
            ),
        },
    )
    return graph.validate_graph()


def lift_many(traces: list[Trace], *, workflow: str | None = None, **kw) -> Graph:
    """Several traces of one workflow -> one graph with its loops recovered.

    Steps that repeat within a trace, at the same position in the call sequence, are a
    loop body: the recorded evidence is that the agent did the same thing again. Steps
    that appear in some traces and not others are the branches, and are marked so a
    reader can see which parts of the graph are conditional.

    The honest limit, stated rather than discovered: this recovers structure that the
    recordings *exhibit*. A branch no trace ever took is not in the graph, and nothing
    here pretends otherwise — `meta.coverage` reports how many traces backed each node.
    """
    if not traces:
        raise ValueError("lift_many needs at least one trace")
    if len(traces) == 1:
        return lift(traces[0], **kw)

    name = workflow or traces[0].workflow
    # The longest trace is the skeleton: it exhibits the most of the workflow.
    skeleton = max(traces, key=lambda t: len(t.steps))
    graph = lift(skeleton, **kw)

    appearances: Counter[str] = Counter()
    for trace in traces:
        for target in {s.name for s in trace.steps}:
            appearances[target] += 1

    repeated: dict[str, int] = {}
    for trace in traces:
        counts = Counter(s.name for s in trace.steps)
        for target, n in counts.items():
            repeated[target] = max(repeated.get(target, 0), n)

    nodes = []
    regions: list[Region] = []
    for node in graph.nodes:
        copy = node.model_copy(deep=True)
        seen_in = appearances[copy.target]
        copy.meta = {
            **copy.meta,
            "coverage": f"{seen_in}/{len(traces)} traces",
            "conditional": seen_in < len(traces),
        }
        nodes.append(copy)

    # One loop region per target the recordings show repeating, holding the first
    # occurrence. A region of repeated work is what `loop_cutoff` needs to see.
    for target, most in repeated.items():
        if most < 2:
            continue
        members = [n.id for n in nodes if n.target == target]
        if not members:
            continue
        body = members[:1]
        region_id = f"r_loop_{target.replace('.', '_')}"
        regions.append(
            Region(
                id=region_id,
                kind="loop",
                body=body,
                condition=Ref(node=body[0], output="value"),
                max_iterations=most,
                meta={"recovered_from": f"{most} repetitions observed in a single trace"},
            )
        )
        for n in nodes:
            if n.id in body:
                n.region = region_id

    return graph.copy_with(
        name=name,
        nodes=nodes,
        regions=regions,
        meta={
            "frontend": "lift_many",
            "n_traces": len(traces),
            "note": (
                "Structure recovered from recorded evidence. A branch no trace ever took is not in this "
                "graph; meta.coverage on each node says how many traces backed it."
            ),
        },
    ).validate_graph()


def implementations_from(trace: Trace) -> dict[str, Any]:
    """Replay implementations for a lifted graph: each node returns what it recorded.

    This is what makes a lifted graph *runnable* without the original agent's code, and
    therefore what lets the optimizer be demonstrated on a trace. It replays recorded
    outputs; it is not a simulation of the tool, and a lifted graph run this way
    measures scheduling only — never tokens or cost, which come from the recording.
    """
    by_target: dict[str, Any] = {}
    for step in trace.steps:
        if step.name in by_target:
            continue

        def make(recorded: Any):
            def replay(**_: Any) -> Any:
                return recorded

            # Tells the executor this *is* the node's output, not a prompt to send.
            # Without it a replayed model node would hand its recorded answer to the
            # provider as a prompt and make a real call -- which would be slow, would
            # spend quota, and would quietly stop being a replay.
            replay.__synapse_replay__ = True
            return replay

        by_target[step.name] = make(step.output)
    return by_target


def from_steps(run_id: str, steps: Iterable[dict], **kw) -> Graph:
    """Convenience for importers: raw dicts -> a lifted graph."""
    return lift(Trace(run_id=run_id, steps=[Step.model_validate(s) for s in steps]), **kw)
