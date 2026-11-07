"""The optimized agent, as code: the optimized SIR and the approved call policy, lowered back to Python.

No model writes this code. It is generated from three things synapse0g already has after a job:

- the customer's source, whose step functions are imported unchanged (their logic stays theirs);
- the optimized SIR (`app.after`): every step, its effect, the data it reads, what was memoized;
- the call policy the firewall approved: per step, hidden reasoning off and/or a cheaper model.

Each optimization becomes a fixed piece of code, and the file says which line came from which pass:

    parallelize   -> the graph's edges are its data dependencies, nothing more: steps that read only the
                     input start together from START; a step waits only for the steps it reads from
    memoize       -> a cache around each pure step the pass marked, keyed on the data it reads
    call policy   -> a wrapper on the agent's model client that recognises each step the way synapse0g's
                     proxy does (model + system prompt + tools) and applies only that step's approved change
    irreversible  -> left exactly in place: never cached, reordered or run in parallel, and held back
                     unless the caller allows it

So the code handed back is the optimized graph itself, written out.
"""

from __future__ import annotations

from pathlib import Path

PSEUDO = ("n_in", "n_state")  # the graph's input and final state: not steps


def _graph(ui: dict) -> dict:
    """Steps in their written order, what each waits for (data and effect edges), and what each reads."""
    order = [n for n in (ui.get("meta") or {}).get("declared_order") or [] if n not in PSEUDO]
    nodes = {n["id"]: n for n in ui["nodes"] if n["id"] not in PSEUDO}
    order += [n for n in nodes if n not in order]
    waits = {n: set() for n in order}
    reads = {n: set() for n in order}
    for e in ui["edges"]["data"]:
        if e["to"] in waits:
            reads[e["to"]].add(e.get("output") or "")
            if e["from"] in waits:
                waits[e["to"]].add(e["from"])
    for e in ui["edges"].get("effect") or []:
        if e["to"] in waits and e["from"] in waits:
            waits[e["to"]].add(e["from"])
    # keep only the edges that matter: a step reached through another step needs no edge of its own
    def reach(src, seen=None):
        seen = seen or set()
        for p in waits[src]:
            if p not in seen:
                seen.add(p)
                reach(p, seen)
        return seen
    direct = {n: {p for p in waits[n] if not any(p in reach(q) for q in waits[n] if q != p)} for n in order}
    return {"order": order, "nodes": nodes, "waits": direct, "reads": {k: sorted(v - {""}) for k, v in reads.items()}}


def generate_langgraph(*, module: str, state_class: str, builder_attr: str, ui_after: dict, policy: dict,
                       step_labels: dict, reasoning_off: dict, refused: list[dict], verdict: dict | None,
                       job: str, report: str | None, sample_input: dict, functions: dict) -> str:
    """The optimized agent as a LangGraph file. `functions[node] = name` when the step is a module-level
    function of that name; otherwise the step's runnable is taken from the original builder."""
    g = _graph(ui_after)
    memo = [n for n in g["order"] if (g["nodes"][n].get("meta") or {}).get("memoize")]
    protected = [n for n in g["order"] if g["nodes"][n].get("effect") in ("external", "irreversible")]
    groups = (ui_after.get("meta") or {}).get("parallel_groups") or []
    starts = [n for n in g["order"] if not g["waits"][n]]
    ends = [n for n in g["order"] if not any(n in g["waits"][m] for m in g["order"])]

    out: list[str] = []
    w = out.append
    w('"""Your agent, optimized by synapse0g. Generated code: regenerate rather than edit by hand.')
    w("")
    w(f"Job {job}. Generated from the optimized SIR graph (sir_after.json) and the call policy the firewall")
    w(f"approved (policy.json){f', from {report}' if report else ''}. Your step functions are imported unchanged from")
    w(f"{module}.py: only how they are wired and how their model calls are sent has changed.")
    if verdict:
        w("")
        w(f"Held-out verdict: {'APPROVE' if verdict.get('approve') else 'BLOCK'}"
          + (f", cost {round((verdict.get('cost_gain') or 0) * 100)}% lower, time {round((verdict.get('latency_gain') or 0) * 100)}% lower"
             if verdict.get("cost_gain") is not None else "")
          + (f", passed {' / '.join(str(p) for p in verdict.get('passes') or [])}" if verdict.get("passes") else ""))
    w("")
    w("    python optimized_agent.py                 # runs the request this job was paid for (input.json)")
    w("    python optimized_agent.py my_input.json   # any input of the same shape")
    if protected:
        w(f"    python optimized_agent.py --allow-irreversible   # also runs {', '.join(protected)}")
    w('"""')
    w("")
    w("from __future__ import annotations")
    w("")
    w("import hashlib")
    w("import inspect")
    w("import json")
    w("import sys")
    w("import threading")
    w("import time")
    w("from pathlib import Path")
    w("")
    w("from langgraph.graph import END, START, StateGraph")
    w("")
    w(f"import {module} as original  # your code, unchanged")
    w("")
    w("HERE = Path(__file__).resolve().parent")
    w("")
    # ---- call policy
    w("# ---- per-step call changes, approved by the firewall ------------------------------------------------")
    w("# A step is recognised the way synapse0g's proxy recognises it: its model, system prompt and tools.")
    w("STEP_CHANGES = {")
    for key, sp in policy.items():
        change = {}
        if sp.get("route_to"):
            change["model"] = sp["route_to"]
        if sp.get("reasoning_off"):
            change["extra_body"] = reasoning_off.get(sp.get("route_to") or "", reasoning_off.get("*", {}))
        label = step_labels.get(key) or key
        w(f"    {key!r}: {change!r},  # {label}")
    w("}")
    for r in refused:
        w(f"# refused by the firewall: {r['technique']} on \"{r.get('label') or r['step']}\": {'; '.join(r.get('reasons') or [])}")
    w("")
    w("")
    w("def _step_key(kwargs: dict) -> str:")
    w('    system = ""')
    w("    for m in kwargs.get(\"messages\") or []:")
    w('        if m.get("role") in ("system", "developer"):')
    w('            c = m.get("content")')
    w("            system = c if isinstance(c, str) else json.dumps(c)")
    w("            break")
    w('    tools = sorted(((t.get("function") or t).get("name", "?")) for t in kwargs.get("tools") or [])')
    w('    raw = json.dumps([kwargs.get("model"), system, tools])')
    w('    return "s-" + hashlib.sha256(raw.encode()).hexdigest()[:8]')
    w("")
    w("")
    w("class _Completions:")
    w("    def __init__(self, inner):")
    w("        self._inner = inner")
    w("")
    w("    def create(self, **kwargs):")
    w("        change = STEP_CHANGES.get(_step_key(kwargs))")
    w("        if change:")
    w('            kwargs = {**kwargs, **{k: v for k, v in change.items() if k != "extra_body"}}')
    w('            if change.get("extra_body"):')
    w('                kwargs["extra_body"] = {**(kwargs.get("extra_body") or {}), **change["extra_body"]}')
    w("        return self._inner.create(**kwargs)")
    w("")
    w("")
    w("class _Chat:")
    w("    def __init__(self, inner):")
    w("        self.completions = _Completions(inner.completions)")
    w("")
    w("")
    w("class StepChangedClient:")
    w('    """Your model client, with each step\'s approved change applied to that step\'s calls only."""')
    w("")
    w("    def __init__(self, inner):")
    w("        self._inner = inner")
    w("        self.chat = _Chat(inner.chat)")
    w("")
    w("    def __getattr__(self, name):")
    w("        return getattr(self._inner, name)")
    w("")
    w("")
    w("original.client = StepChangedClient(original.client)")
    w("")
    # ---- memoize
    if memo:
        w("# ---- memoize: pure steps, served from a cache keyed on the data they read ----------------------------")
        w('_CACHE_PATH = HERE / ".synapse0g_cache.json"')
        w("_cache_lock = threading.Lock()")
        w("")
        w("")
        w("def _load_cache() -> dict:")
        w("    try:")
        w('        return json.loads(_CACHE_PATH.read_text(encoding="utf-8"))')
        w("    except (OSError, ValueError):")
        w("        return {}")
        w("")
        w("")
        w("def memoized(step, name: str, reads: list[str]):")
        w('    """Same inputs, same answer: the step is pure, so its answer is kept and served again."""')
        w("    try:")
        w("        source = inspect.getsource(step)")
        w("    except (OSError, TypeError):")
        w("        source = name")
        w("")
        w("    def run(state):")
        w("        key = hashlib.sha256(json.dumps([name, source, {r: state.get(r) for r in reads}], sort_keys=True,")
        w('                                        default=str).encode()).hexdigest()')
        w("        with _cache_lock:")
        w("            hit = _load_cache().get(key)")
        w("        if hit is not None:")
        w('            print(f"  {name}: served from the cache")')
        w("            return hit")
        w("        out = step(state)")
        w("        with _cache_lock:")
        w("            cache = _load_cache()")
        w("            cache[key] = out")
        w('            _CACHE_PATH.write_text(json.dumps(cache, default=str), encoding="utf-8")')
        w("        return out")
        w("")
        w("    run.__name__ = name")
        w("    return run")
        w("")
        w("")
    # ---- protection
    if protected:
        w("# ---- irreversible and external steps: in their written place, never cached or run in parallel -------")
        w("ALLOW_IRREVERSIBLE = False")
        writes = {n: next((e["output"] for e in ui_after["edges"]["data"] if e["from"] == n and e.get("output")), "result")
                  for n in protected}
        w(f"WRITES = {writes!r}")
        w("")
        w("")
        w("def protected(step, name: str):")
        w('    """Runs only when the caller allows it; otherwise says it was held back. Its position is unchanged."""')
        w("")
        w("    def run(state):")
        w("        if not ALLOW_IRREVERSIBLE:")
        w("            # the step's own output, saying it did not run")
        w('            return {WRITES[name]: {"status": f"held back: {name} is irreversible; run with --allow-irreversible"}}')
        w("        return step(state)")
        w("")
        w("    run.__name__ = name")
        w("    return run")
        w("")
        w("")
    # ---- the graph
    w("# ---- the optimized graph: sir_after.json, written out ---------------------------------------------------")
    w("def build() -> StateGraph:")
    w(f"    g = StateGraph(original.{state_class})")
    for n in g["order"]:
        ref = f"original.{functions[n]}" if functions.get(n) else f"original.{builder_attr}.nodes[{n!r}].runnable"
        note = []
        if n in memo:
            ref = f"memoized({ref}, {n!r}, {g['reads'][n]!r})"
            note.append("memoize: pure")
        if n in protected:
            ref = f"protected({ref}, {n!r})"
            note.append(f"{g['nodes'][n].get('effect')}: never cached or reordered")
        w(f"    g.add_node({n!r}, {ref})" + (f"  # {'; '.join(note)}" if note else ""))
    w("")
    in_group = {x for grp in groups for x in grp}
    for n in starts:
        why = "parallelize: reads only the input, starts with the others" if n in in_group else "reads only the input"
        w(f"    g.add_edge(START, {n!r})  # {why}")
    for n in g["order"]:
        ws = sorted(g["waits"][n], key=g["order"].index)
        if not ws:
            continue
        if len(ws) == 1:
            w(f"    g.add_edge({ws[0]!r}, {n!r})  # {n} reads what {ws[0]} writes")
        else:
            w(f"    g.add_edge({ws!r}, {n!r})  # {n} waits for all of these, and only these")
    for n in ends:
        w(f"    g.add_edge({n!r}, END)")
    w("    return g")
    w("")
    w("")
    w("builder = build()")
    w("app = builder.compile()")
    w("")
    w("")
    w('if __name__ == "__main__":')
    w('    args = [a for a in sys.argv[1:] if not a.startswith("--")]')
    if protected:
        w('    ALLOW_IRREVERSIBLE = "--allow-irreversible" in sys.argv')
    w('    inputs = json.loads(Path(args[0] if args else HERE / "input.json").read_text(encoding="utf-8"))')
    w("    t0 = time.perf_counter()")
    w("    state = app.invoke(inputs)")
    w('    print(f"done in {time.perf_counter() - t0:.1f} s")')
    w("    print(json.dumps({k: v for k, v in state.items() if k not in inputs}, indent=1, default=str)[:4000])")
    return "\n".join(out) + "\n"


def generate_function(*, module: str, entry: str, job: str) -> str:
    """A plain-Python agent, optimized: the same function, compiled and run by synapse0g's runtime, which runs its
    optimized SIR (independent steps together, pure steps cached, irreversible steps flagged and held back)."""
    return f'''"""Your agent, optimized by synapse0g (job {job}). Generated code: regenerate rather than edit by hand.

Your function `{entry}` is imported unchanged from {module}.py. synapse0g compiles it to the optimized SIR
(sir_after.json) and runs that graph: independent steps together, pure steps from the cache, and irreversible or
external steps flagged and held back unless you allow them.

    python optimized_agent.py                 # runs the input this job was paid for (input.json)
    python optimized_agent.py my_input.json
    python optimized_agent.py --allow-irreversible
"""

import json
import sys
import time
from pathlib import Path

import synapse0g

import {module} as original  # your code, unchanged

HERE = Path(__file__).resolve().parent
app = synapse0g.compile(original.{entry}, effects=getattr(original, "EFFECTS", {{}}))

if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    inputs = json.loads(Path(args[0] if args else HERE / "input.json").read_text(encoding="utf-8"))
    t0 = time.perf_counter()
    out = app.invoke(**inputs, allow_irreversible="--allow-irreversible" in sys.argv)
    print(f"done in {{time.perf_counter() - t0:.1f}} s")
    print(json.dumps(out, indent=1, default=str)[:4000])
'''


def readme(*, job: str, module: str, applied: list[str], protected: list[str], verdict: dict | None) -> str:
    return f"""# Your optimized agent (synapse0g job {job})

| File | What it is |
|---|---|
| `optimized_agent.py` | your agent, optimized: generated from `sir_after.json` and `policy.json` |
| `{module}.py` | your agent as you wrote it, unchanged: the optimized one imports its steps from here |
| `sir_before.json` / `sir_after.json` | the graph synapse0g lifted from your code, and the graph after the passes |
| `policy.json` | the per-step call changes the firewall approved, and the ones it refused |
| `OPTIMIZATION.md` | what changed, why, what was refused, and this job's measured run |
| `input.json` | the request this job was paid for |

Run it from this folder, in the environment your agent already uses:

    python optimized_agent.py
    python optimized_agent.py your_input.json

Graph passes applied: {', '.join(applied) or 'none'}. Never touched: {', '.join(protected) or 'none'}
(held back unless you pass `--allow-irreversible`).
{('Held-out verdict: ' + ('APPROVE' if verdict.get('approve') else 'BLOCK')) if verdict else ''}
"""


def optimization_md(*, explain: str, policy_described: list[str], refused: list[dict], run: dict, paid: dict) -> str:
    calls = run.get("calls") or []
    lines = ["# What synapse0g changed, and why", "", "## The graph (from the passes)", "", "```", explain, "```", "",
             "## Per-step call changes (approved by the firewall)", ""]
    lines += [f"- {d}" for d in policy_described] or ["- none"]
    lines += ["", "## Refused", ""]
    lines += [f"- {r['technique']} on \"{r.get('label') or r['step']}\": {'; '.join(r.get('reasons') or [])}" for r in refused] or ["- none"]
    lines += ["", "## This job, measured", "",
              f"- paid by transaction `{paid.get('tx')}` (quote {paid.get('quote')})",
              f"- answer right: {run.get('passed')}; time {run.get('wall_s')} s; 0G charged {run.get('billed_0g')} 0G over {len(calls)} calls",
              "", "| step | model | 0G request id | 0G charged |", "|---|---|---|---|"]
    lines += [f"| {c.get('step')} | {c.get('model')} | {c.get('request_id')} | {c.get('billed_0g')} |" for c in calls]
    return "\n".join(lines) + "\n"


def module_functions(module, node_ids: list[str]) -> dict:
    """Steps that are module-level functions of the same name, referenced by name in the generated code."""
    out = {}
    for n in node_ids:
        f = getattr(module, n, None)
        if callable(f) and getattr(f, "__name__", None) == n and getattr(f, "__module__", None) == module.__name__:
            out[n] = n
    return out


def write_pack(folder: Path, files: dict[str, str]) -> Path:
    """The pack as a folder and a zip beside it. Returns the zip."""
    import zipfile

    folder.mkdir(parents=True, exist_ok=True)
    for name, text in files.items():
        (folder / name).write_text(text, encoding="utf-8")
    zpath = folder.with_suffix(".zip")
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
        for name, text in files.items():
            z.writestr(f"{folder.name}/{name}", text)
    return zpath
