"""What the dashboard shows: the saved run reports, reshaped into one feed.

Nothing here is computed from a model or a guess. Every number is read from a report in
`reports/`, which holds each call's measured tokens, time and cost. Where a number is missing
from the report, it is missing here too.

Three featured results, one per claim:
  faster   the research workflow graph on 0G, run unchanged and optimized on held-out tasks
  cheaper  the LangGraph maths agent on Groq, call-level policy, held-out tasks
  blocked  the tau-bench retail agent on 0G: a cheaper policy the firewall refused
"""

from __future__ import annotations

import json
from collections import Counter
from statistics import mean, median

from synapse0g import effects
from synapse0g.registry import ROOT

REPORTS = ROOT / "reports"

FEATURED = [
    ("faster", "graph", "synapse_graph/research_fanout_test_0g_first_run__thinking_off.json"),
    ("cheaper", "call", "math_groq-main_2026-11-05T2130.json"),
    ("blocked", "call", "retail_zg-main_2026-11-05T2127.json"),
]

PROVIDER_NAMES = {"zerog": "0G Compute", "groq": "Groq"}

# tau-bench tools that only read: everything else in its tool list changes the database
_RETAIL_READS = {
    "get_order_details", "get_product_details", "get_user_details", "list_all_product_types",
    "find_user_id_by_email", "find_user_id_by_name_zip", "think", "calculate",
}


def _load(rel: str) -> dict:
    return json.loads((REPORTS / rel).read_text(encoding="utf-8"))


def _change(before: float | None, after: float | None) -> float | None:
    """Fractional reduction: 0.25 means after is 25% lower than before."""
    if before in (None, 0) or after is None:
        return None
    return 1 - after / before


def _metric(name, unit, before, after, *, ci=None, note=None, lower_is_better=True):
    return {"name": name, "unit": unit, "before": before, "after": after, "change": _change(before, after),
            "ci": ci, "note": note, "lower_is_better": lower_is_better}


# ---- the graph workflow (research on 0G) --------------------------------------------------
def _graph_nodes(graph: dict, other: dict | None) -> dict:
    """Nodes, edges and regions of a workflow graph. Edges come from each argument's reference.

    A model node is named by the step that reads its output (`parse_plan` -> plan). A node is
    marked changed when its model moved or it newly runs in parallel; memoization is shown as a
    tag, since it applies to every pure node."""
    others = {n["id"]: n for n in (other or {}).get("nodes", [])}
    parallel = set((graph.get("meta") or {}).get("parallel_maps") or [])
    other_parallel = set(((other or {}).get("meta") or {}).get("parallel_maps") or [])
    # steps that are not a map region but were found independent and run side by side
    grouped = {nid for g in (graph.get("meta") or {}).get("parallel_groups") or [] for nid in g}
    other_grouped = {nid for g in ((other or {}).get("meta") or {}).get("parallel_groups") or [] for nid in g}
    consumers: dict[str, list[str]] = {}
    for n in graph["nodes"]:
        for ref in (n.get("args") or {}).values():
            if isinstance(ref, dict) and "node" in ref:
                consumers.setdefault(ref["node"], []).append(n["target"].split(".")[-1])
    nodes, edges = [], []
    for n in graph["nodes"]:
        meta = n.get("meta") or {}
        o = others.get(n["id"])
        label = n["target"].split(".")[-1].removeprefix("langgraph:")
        if n["kind"] == "model":
            reader = next((c for c in consumers.get(n["id"], []) if c.startswith("parse_")), None)
            label = reader.removeprefix("parse_") if reader else label
        elif n["target"] == "workflow_input":
            label = "input: " + ", ".join(n.get("outputs") or [])
        region = n.get("region")
        nodes.append({
            "id": n["id"],
            "label": label,
            "kind": n["kind"],
            "effect": n["effect"],
            "determinism": n["determinism"],
            "model": n["target"] if n["kind"] == "model" else None,
            "region": region,
            "memoized": bool(meta.get("memoize")),
            "concurrent": n["id"] in grouped,
            "changed": o is not None and (o["target"] != n["target"] or (region in parallel) != (region in other_parallel)
                                          or (n["id"] in grouped) != (n["id"] in other_grouped)),
            "reads": meta.get("measured_reads"),
            "writes": meta.get("measured_writes"),
        })
        for arg, ref in (n.get("args") or {}).items():
            if isinstance(ref, dict) and "node" in ref:
                edges.append({"source": ref["node"], "target": n["id"], "label": arg})
    regions = [{"id": r["id"], "kind": r["kind"], "nodes": r["body"], "parallel": r["id"] in parallel,
                "limit": (graph.get("meta") or {}).get("max_concurrency") if r["id"] in parallel else None}
               for r in graph.get("regions") or []]
    return {"nodes": nodes, "edges": edges, "regions": regions}


def compile_workflow(name: str) -> dict:
    """Compile one of the bundled workflows with the graph engine and run its default pass
    pipeline. Offline: no provider is called. Returns both graphs and every pass's result."""
    from synapse0g.graph.bench.suite import GRAPHS
    from synapse0g.graph.core.sir import Profile
    from synapse0g.graph.passes.base import run_pipeline

    graph = GRAPHS[name].graph()
    optimized, results = run_pipeline(graph, Profile.empty(graph.id), None)
    passes = []
    for r in results:
        d = r.model_dump(mode="json")
        passes.append({k: v for k, v in d.items() if k not in ("graph", "before", "after")})
    return {"before": graph.model_dump(mode="json"), "after": optimized.model_dump(mode="json"), "passes": passes,
            # the shape the SIR graph view reads: the graph plus its derived data and effect edges
            "sir": {"before": graph.to_ui(), "after": optimized.to_ui(), "passes": passes}}


def _transforms(passes: list[dict]) -> list[dict]:
    return [{"pass": p["pass_name"], "applied": p["applied"],
             "changes": [t["description"] for t in p["transformations"]],
             "findings": [f"{f['node']}: {f['message']}" for f in p["findings"]],
             "note": (p.get("estimate") or {}).get("note")}
            for p in passes]


def _protections(passes: list[dict], graph: dict) -> dict[str, list[str]]:
    """For each node whose effect forbids a transformation, what the passes refused and why."""
    effect = {n["id"]: n["effect"] for n in graph["nodes"]}
    out: dict[str, list[str]] = {}
    for p in passes:
        for f in p["findings"]:
            if effect.get(f["node"]) in ("write", "external", "irreversible") and f["precondition"].startswith("effect"):
                out.setdefault(f["node"], []).append(f"{p['pass_name']}: {f['message']}")
    return out


WORKFLOWS = {
    "research_fanout": "Plan sub-questions, search and summarise each, answer (HotpotQA)",
    "code_review": "Review each changed file, then merge the reviews",
    "rag_qa": "Retrieve passages, answer from them",
    "math_tools": "Word problems with a calculator tool (GSM8K)",
    "shop_ops": "Customer-service agent that refunds, cancels and orders: irreversible tools",
}


def workflows() -> list[dict]:
    """Every bundled workflow, compiled now by the engine, with the saved runs that measured it."""
    runs = history()
    out = []
    for name, about in WORKFLOWS.items():
        c = compile_workflow(name)
        protected = _protections(c["passes"], c["before"])
        before = _graph_nodes(c["before"], None)
        after = _graph_nodes(c["after"], c["before"])
        for g in (before, after):
            for n in g["nodes"]:
                n["refused"] = protected.get(n["id"], [])
        out.append({
            "name": name,
            "about": about,
            "graph_id": c["before"]["id"],
            "nodes": len(c["before"]["nodes"]),
            "effects": dict(Counter(n["effect"] for n in c["before"]["nodes"])),
            "graph": {"before": before, "after": after},
            "sir": c["sir"],
            "transforms": _transforms(c["passes"]),
            "protected": [{"node": k, "reasons": v} for k, v in protected.items()],
            "runs": [r for r in runs if r["agent"] == name],
        })
    return out


def _graph_result(kind: str, rel: str) -> dict:
    rep = _load(rel)
    graphs = compile_workflow("research_fanout")
    v = rep["verdict"]
    seq = {r["task_id"]: r for r in rep["rows"]["sequential"]}
    opt = {r["task_id"]: r for r in rep["rows"]["optimized"]}
    status = {p["task_id"]: p for p in rep["per_task"]}

    tasks = []
    for tid, s in seq.items():
        o = opt.get(tid, {})
        st = status.get(tid, {})
        tasks.append({
            "id": tid,
            "status": "crashed" if s.get("crashed") or o.get("crashed") else st.get("status", "unknown"),
            "before": [s.get("passed")],
            "after": o.get("passed"),
            "before_wall_s": s["wall_ms"] / 1000 if s.get("wall_ms") is not None else None,
            "after_wall_s": o["wall_ms"] / 1000 if o.get("wall_ms") is not None else None,
            "before_cost_usd": s.get("cost_usd"),
            "after_cost_usd": o.get("cost_usd"),
            "before_calls": s.get("provider_calls"),
            "after_calls": o.get("provider_calls"),
            "cache_hits": o.get("cache_hits"),
            "answer_before": s.get("answer"),
            "answer_after": o.get("answer"),
            "error": s.get("error") or o.get("error"),
        })
    ratios = [t["before_wall_s"] / t["after_wall_s"] for t in tasks if t["before_wall_s"] and t["after_wall_s"]]
    b, a = v["cold"]["before"], v["cold"]["after"]
    n = len(tasks)
    passes = (sum(1 for t in tasks if t["before"][0]), sum(1 for t in tasks if t["after"]))
    transforms = _transforms(graphs["passes"])
    return {
        "id": "research-0g",
        "claim": kind,
        "layer": "graph",
        "sir": graphs["sir"],
        "title": "Research workflow on 0G",
        "subtitle": "Plan, search each sub-question, summarise, answer (HotpotQA)",
        "provider": "0G Compute",
        "model": "qwen3.8-flash (planning step moved to qwen3.7-flash)",
        "tasks": n,
        "split": "held-out",
        "headline": {"value": median(ratios), "unit": "x", "label": "faster (median per task)"},
        "verdict": {
            "decision": v["decision"],
            "reasons": [v["reason"]],
            "counts": {"fixed": v["fixed"], "unchanged": v["unchanged"], "broken": v["broken"], "crashed": v["crashed"]},
            "passes": {"before": passes[0], "after": passes[1]},
        },
        "metrics": [
            _metric("Time per task (median)", "s", b["wall_p50_ms"] / 1000, a["wall_p50_ms"] / 1000),
            _metric("Time per task (95th percentile)", "s", b["wall_p95_ms"] / 1000, a["wall_p95_ms"] / 1000),
            _metric("Cost per task", "usd", b["cost_usd"], a["cost_usd"],
                    note="measured tokens x 0G's listed price; 0G's per-request bill was not recorded for this run"),
            _metric("Tokens per task", "tokens", b["tokens"], a["tokens"]),
            _metric("Model calls per task", "calls", b["provider_calls"], a["provider_calls"]),
            _metric("Tasks passed", "tasks", passes[0], passes[1], lower_is_better=False),
        ],
        "billing": {
            "source": "listed",
            "explain": "Cost is measured tokens times 0G's listed catalog price. This run did not store 0G's billed amount per request, so no credit saving is claimed from 0G's bill.",
            "spent_usd": rep["ledger"]["spent"]["cost_usd"],
        },
        "graph": {"before": _graph_nodes(graphs["before"], None),
                  "after": _graph_nodes(graphs["after"], graphs["before"])},
        "transforms": transforms,
        "policy": [t for p in transforms if p["applied"] for t in p["changes"]],
        "task_rows": tasks,
        "search": None,
        "report": "reports/" + rel,
    }


# ---- call-level agents (maths on Groq, retail on 0G) -------------------------------------
def _arm_records(rep: dict, label: str):
    return rep["runs"][label]


def _agent_graph(rep: dict, labels: list[str], label_map: dict, kind: str) -> dict:
    """The agent's loop as it actually ran: its model step, the tools it called, how often.

    Built from the per-call records of the given runs, averaged per task."""
    runs = [rep["runs"][lb] for lb in labels]
    ntasks = sum(len(r) for r in runs) or 1
    calls, tools, lat, tin, tout, cost = 0, Counter(), [], 0, 0, 0.0
    models, techniques = Counter(), Counter()
    for r in runs:
        for t in r.values():
            for rec in t["records"]:
                calls += 1
                lat.append(rec["latency_s"])
                tin += rec.get("tokens_in") or 0
                tout += rec.get("tokens_out") or 0
                cost += rec.get("cost_usd") or 0.0
                models[rec["tag"]] += 1
                for tech in rec.get("techniques") or []:
                    techniques[tech] += 1
                for name in rec.get("tool_calls") or []:
                    tools[name] += 1
    step_label = next(iter(rep["steps"].values())).split(" [tools")[0]
    nodes = [{"id": "in", "label": "customer" if kind == "retail" else "task", "kind": "io", "effect": "pure",
              "stats": None, "note": "simulated customer, separate unoptimized session" if kind == "retail" else None}]
    nodes.append({
        "id": "agent", "label": "agent model step", "kind": "model", "effect": "pure",
        "model": ", ".join(m for m, _ in models.most_common()),
        "techniques": [t for t, _ in techniques.most_common()],
        "changed": bool(techniques),
        "detail": step_label,
        "stats": {"calls_per_task": calls / ntasks, "latency_s": mean(lat) if lat else None,
                  "tokens_per_task": (tin + tout) / ntasks, "cost_per_task_usd": cost / ntasks},
    })
    edges = [{"source": "in", "target": "agent", "label": "message"}]
    for name, count in sorted(tools.items(), key=lambda kv: -kv[1]):
        if kind == "retail":
            effect = "read" if name in _RETAIL_READS else effects.classify(name, "", label_map)[0]
            if name not in _RETAIL_READS and effect in ("pure", "read"):
                effect = "write"
        else:
            effect = effects.classify(name, "", label_map)[0]
        nodes.append({"id": "t:" + name, "label": name, "kind": "tool", "effect": effect,
                      "stats": {"calls_per_task": count / ntasks}})
        edges.append({"source": "agent", "target": "t:" + name, "label": f"{count / ntasks:.1f}/task"})
        edges.append({"source": "t:" + name, "target": "agent", "label": "result"})
    nodes.append({"id": "out", "label": "reply" if kind == "retail" else "answer", "kind": "io", "effect": "pure", "stats": None})
    edges.append({"source": "agent", "target": "out", "label": ""})
    return {"nodes": nodes, "edges": edges, "regions": []}


def _actions(task_run: dict) -> list[str]:
    """The state-changing tools the agent asked for, in order (names only; arguments are not recorded)."""
    return [n for rec in task_run["records"] for n in rec.get("tool_calls") or [] if n not in _RETAIL_READS]


def _call_tasks(rep: dict, verdict: dict | None, kind: str,
                labels: tuple[str, str, str] = ("test unchanged 1", "test unchanged 2", "test optimized")) -> list[dict]:
    b1, b2 = rep["runs"][labels[0]], rep["runs"][labels[1]]
    opt = rep["runs"].get(labels[2]) or {}
    v = verdict or {}
    rows = []
    for tid in b1:
        r1, r2, ro = b1[tid], b2.get(tid, {}), opt.get(tid, {})
        if r1.get("crashed") or r2.get("crashed") or ro.get("crashed"):
            status = "crashed"
        elif tid in v.get("broken", []):
            status = "broken"
        elif tid in v.get("fixed", []):
            status = "fixed"
        elif tid in v.get("flaky", []) or tid in v.get("unstable", []):
            status = "unstable"
        else:
            status = "unchanged"

        def cost(r):
            return sum(rec.get("cost_usd") or 0.0 for rec in r.get("records", [])) if r else None

        row = {
            "id": tid,
            "status": status,
            "before": [r1.get("passed"), r2.get("passed")],
            "after": ro.get("passed"),
            "before_wall_s": mean([x["wall_s"] for x in (r1, r2) if x.get("wall_s") is not None]) if r2 else r1.get("wall_s"),
            "after_wall_s": ro.get("wall_s"),
            "before_cost_usd": mean([cost(r1), cost(r2)]) if r2 else cost(r1),
            "after_cost_usd": cost(ro),
            "before_calls": len(r1.get("records", [])),
            "after_calls": len(ro.get("records", [])),
            "answer_before": r1.get("answer"),
            "answer_after": ro.get("answer"),
            "error": r1.get("error") or r2.get("error") or ro.get("error"),
        }
        if kind == "retail":
            row["actions_before"] = [_actions(r1), _actions(r2)]
            row["actions_after"] = _actions(ro) if ro else None
        rows.append(row)
    return rows


def _call_result(kind: str, rel: str) -> dict:
    rep = _load(rel)
    ev = rep["evaluation"]
    v = ev["verdict"]
    b1, b2, o = ev["baseline_1"], ev["baseline_2"], ev["optimized"]
    agent = rep["agent"]

    def avg(key):
        vals = [x.get(key) for x in (b1, b2)]
        return mean(vals) if all(x is not None for x in vals) else None

    per_task = {k: (avg(k) / b1["tasks"] if avg(k) is not None else None) for k in ("tokens_in", "tokens_out", "reasoning_tokens")}
    o_task = {k: (o[k] / o["tasks"] if o and o.get(k) is not None else None) for k in per_task}
    billed = rep["provider"] == "zerog"
    labels = {}
    if agent == "retail":
        from synapse0g.agents.retail import LABELS as labels
    tasks = _call_tasks(rep, v, agent)
    counts = Counter(t["status"] for t in tasks)
    metrics = [
        _metric("Cost per task", "usd", avg("cost_per_task_usd"), o and o["cost_per_task_usd"],
                ci=v and v.get("cost_ci"),
                note="0G's own billed amount per request" if billed else "measured tokens x Groq's listed price (Groq reports no bill)"),
        _metric("Time per task (mean)", "s", avg("mean_wall_s"), o and o["mean_wall_s"], ci=v and v.get("latency_ci")),
        _metric("Time per task, excl. rate-limit waits", "s", avg("mean_wall_excl_waits_s"), o and o["mean_wall_excl_waits_s"]),
        _metric("Tokens per task", "tokens", (per_task["tokens_in"] or 0) + (per_task["tokens_out"] or 0),
                (o_task["tokens_in"] or 0) + (o_task["tokens_out"] or 0)),
        _metric("Hidden reasoning tokens per task", "tokens", per_task["reasoning_tokens"], o_task["reasoning_tokens"]),
        _metric("Model calls per task", "calls", avg("model_calls") / b1["tasks"], o and o["model_calls"] / o["tasks"]),
        _metric("Tasks passed", "tasks", avg("passed"), o and o["passed"], lower_is_better=False),
        _metric("Crashed runs", "tasks", avg("crashed"), o and o["crashed"]),
    ]
    # the firewall's time interval is computed with rate-limit waits removed, so it belongs on that row
    if metrics[1]["before"] == metrics[2]["before"] and metrics[1]["after"] == metrics[2]["after"]:
        del metrics[2]  # no rate-limit waits in this run: the second time row would repeat the first
    else:
        metrics[2]["ci"], metrics[1]["ci"] = metrics[1]["ci"], None
    if agent == "retail":
        metrics.insert(5, _metric("Irreversible tool calls (all tasks)", "calls", avg("protected_actions"), o and o["protected_actions"],
                                  note="refunds, exchanges, cancellations, order and address changes"))
    s = rep["search"]
    search = {
        "trials": [{
            "technique": t["candidate"]["technique"], "why": t["candidate"]["why"],
            "predicted_usd": t["candidate"]["predicted_usd"], "measured_usd": t["measured_usd"],
            "approve": t["verdict"]["approve"], "reasons": t["verdict"]["reasons"],
            "passes": t["verdict"]["passes"], "cost_gain": t["verdict"]["cost_gain"],
            "latency_gain": t["verdict"]["latency_gain"], "broken": t["verdict"]["broken"],
        } for t in s["trials"]],
        "skipped": [{"technique": c["technique"], "why": c["why"], "predicted_usd": c["predicted_usd"]} for c in s["skipped"]],
        "policy": [x.split(": ", 1)[-1] for x in s["policy_described"]],
        "tuning_tasks": rep["calib_n"],
    }
    before_labels = ["test unchanged 1", "test unchanged 2"]
    after_labels = ["test optimized"] if "test optimized" in rep["runs"] else before_labels
    title = {"math": "Maths agent on Groq", "retail": "Retail support agent on 0G", "research": "Research agent on 0G"}[agent]
    subtitle = {"math": "LangGraph ReAct agent with a calculator (GSM8K)",
                "retail": "tau-bench retail: returns, exchanges, cancellations, judged by the final database",
                "research": "LangGraph ReAct agent with search (HotpotQA)"}[agent]
    cost = metrics[0]
    headline = ({"value": cost["change"], "unit": "%", "label": "cheaper per task"} if kind == "cheaper"
                else {"value": counts["broken"], "unit": "tasks", "label": "broken by the cheaper policy: blocked"})
    billing = {"source": "billed" if billed else "listed",
               "explain": ("Cost is 0G's own billed amount for each request (x_0g_trace.billing), summed."
                           if billed else "Groq does not report a bill; cost is the tokens Groq reported times its listed price."),
               "spent_usd": rep["spend"]["spent_usd"], "calls": rep["spend"]["calls"]}
    if billed:
        recs = [rec for lb in before_labels + after_labels for t in rep["runs"][lb].values() for rec in t["records"]]
        billing["billed_usd"] = sum(r.get("billed_usd") or 0 for r in recs)
        billing["listed_usd"] = sum(r.get("listed_cost_usd") or 0 for r in recs)
        billing["requests_with_bill"] = sum(1 for r in recs if r.get("billed_usd") is not None)
        billing["requests"] = len(recs)
    return {
        "id": f"{agent}-{rep['provider']}",
        "claim": kind,
        "layer": "call",
        "title": title,
        "subtitle": subtitle,
        "provider": PROVIDER_NAMES.get(rep["provider"], rep["provider"]),
        "model": rep["prices"]["models"][rep["model"]]["tag"],
        "tasks": rep["test_n"],
        "split": "held-out",
        "headline": headline,
        "verdict": {
            "decision": "APPROVE" if v and v["approve"] else ("BLOCK" if v else "NO CHANGE"),
            # held-out tasks run once per arm, so the report's "failed twice" reads "failed" here
            "reasons": [x.replace("failed twice", "failed under the optimized policy") for x in (v or {}).get("reasons") or []] or (["No regressions; cost improved with 95% confidence"] if v and v["approve"] else []),
            "counts": {"fixed": counts["fixed"], "unchanged": counts["unchanged"] + counts["unstable"],
                       "broken": counts["broken"], "crashed": counts["crashed"]},
            "passes": {"before": avg("passed"), "after": o and o["passed"]},
            "outcome": "The original agent stays deployed." if v and not v["approve"] else "The optimized policy is approved.",
        },
        "metrics": metrics,
        "billing": billing,
        "graph": {"before": _agent_graph(rep, before_labels, labels, agent),
                  "after": _agent_graph(rep, after_labels, labels, agent)},
        "transforms": [{"pass": t["technique"], "applied": t["approve"],
                        "changes": [t["why"]], "findings": t["reasons"], "note": None} for t in search["trials"]],
        "policy": search["policy"],
        "task_rows": tasks,
        "search": search,
        "report": "reports/" + rel,
    }


# ---- the supplier-check app: compiled by synapse0g, both layers, on 0G --------------------
SUPPLIER_ARMS = ("test as written 1", "test as written 2", "test optimized")
HAND = "test hand-parallel LangGraph"


def latest_supplier() -> str | None:
    """The newest supplier study whose held-out stage finished."""
    for p in sorted(REPORTS.glob("supplier_*.json"), reverse=True):
        d = json.loads(p.read_text(encoding="utf-8"))
        if (d.get("evaluation") or {}).get("verdict"):
            return p.relative_to(REPORTS).as_posix()
    return None


def _supplier_result(rel: str) -> dict:
    rep = _load(rel)
    ev = rep["evaluation"]
    v, b1, b2, o, hand = ev["verdict"], ev["baseline_1"], ev["baseline_2"], ev["optimized"], ev["hand_parallel"]
    runs = rep["runs"]

    def avg(key):
        return mean([b1[key], b2[key]]) if b1.get(key) is not None and b2.get(key) is not None else None

    # speed: per task, the mean of the two as-written runs over the optimized run
    ratios = [mean([runs[SUPPLIER_ARMS[0]][t]["wall_s"], runs[SUPPLIER_ARMS[1]][t]["wall_s"]]) / r["wall_s"]
              for t, r in runs[SUPPLIER_ARMS[2]].items() if not r["crashed"] and r["wall_s"]]
    hand_ratios = [mean([runs[SUPPLIER_ARMS[0]][t]["wall_s"], runs[SUPPLIER_ARMS[1]][t]["wall_s"]]) / r["wall_s"]
                   for t, r in runs[HAND].items() if not r["crashed"] and r["wall_s"]]
    n = rep["test_n"]
    tasks = _call_tasks(rep, v, "supplier", SUPPLIER_ARMS)
    counts = Counter(t["status"] for t in tasks)
    executed = {lb: sum(1 for r in runs[lb].values() if r["extra"].get("order_executed")) for lb in (*SUPPLIER_ARMS, HAND)}
    metrics = [
        _metric("Time per task (median)", "s", avg("median_wall_s"), o["median_wall_s"]),
        _metric("Time per task (mean)", "s", avg("mean_wall_s"), o["mean_wall_s"], ci=v.get("latency_ci")),
        _metric("Cost per task", "usd", avg("cost_per_task_usd"), o["cost_per_task_usd"], ci=v.get("cost_ci"),
                note="0G's own billed amount per request"),
        _metric("Tokens per task", "tokens", (avg("tokens_in") + avg("tokens_out")) / n, (o["tokens_in"] + o["tokens_out"]) / n),
        _metric("Model calls per task", "calls", avg("model_calls") / n, o["model_calls"] / n),
        _metric("Tasks passed", "tasks", avg("passed"), o["passed"], lower_is_better=False),
        _metric("Crashed runs", "tasks", avg("crashed"), o["crashed"]),
    ]
    arms = [
        {"label": "As written (LangGraph runs the chain)", "median_wall_s": avg("median_wall_s"), "cost_per_task_usd": avg("cost_per_task_usd"),
         "passed": avg("passed"), "calls_per_task": avg("model_calls") / n, "speedup": 1.0, "orders_executed": executed[SUPPLIER_ARMS[0]],
         "note": "the developer's code, unchanged; its orders go to the test marketplace"},
        {"label": "Hand-wired fan-out in LangGraph", "median_wall_s": hand["median_wall_s"], "cost_per_task_usd": hand["cost_per_task_usd"],
         "passed": hand["passed"], "calls_per_task": hand["model_calls"] / n, "speedup": median(hand_ratios) if hand_ratios else None,
         "orders_executed": executed[HAND],
         "note": "what LangGraph alone gets when an expert already knows which steps are independent"},
        {"label": "Compiled by synapse0g", "median_wall_s": o["median_wall_s"], "cost_per_task_usd": o["cost_per_task_usd"],
         "passed": o["passed"], "calls_per_task": o["model_calls"] / n, "speedup": median(ratios) if ratios else None,
         "orders_executed": executed[SUPPLIER_ARMS[2]],
         "note": "found by the compiler from measured reads and writes; checked by the firewall; orders never placed in a test"},
    ]
    s = rep["search"]
    trials = [{
        "technique": t["candidate"]["technique"], "step": rep["steps"].get(t["candidate"]["step"], t["candidate"]["step"]),
        "why": t["candidate"]["why"], "predicted_usd": t["candidate"]["predicted_usd"], "measured_usd": t["measured_usd"],
        "approve": t["verdict"]["approve"], "reasons": t["verdict"]["reasons"], "passes": t["verdict"]["passes"],
        "cost_gain": t["verdict"]["cost_gain"], "latency_gain": t["verdict"]["latency_gain"], "broken": t["verdict"]["broken"],
    } for t in s["trials"]]
    g = rep["graph_stage"]["verdict"]
    recs = [rec for lb in SUPPLIER_ARMS for t in runs[lb].values() for rec in t["records"]]
    comp = rep["compile"]
    return {
        "id": "supplier-0g",
        "claim": "app",
        "layer": "app",
        "title": "Supplier-check agent on 0G",
        "subtitle": "An ordinary LangGraph app: read the policy, look up quotes, reviews and certificates, choose, place the order",
        "provider": "0G Compute",
        "model": rep["prices"]["models"][rep["model"]]["tag"],
        "tasks": n,
        "split": "held-out",
        "headline": {"value": median(ratios) if ratios else None, "unit": "x", "label": "faster (median per task)"},
        "verdict": {
            "decision": "APPROVE" if v["approve"] else "BLOCK",
            "reasons": v["reasons"] or ["No task broke; time and cost improved with 95% confidence"],
            "counts": {"fixed": counts["fixed"], "unchanged": counts["unchanged"] + counts["unstable"],
                       "broken": counts["broken"], "crashed": counts["crashed"]},
            "passes": {"before": avg("passed"), "after": o["passed"]},
            "outcome": "The compiled app is approved." if v["approve"] else "The app stays as written.",
        },
        "metrics": metrics,
        "arms": arms,
        "graph_stage": {"approve": g["approve"], "passes": g["passes"], "latency_gain": g["latency_gain"], "cost_gain": g["cost_gain"],
                        "reasons": g["reasons"], "tasks": rep["calib_n"]},
        "sir": {"before": _ui(comp["before"]), "after": _ui(comp["after"]), "passes": comp["passes"],
                "as_written": comp["before"]["meta"].get("declared_order") or []},
        "integration": ['from synapse0g.openai import OpenAI          # was: from openai import OpenAI',
                        'app = synapse0g.compile(builder, sample=request, effects=EFFECTS)   # was: builder.compile()',
                        'state = app.invoke(request)'],
        "search": {"trials": trials, "policy": [x.split(": ", 1)[-1] for x in s["policy_described"]], "tuning_tasks": rep["calib_n"],
                   "skipped": [{"technique": c["technique"], "why": c["why"], "predicted_usd": c["predicted_usd"]} for c in s["skipped"]]},
        "billing": {"source": "billed", "explain": "Cost is 0G's own billed amount for each request (x_0g_trace.billing), summed.",
                    "spent_usd": rep["spend"]["spent_usd"], "calls": rep["spend"]["calls"],
                    "billed_usd": sum(r.get("billed_usd") or 0 for r in recs), "listed_usd": sum(r.get("listed_cost_usd") or 0 for r in recs),
                    "requests_with_bill": sum(1 for r in recs if r.get("billed_usd") is not None), "requests": len(recs)},
        "task_rows": tasks,
        "report": "reports/" + rel,
    }


def _ui(graph_json: dict) -> dict:
    from synapse0g.graph.core.sir import Graph

    return Graph.model_validate(graph_json).to_ui()


def featured() -> list[dict]:
    out = []
    hero = latest_supplier()
    if hero:
        out.append(_supplier_result(hero))
    for kind, layer, rel in FEATURED:
        out.append(_graph_result(kind, rel) if layer == "graph" else _call_result(kind, rel))
    return out


def history() -> list[dict]:
    """Every saved run, newest first, with its outcome in one line. Invalid runs are listed as such."""
    rows = []
    for p in sorted(REPORTS.rglob("*.json"), reverse=True):
        rel = p.relative_to(REPORTS).as_posix()
        if rel.endswith("_graphs.json"):
            continue
        d = json.loads(p.read_text(encoding="utf-8"))
        invalid = rel.startswith("invalid/")
        if "evaluation" in d:
            v = d["evaluation"]["verdict"]
            o = d["evaluation"]["optimized"]
            outcome = "no beneficial change" if v is None else ("APPROVE" if v["approve"] else "BLOCK")
            rows.append({
                "file": "reports/" + rel, "layer": "call", "agent": d["agent"], "provider": PROVIDER_NAMES.get(d["provider"], d["provider"]),
                "model": d["model"], "started_at": d["started_at"], "tasks": d["test_n"], "outcome": outcome, "invalid": invalid,
                "policy": [x.split(": ", 1)[-1] for x in d["search"]["policy_described"]], "cost_change": v and v["cost_gain"], "time_change": v and v["latency_gain"],
                "passes": v and v["passes"], "crashed": (o or d["evaluation"]["baseline_1"]).get("crashed"),
                "spent_usd": d["spend"]["spent_usd"], "split": "test",
            })
        elif "verdict" in d:
            v, m = d["verdict"], d["meta"]
            rows.append({
                "file": "reports/" + rel, "layer": "graph", "agent": m["workflow"], "provider": "0G Compute", "model": m["model"],
                "started_at": None, "tasks": v["n_tasks"], "outcome": v["decision"], "invalid": False,
                "policy": [m["pipeline"]], "cost_change": -v["d_cost_pct"] / 100 if v.get("d_cost_pct") is not None else None,
                "time_change": -v["d_latency_pct"] / 100 if v.get("d_latency_pct") is not None else None,
                "passes": [round(v["accuracy_before"] * v["n_tasks"]), round(v["accuracy_after"] * v["n_tasks"])] if v.get("accuracy_before") is not None else None,
                "crashed": v["crashed"], "spent_usd": d["ledger"]["spent"]["cost_usd"],
                "split": m["split"],
            })
    return rows


def observed_runs() -> list[dict]:
    """Runs recorded by `synapse0g.wrap`, newest first, one line each."""
    rows = []
    for p in sorted((REPORTS / "observed").glob("*.json"), reverse=True):
        d = json.loads(p.read_text(encoding="utf-8"))
        applied = [x["pass_name"] for x in d["passes"] if x["applied"]]
        rows.append({"run_id": d["run_id"], "workflow": d["workflow"], "saved_at": d["saved_at"], "calls": len(d["calls"]),
                     "repeated_calls": d["repeated_calls"], "applied": applied,
                     "tokens": sum((c["tokens_in"] or 0) + (c["tokens_out"] or 0) for c in d["calls"]),
                     "wall_ms": sum(c["latency_ms"] for c in d["calls"])})
    return rows


def observed_run(run_id: str) -> dict | None:
    if not run_id.replace("-", "").replace("_", "").isalnum():
        return None
    p = REPORTS / "observed" / f"{run_id}.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else None


def feed() -> dict:
    return {"featured": featured(), "history": history(), "workflows": workflows()}


def _cli() -> None:
    print(json.dumps(feed(), indent=1, default=str)[:4000])


if __name__ == "__main__":
    _cli()

