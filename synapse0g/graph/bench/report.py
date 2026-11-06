"""The numbers table (docs/BENCHMARKS.md §7 and §8).

    py -3 -m bench.report                  # every run in bench/reports/
    py -3 -m bench.report --split test     # only the numbers we may quote

Writes bench/reports/latest.md and latest.json. Every number is computed here from the rows
bench/suite.py wrote. What cannot be computed is listed as absent, never filled in.

The rules this enforces: cold and warm are separate rows, the accuracy delta sits beside
every saving, crashes are reported and never dropped, None prints as a dash, sample counts
are on every aggregate, and a loss is shown. Anything not on `test` carries a banner.

Confidence intervals are ported from Black Box `eval/metrics.py`.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from datetime import UTC, datetime
from pathlib import Path
from statistics import mean, median
from typing import Any

REPORT_DIR = Path(__file__).parent / "reports"
SMALL_SAMPLE = 20  # a p95 from fewer executions than this is not a p95, and says so
OUTPUT_NAMES = ("latest.md", "latest.json")
MODE_LABELS = {"sequential": "sequential (cold)", "optimized": "synapse optimized (cold)",
               "cache_only_cold": "cache-only (cold)", "cache_only_warm": "cache-only (warm)",
               "sequential_again": "sequential, repeated (cold)"}


def bootstrap_ci(values: list[float], reps: int = 1000, seed: int = 0) -> list[float] | None:
    """95 % interval of the mean by resampling tasks. None below 2 tasks."""
    if len(values) < 2:
        return None
    rng = random.Random(seed)
    means = sorted(mean(rng.choices(values, k=len(values))) for _ in range(reps))
    return [round(means[int(0.025 * reps)], 4), round(means[int(0.975 * reps) - 1], 4)]


def p95(values: list[float]) -> float:
    ranked = sorted(values)
    return ranked[min(len(ranked) - 1, int(0.95 * len(ranked)))]


def total_or_none(values: list[Any]) -> Any:
    """The median of what was reported, or None if any execution did not report."""
    return None if any(v is None for v in values) else median(values)


def aggregate(rows: list[dict]) -> dict[str, Any]:
    done = [r for r in rows if not r["crashed"]]
    out: dict[str, Any] = {"n": len(done), "crashed": len(rows) - len(done)}
    if not done:
        return out
    walls = [r["wall_ms"] / 1000 for r in done]
    passed = [float(r["passed"]) for r in done]
    out.update(wall_p50_s=median(walls), wall_p95_s=p95(walls), tokens_in=total_or_none([r["tokens_in"] for r in done]),
               tokens_out=total_or_none([r["tokens_out"] for r in done]),
               cost_usd=total_or_none([r.get("cost_usd") for r in done]),
               calls=median(r["provider_calls"] for r in done),
               cache_hits=median(r["cache_hits"] for r in done), accuracy=mean(passed),
               accuracy_ci95=bootstrap_ci(passed))
    return out


def percent_change(after: Any, before: Any) -> float | None:
    if after is None or before in (None, 0):
        return None
    return 100 * (after - before) / before


def compare(base: list[dict], other: list[dict]) -> dict[str, Any]:
    """Paired on tasks where neither execution crashed, so a crash cannot flatter a mode."""
    pairs = [(a, b) for a, b in zip(base, other, strict=True) if not a["crashed"] and not b["crashed"]]
    if not pairs:
        return {"n_paired": 0}
    first = aggregate([a for a, _ in pairs])
    second = aggregate([b for _, b in pairs])
    return {"n_paired": len(pairs), "speedup": median(a["wall_ms"] / b["wall_ms"] for a, b in pairs),
            "d_tokens_pct": percent_change(second["tokens_in"], first["tokens_in"]),
            "d_cost_pct": percent_change(second["cost_usd"], first["cost_usd"]),
            "d_accuracy_pp": 100 * (second["accuracy"] - first["accuracy"])}


def fmt(value: Any, spec: str = "") -> str:
    """None prints as a dash, never as zero."""
    return "—" if value is None else format(value, spec)


def signed(value: float | None, suffix: str) -> str:
    return "—" if value is None else f"{value:+.1f}{suffix}"


def ci_text(ci: list[float] | None) -> str:
    return "—" if ci is None else f"{ci[0]:.2f}–{ci[1]:.2f}"


def build(runs: list[dict], ablations: list[dict] | None = None) -> dict[str, Any]:
    blocks = []
    for run in runs:
        rows = run["rows"]
        blocks.append({"meta": run["meta"], "verdict": run.get("verdict"), "repeat_flips": run.get("repeat_flips"),
                       "waits": run.get("waits"),
                       "at_fault": [t["task_id"] for t in run.get("per_task", [])
                                    if t["status"] in ("broken", "crashed")],
                       "modes": {name: aggregate(r) for name, r in rows.items()},
                       "vs_sequential": {name: compare(rows["sequential"], r) for name, r in rows.items()
                                         if name != "sequential"}})
    absent = {"framework-default baseline": "not built"}
    if any(b["verdict"] is None for b in blocks):
        absent["firewall verdict"] = "a run here has none, so it is a draft (CLAUDE.md rule 4)"
    return {"workflows": blocks, "ablations": ablations or [], "absent": absent}


LABELS = {"sequential": ("original (sequential)", True), "optimized": ("synapse optimized", True),
          "cache_only_cold": ("cache-only baseline", True), "cache_only_warm": ("cache-only baseline", False),
          "sequential_again": ("original, run again", True)}


def plain_reason(reason: str | None) -> str | None:
    """Saved verdicts keep the firewall's old wording, which called these breaks model noise. That
    was only a wall-time heuristic: `rag_qa` lost 7 passes, and the screen must not excuse it."""
    if reason is None:
        return None
    return (reason.replace("broke with model-noise signature", "passed before and fail now")
            .replace("; consider increasing repeats or fixing the seed", ""))


def to_ui(runs: list[dict], chaos: list[dict] | None = None,ablations: list[dict] | None = None) -> dict[str, Any]:
    """The dashboard's shape (docs/API_FOR_UI.md section 7). Every number comes from a recorded
    run, a value nobody reported stays None, and a run off `test` says it is not reportable."""
    built = build(runs)["workflows"]
    workflows = []
    for block in built:
        verdict = block["verdict"] or {}
        rows = [
            {"variant": LABELS.get(name, (name, True))[0], "cold": LABELS.get(name, (name, True))[1],
             "samples": m.get("n"), "crashed": m.get("crashed"),
             "wall_p50_ms": m["wall_p50_s"] * 1000 if "wall_p50_s" in m else None,
             "wall_p95_ms": m["wall_p95_s"] * 1000 if "wall_p95_s" in m else None,
             "tokens": m.get("tokens_in"), "cost_usd": m.get("cost_usd"), "accuracy": m.get("accuracy"),
             "provider_calls": m.get("calls"), "cache_hits": m.get("cache_hits")}
            for name, m in block["modes"].items()
        ]
        pipeline = block["meta"].get("pipeline") or ""
        workflows.append({
            "name": block["meta"]["workflow"], "split": block["meta"]["split"], "model": block["meta"].get("model"),
            "n_tasks": rows[0]["samples"] if rows else 0, "relation": block["meta"].get("relation"),
            "noise_band_pct": verdict.get("noise_band_pct"),
            "pipeline": [p for p in pipeline.split(" -> ") if p and p != "nothing applied"],
            "verdict": verdict.get("decision", "NONE"), "verdict_reason": plain_reason(verdict.get("reason")),
            "reportable": bool(verdict.get("reportable")), "repeat_flips": block.get("repeat_flips"), "rows": rows,
        })
    splits = sorted({w["split"] for w in workflows})
    return {"generated_at": datetime.now(UTC).isoformat(), "split": ", ".join(splits), "workflows": workflows,
            "chaos": chaos or [], "ablations": ablations or []}


def effect_text(effect: dict[str, Any] | None) -> str:
    if effect is None or effect["slower_pct"] is None:
        return "—"
    noise = {True: ", inside noise", False: ", outside noise", None: ""}[effect["within_noise"]]
    return f"{effect['slower_pct']:+.1f}% wall, {signed(effect['d_tokens_pct'], '%')} tokens{noise}"


def ablation_lines(study: dict[str, Any]) -> list[str]:
    """One study as markdown. Effect is against the full pipeline, so + means the pass was helping."""
    meta = study["meta"]
    lines = [f"## Ablation: {meta['workflow']}", "",
             f"split `{meta['split']}`, model `{meta['model']}`, {meta['n_tasks']} tasks, "
             f"relation `{meta['relation']}`, noise band ±{fmt(study['noise_band_pct'], '.1f')}%", "",
             f"**NOT A HEADLINE NUMBER: split is `{meta['split']}`. This says which pass earns the gain.**", "",
             "| pass removed | wall p50 | speedup | tokens in | cost USD | passed | verdict | removing it costs |",
             "|---|---|---|---|---|---|---|---|"]
    for row in study["rows"]:
        wall = None if row["wall_p50_ms"] is None else row["wall_p50_ms"] / 1000
        speedup = "—" if row["speedup"] is None else f"{row['speedup']:.2f}x"
        verdict = "—" if row["verdict"] is None else f"{row['verdict']} ({row['broken']} broke, {row['fixed']} fixed)"
        lines.append(f"| {row['removed']} | {fmt(wall, '.2f')} s | {speedup} | {fmt(row['tokens_in'], '.0f')} "
                     f"| {fmt(row.get('cost_usd'), '.6f')} | {row['passed']}/{row['samples']} | {verdict} "
                     f"| {effect_text(row['effect'])} |")
    if study.get("repeat_flips"):
        flips = study["repeat_flips"]
        lines += ["", f"unmodified graph, run again: {flips['broke']} broke, {flips['fixed']} fixed, "
                      f"of {flips['tasks']} tasks"]
    return [*lines, ""]


def to_markdown(report: dict[str, Any]) -> str:
    lines = ["# Synapse benchmark report", ""]
    for block in report["workflows"]:
        meta = block["meta"]
        lines += [f"## {meta['workflow']}", "",
                  f"split `{meta['split']}`, model `{meta['model']}`, relation `{meta['relation']}`, "
                  f"pipeline `{meta['pipeline']}`", ""]
        if meta["split"] != "test":
            lines += [f"**NOT A HEADLINE NUMBER: split is `{meta['split']}`, not `test`.**", ""]
        if meta.get("label"):
            lines += [f"**Labelled run: {meta['label']}. These tasks were looked at before.**", ""]
        if block["verdict"] is not None:
            v = block["verdict"]
            lines += [f"**firewall: {v['decision']}** (relation `{v.get('relation')}`, split `{v.get('split')}`, "
                      f"{v.get('condition') or 'condition not recorded'}) — {v['reason']}", "",
                      f"unchanged {v['unchanged']}, fixed {v['fixed']}, broken {v['broken']} "
                      f"({v['stochastic_breaks']} with no structural sign), crashed {v['crashed']}; "
                      f"noise band ±{fmt(v['noise_band_pct'], '.1f')}%", ""]
            if block.get("repeat_flips"):
                f = block["repeat_flips"]
                lines += [f"unmodified graph, run again: {f['broke']} broke, {f['fixed']} fixed, of {f['tasks']} tasks",
                          ""]
            for name, wait in (block.get("waits") or {}).items():
                if wait["retries"]:
                    lines += [f"provider back-off in `{name}`: {wait['retries']} retries, {wait['slept_s']:.1f} s "
                              f"asleep, {wait['runs_hit']} runs. That time is inside its wall numbers.", ""]
            if block["at_fault"]:
                lines += ["broken or crashed: " + ", ".join(f"`{t}`" for t in block["at_fault"]), ""]
        lines += ["| mode | n | crashed | wall p50 | wall p95 | tokens in | tokens out | cost USD | calls | hits "
                  "| accuracy | 95% CI |", "|---|---|---|---|---|---|---|---|---|---|---|---|"]
        for name, m in block["modes"].items():
            label = MODE_LABELS.get(name, name)
            if not m["n"]:
                lines.append(f"| {label} | 0 | {m['crashed']} | — | — | — | — | — | — | — | — | — |")
                continue
            weak = "*" if m["n"] < SMALL_SAMPLE else ""
            lines.append(f"| {label} | {m['n']} | {m['crashed']} | {m['wall_p50_s']:.2f} s "
                         f"| {m['wall_p95_s']:.2f} s{weak} | {fmt(m['tokens_in'], '.0f')} "
                         f"| {fmt(m['tokens_out'], '.0f')} | {fmt(m['cost_usd'], '.4f')} "
                         f"| {m['calls']:.0f} | {m['cache_hits']:.0f} | {m['accuracy']:.2f} "
                         f"| {ci_text(m['accuracy_ci95'])} |")
        lines += ["", f"\\* p95 from fewer than {SMALL_SAMPLE} executions. Tokens and calls are per-task medians.", "",
                  "| vs sequential | paired tasks | speedup | tokens | cost | accuracy |", "|---|---|---|---|---|---|"]
        for name, c in block["vs_sequential"].items():
            label = MODE_LABELS.get(name, name)
            if not c["n_paired"]:
                lines.append(f"| {label} | 0 | — | — | — | — |")
                continue
            lines.append(f"| {label} | {c['n_paired']} | {c['speedup']:.2f}x | {signed(c['d_tokens_pct'], '%')} "
                         f"| {signed(c['d_cost_pct'], '%')} | {signed(c['d_accuracy_pp'], ' pp')} |")
        lines.append("")
    for study in report.get("ablations", []):
        lines += ablation_lines(study)
    lines += ["## Not in this report", ""]
    lines += [f"- **{name}**: {reason}" for name, reason in report["absent"].items()]
    return "\n".join(lines) + "\n"


def load_runs(root: Path, split: str | None = None) -> list[dict]:
    runs = []
    for path in sorted(root.glob("*.json")):
        if path.name in OUTPUT_NAMES:
            continue
        run = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(run, dict) or "meta" not in run:
            print(f"skipping {path.name}: not a benchmark run (no meta)", file=sys.stderr)
            continue
        if split is None or run["meta"]["split"] == split:
            runs.append(run)
    return runs


def load_ablations(root: Path) -> list[dict]:
    """Every saved study, without its raw rows. They are never on `test`, so `--split test`
    does not filter them: each one says its split and that it is not a headline number."""
    studies = [json.loads(path.read_text(encoding="utf-8")) for path in sorted((root / "ablation").glob("*.json"))]
    return [{k: v for k, v in study.items() if k != "runs"} for study in studies]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Write bench/reports/latest.md and latest.json.")
    ap.add_argument("--split", choices=["train", "calib", "test"], help="only this split (default: all)")
    ap.add_argument("--out", default=str(REPORT_DIR), help="report directory")
    args = ap.parse_args(argv)
    out = Path(args.out)
    runs = load_runs(out, args.split)
    if not runs:
        print(f"no benchmark runs in {out}. Run: py -3 -m bench.suite", file=sys.stderr)
        return 1
    report = build(runs, load_ablations(out))
    (out / "latest.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    markdown = to_markdown(report)
    (out / "latest.md").write_text(markdown, encoding="utf-8")
    print(markdown)
    print(f"-> {out / 'latest.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
