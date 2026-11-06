"""A pass that broke a workflow stays out of that workflow until someone lets it back in."""

from __future__ import annotations

import time

from synapse0g.graph.core.store import StoreProtocol


def active(store: StoreProtocol, workflow: str) -> dict:
    return store.get_artifact("quarantine", workflow) or {}


def add(store: StoreProtocol, workflow: str, pass_name: str, reason: str) -> None:
    current = active(store, workflow)
    current[pass_name] = {"reason": reason, "at": time.time()}
    store.put_artifact("quarantine", workflow, current)


def release(store: StoreProtocol, workflow: str, pass_name: str) -> bool:
    current = active(store, workflow)
    found = current.pop(pass_name, None) is not None
    store.put_artifact("quarantine", workflow, current)
    return found
