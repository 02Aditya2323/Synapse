"""`early_exit` -- confidence-gated short circuit. **Off by default.**

Inserts a gate that ends the workflow early when a confidence signal exceeds a
calibrated threshold. docs/PASSES.md section 11.

**This pass changes behaviour by design.** It is the one pass in the catalogue that
cannot claim equivalence, and it does not try to: it declares a *tolerance* instead. The
contract is explicit --

- the threshold is calibrated on the **`calib`** split, never on `test`;
- the measured accuracy drop on `test` must be within a declared tolerance;
- **every number it produces is reported with the accuracy delta beside it. Never the
  saving alone.**

**The failure mode is the classic leak**: calibrating the threshold on the same tasks
used to report accuracy. Split discipline is what prevents it (docs/CORRECTNESS.md), and
planted case U10 in docs/CORRECTNESS.md section 5 is exactly this -- a threshold
calibrated on the test split, expected to be caught by the layer-3 split-discipline
check.

So this pass refuses to apply unless it is given a threshold **and** told which split
produced it, and it writes both onto the graph so the firewall can check the provenance
rather than take our word for it.
"""

from __future__ import annotations

from synapse0g.graph.core.effects import Finding
from synapse0g.graph.core.sir import Effect, Graph, Node, Profile, Ref
from synapse0g.graph.passes.base import Estimate, Transformation, register

GATE_TARGET = "early_exit_gate"

#: The accuracy we are willing to lose, in percentage points, unless the caller says
#: otherwise. Small on purpose: this is a trade, and the default should not quietly
#: spend much.
DEFAULT_TOLERANCE_PP = 1.0


def _config(graph: Graph) -> dict | None:
    """`meta.early_exit` = {"threshold": float, "split": "calib", "signal": "<node>.<output>",
    "tolerance_pp": float}. Absent means the pass is off."""
    config = graph.meta.get("early_exit")
    return dict(config) if isinstance(config, dict) else None


def _signal_ref(graph: Graph, config: dict) -> Ref | None:
    raw = str(config.get("signal") or "")
    if "." not in raw:
        return None
    node_id, _, output = raw.partition(".")
    if not graph.has(node_id) or output not in graph.node(node_id).outputs:
        return None
    return Ref(node=node_id, output=output)


def skipped_cone(graph: Graph, after_node: str) -> list[str]:
    """What an early exit would skip: everything downstream of the gate that is not
    needed to produce the gate's own answer."""
    keep = {after_node} | _ancestors(graph, after_node)
    return [n.id for n in graph.nodes if n.id not in keep and n.target != "workflow_input"]


def _ancestors(graph: Graph, node_id: str) -> set[str]:
    seen: set[str] = set()
    frontier = list(graph.node(node_id).depends_on())
    while frontier:
        nid = frontier.pop()
        if nid in seen or not graph.has(nid):
            continue
        seen.add(nid)
        frontier.extend(graph.node(nid).depends_on())
    return seen


class EarlyExit:
    name = "early_exit"
    description = "End the workflow early when a calibrated confidence signal is high enough."
    precondition = (
        "The threshold is calibrated on the calib split (never test), and the measured accuracy drop on "
        "test is within a declared tolerance. This pass trades accuracy for cost and declares a tolerance "
        "rather than claiming equivalence."
    )

    def preconditions(self, graph: Graph, profile: Profile) -> list[Finding]:
        config = _config(graph)
        if config is None:
            return [
                Finding(
                    node="",
                    precondition="threshold calibrated on calib",
                    message=(
                        "early_exit is off: no calibrated threshold is configured. This pass changes behaviour "
                        "by design, so it never switches itself on."
                    ),
                )
            ]
        findings: list[Finding] = []
        split = str(config.get("split") or "")
        if split != "calib":
            findings.append(
                Finding(
                    node="",
                    precondition="threshold calibrated on calib",
                    message=(
                        f"the threshold was calibrated on the {split or 'unnamed'} split. Calibrating on the "
                        f"split we report accuracy from inflates every number; only `calib` is accepted."
                    ),
                )
            )
        if self._signal(graph, config) is None:
            findings.append(
                Finding(
                    node="",
                    precondition="signal names a real node output",
                    message=(
                        f"meta.early_exit.signal is {config.get('signal')!r}, which does not name a "
                        f"'<node>.<output>' in this graph."
                    ),
                )
            )
        if config.get("threshold") is None:
            findings.append(
                Finding(
                    node="",
                    precondition="threshold is a number",
                    message="meta.early_exit.threshold is missing, so there is nothing to gate on.",
                )
            )
        return findings

    def _signal(self, graph: Graph, config: dict) -> Ref | None:
        return _signal_ref(graph, config)

    def predict(self, graph: Graph, profile: Profile) -> Estimate:
        config = _config(graph)
        if config is None or self.preconditions(graph, profile):
            return Estimate(note="early_exit is off until a threshold calibrated on the calib split is supplied")

        signal = self._signal(graph, config)
        assert signal is not None
        skipped = skipped_cone(graph, signal.node)
        p_exit = float(config.get("p_exit", 0.0) or 0.0)
        tolerance = float(config.get("tolerance_pp", DEFAULT_TOLERANCE_PP))

        saved_ms = sum(profile.latency_of(nid) for nid in skipped) * p_exit
        saved_tokens = int(
            sum(
                ((profile.node(nid).tokens_in_mean or 0) + (profile.node(nid).tokens_out_mean or 0))
                for nid in skipped
                if profile.node(nid) is not None
            )
            * p_exit
        )
        costs = [profile.node(nid).cost_usd_mean for nid in skipped if profile.node(nid) is not None]
        saved_cost = None if not costs or any(c is None for c in costs) else sum(costs) * p_exit

        return Estimate(
            d_latency_ms=-saved_ms,
            d_tokens=-saved_tokens,
            d_cost_usd=None if saved_cost is None else -saved_cost,
            # The only pass that does not claim equivalence, so its risk is not a
            # probability of being wrong -- it is the declared tolerance itself.
            risk=1.0,
            confidence=0.0 if profile.is_empty else 0.4,
            note=(
                f"skips {len(skipped)} node(s) on an estimated {p_exit:.0%} of tasks. Reports a "
                f"{tolerance:.1f}pp accuracy tolerance, not equivalence: never show this saving without the "
                f"measured accuracy delta beside it."
            ),
        )

    def apply(self, graph: Graph) -> tuple[Graph, list[Transformation]]:
        config = _config(graph)
        if config is None or self.preconditions(graph, Profile.empty(graph.id)):
            return graph, []

        signal = self._signal(graph, config)
        assert signal is not None
        if any(n.target == GATE_TARGET for n in graph.nodes):
            return graph, []

        threshold = float(config["threshold"])
        skipped = skipped_cone(graph, signal.node)
        gate = Node(
            id=_fresh_id(graph, "gate"),
            kind="control",
            target=GATE_TARGET,
            args={"confidence": signal, "threshold": _literal(threshold)},
            outputs=["exit_early"],
            effect=Effect.PURE,
            determinism="deterministic",
            meta={
                # Written onto the graph so the firewall can check the provenance of the
                # threshold instead of trusting the pass that used it.
                "threshold": threshold,
                "calibrated_on": config.get("split"),
                "tolerance_pp": float(config.get("tolerance_pp", DEFAULT_TOLERANCE_PP)),
                "skips": skipped,
            },
        )

        transformation = Transformation(
            pass_name=self.name,
            nodes_before=[signal.node],
            nodes_after=[gate.id],
            description=(
                f"added a gate on {signal.node}.{signal.output} at threshold {threshold:g} (calibrated on the "
                f"{config.get('split')} split); when it fires, {len(skipped)} node(s) are skipped. Trades "
                f"accuracy for cost: report the accuracy delta beside any saving."
            ),
        )
        return graph.copy_with(nodes=[*graph.nodes, gate]), [transformation]


def _literal(value: float):
    from synapse0g.graph.core.sir import Literal_

    return Literal_(value=value)


def _fresh_id(graph: Graph, stem: str) -> str:
    existing = set(graph.node_ids)
    if stem not in existing:
        return stem
    i = 2
    while f"{stem}{i}" in existing:
        i += 1
    return f"{stem}{i}"


register(EarlyExit())
