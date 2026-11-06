"""Canonical JSON and content fingerprints.

Canonical JSON and fingerprints for SIR nodes.

The same logical call must fingerprint identically on every machine and in every
process, or CSE, memoization and the cross-run cache all silently stop working —
"silently" being the dangerous part, since the failure mode is a missing optimization
rather than a crash.

See docs/IR_SPEC.md §5.
"""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - import cycle: sir imports nothing from here
    from synapse0g.graph.core.sir import Node


def _default(value: Any) -> Any:
    """Make the un-JSON-able canonical. Order-independent for sets; stable for bytes."""
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, (set, frozenset)):
        return sorted(value, key=repr)
    if isinstance(value, bytes):
        return value.hex()
    return repr(value)


def canonical(value: Any) -> str:
    """One stable text form of a value: sorted keys, no whitespace, unicode kept."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=_default)


def fingerprint(value: Any) -> str:
    """SHA-256 over the canonical form."""
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def node_hash(node: Node, resolved: dict[str, Any]) -> str:
    """The content address of one node execution: the key for CSE, memoize and the cache.

    `resolved` maps argument name -> the *value* that argument has at execution time
    (literals as themselves, refs as the upstream node's actual output). Hashing
    resolved values rather than the Ref is what makes two structurally different nodes
    that compute the same thing collapse.

    `node.cache_key_fields` narrows the key. Narrowing it wrongly is a correctness bug
    (two different calls hash the same and one serves the other's answer), which is why
    the default is every argument and narrowing is explicit in the DSL.
    """
    fields = node.cache_key_fields if node.cache_key_fields is not None else sorted(node.args)
    missing = [f for f in fields if f not in resolved]
    if missing:
        raise KeyError(
            f"node {node.id!r}: cannot hash, no resolved value for {', '.join(missing)}. "
            "Every cache-key field must have a value before the node runs."
        )
    return fingerprint(
        {
            "kind": node.kind,
            "target": node.target,
            # The declaration that built this node, where it differs from the target.
            # Three model nodes can share the model `agent-small` and build three
            # different prompts; without this they would share a cache entry and one
            # would be served another's answer.
            "impl": node.meta.get("impl"),
            # A clipped call sends a different prompt, so it must not share a full call's entry.
            "trim": node.meta.get("trim_chars"),
            "effect": node.effect,
            "args": {f: resolved[f] for f in sorted(fields)},
        }
    )


def state_hash(state: dict[str, Any]) -> str:
    """Fingerprint of workflow-internal state. `loop_cutoff` compares these across
    iterations: two successive iterations with the same state hash cannot change the
    result, so the loop has reached a fixpoint (docs/PASSES.md §6)."""
    return fingerprint(state)
