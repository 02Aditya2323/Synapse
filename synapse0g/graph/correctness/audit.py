"""Does this model server give the same answer when it is busy?

The same prompts run one at a time, then concurrently, at temperature 0 with a fixed seed. Each
prompt counts as stable when every run returns the same text. If answers hold one at a time but
change under concurrency, parallelizing model calls on this server changes behaviour, however
independent the calls look in the graph. No static check can see that, so it has to be measured.
"""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed

PROMPTS = (
    "Review this code and list every bug in one short paragraph:\n\n"
    "def avg(xs):\n    t=0\n    for i in range(len(xs)+1):\n        t+=xs[i]\n    return t/len(xs)",
    "Who wrote the novel that the film Blade Runner is based on, and when was it published? Two sentences.",
    "Plan three web searches to find out which country has the most time zones. One query per line.",
    "Summarize in two sentences why a compiler uses an intermediate representation.",
    "Is 2,147,483,647 prime? Explain in one short paragraph.",
    "Write a one-paragraph review of a function that opens a file and never closes it.",
)


def audit(ask: Callable[[str], str], *, prompts: tuple[str, ...] = PROMPTS, rounds: int = 6,
          concurrency: int = 4) -> dict:
    serial: dict[str, set[str]] = {p: set() for p in prompts}
    for _ in range(rounds):
        for prompt in prompts:
            serial[prompt].add(ask(prompt))

    busy: dict[str, set[str]] = {p: set() for p in prompts}
    with ThreadPoolExecutor(concurrency) as pool:
        for _ in range(rounds):
            futures = {pool.submit(ask, p): p for p in prompts}
            for future in as_completed(futures):
                busy[futures[future]].add(future.result())

    serial_varied = sum(len(v) > 1 for v in serial.values())
    busy_varied = sum(len(v) > 1 for v in busy.values())
    return {
        "prompts": len(prompts),
        "rounds": rounds,
        "concurrency": concurrency,
        "varied_one_at_a_time": serial_varied,
        "varied_under_concurrency": busy_varied,
        "safe_to_parallelize_model_calls": serial_varied == 0 and busy_varied == 0,
        "advice": _advice(serial_varied, busy_varied),
    }


def _advice(serial_varied: int, busy_varied: int) -> str:
    if serial_varied:
        return "Answers change even one at a time, so this model is not reproducible with these settings."
    if busy_varied:
        return ("Answers hold one at a time and change under concurrent requests. Run model calls one at a "
                "time on this server: tools can still overlap.")
    return "Answers held in both modes, on these prompts."


# ------------------------------------------------------------------ stored results
#
# A stored audit travels on the graph being optimized (`graph.meta["server_audits"]`), because a
# pass's `apply()` sees only the graph. An optimized graph therefore records which audits shaped
# its schedule.

KIND = "server_audit"
META_KEY = "server_audits"


def save(store, model_id: str, result: dict, *, provider: str | None = None) -> dict:
    """Keep an audit so the optimizer can use it. The newest result for a model replaces the last."""
    from synapse0g.graph.core.sir import utc_now

    record = {**result, "model_id": model_id, "provider": provider, "recorded_at": utc_now()}
    store.put_artifact(KIND, model_id, record)
    return record


def stored(store) -> dict[str, dict]:
    """Every stored audit, by model id."""
    return dict(store.list_artifacts(KIND, limit=1000))


def attach(graph, audits: dict[str, dict] | None):
    """The graph with the stored audits of the models it calls. Unchanged when there are none."""
    models = {n.target for n in graph.nodes if n.kind == "model"}
    relevant = {m: a for m, a in (audits or {}).items() if m in models}
    return graph.copy_with(meta={META_KEY: relevant}) if relevant else graph


def attach_from_store(graph, store):
    try:
        return attach(graph, stored(store))
    except Exception as exc:  # noqa: BLE001 - a store without artifacts: say so, optimize without audits
        import sys

        print(f"[audit] could not read stored audits: {type(exc).__name__}: {exc}", file=sys.stderr)
        return graph


def unstable(graph, model_id: str) -> dict | None:
    """This model's stored audit if it says overlapping its calls is not safe, else None."""
    record = (graph.meta.get(META_KEY) or {}).get(model_id)
    if record and not record.get("safe_to_parallelize_model_calls", True):
        return record
    return None
