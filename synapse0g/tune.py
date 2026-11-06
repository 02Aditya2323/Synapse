"""The optimizer's search, and the firewall that decides what ships.

  1. Run the agent unchanged on the tuning tasks, twice. Two runs show which tasks are stable
     and how much cost and time move by chance (the noise band).
  2. Profile each step: tokens, hidden reasoning, old tool results, repeated requests, cost.
  3. Propose techniques per step, each with a predicted saving computed from that profile. A
     technique predicted to save less than `min_gain` of the cost is not tried, and says why.
  4. Try the best-predicted technique on the tuning tasks. The firewall keeps it only if no task
     that passed in both baseline runs now fails (checked twice), the pass count did not drop,
     and cost or time improved by more than the noise band. Predicted and measured savings are
     both recorded.
  5. Repeat from the new profile until nothing is left worth trying.
  6. Measure the final policy against the unchanged agent on held-out tasks, which the search
     never saw. Those are the numbers we report, including "no beneficial change".
"""

from __future__ import annotations

import random
import statistics
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from synapse0g.optimizer import Policy
from synapse0g.proxy import Engine
from synapse0g.registry import Registry
from synapse0g.tasks import CHECKERS, Task


@dataclass
class TaskRun:
    task_id: str
    passed: bool
    crashed: bool
    error: str | None
    answer: str
    wall_s: float
    records: list[dict]
    extra: dict = field(default_factory=dict)

    @property
    def cost(self) -> float:
        return sum(r["cost_usd"] or 0.0 for r in self.records)


@dataclass
class Batch:
    label: str
    policy: dict
    runs: dict[str, TaskRun]

    def total(self, field_: str) -> float:
        return sum(r.get(field_) or 0 for run in self.runs.values() for r in run.records)

    def summary(self) -> dict:
        runs = list(self.runs.values())
        n = len(runs) or 1
        passes = sum(r.passed for r in runs)
        cost = sum(r.cost for r in runs)
        billed = self.total("billed_usd")
        calls = [r for run in runs for r in run.records]
        return {
            "label": self.label,
            "tasks": len(runs),
            "passed": passes,
            "crashed": sum(r.crashed for r in runs),
            "model_calls": sum(1 for r in calls if r["status"] == "ok"),
            "reused_calls": sum(1 for r in calls if r["status"] == "reused"),
            "tokens_in": int(self.total("tokens_in")),
            "tokens_out": int(self.total("tokens_out")),
            "reasoning_tokens": int(self.total("reasoning_tokens")),
            "cost_usd": cost,
            "billed_usd": billed,
            "listed_cost_usd": self.total("listed_cost_usd"),
            "cost_per_task_usd": cost / n,
            "cost_per_pass_usd": cost / passes if passes else None,
            "mean_wall_s": statistics.fmean(r.wall_s for r in runs) if runs else 0.0,
            "median_wall_s": statistics.median(r.wall_s for r in runs) if runs else 0.0,
            "retries": int(self.total("retries")),
            "protected_actions": sum(r.extra.get("protected_actions", 0) for r in runs),
            "harness_cost_usd": sum(r.extra.get("user_sim_cost_usd", 0.0) for r in runs),
            # wall time minus rate-limit back-off: what the run takes when the provider is not throttling
            "mean_wall_excl_waits_s": statistics.fmean(
                max(r.wall_s - sum(x.get("waited_s") or 0 for x in r.records), 0.0) for r in runs
            ) if runs else 0.0,
        }


def run_batches(
    engine: Engine, base_url: str, agent, model: str, tasks: list[Task], arms: list[tuple], workers: int
) -> list[Batch]:
    """Run several policies over the same tasks in one pool, alternating task by task.

    Arms that run together see the same provider conditions, so a provider that slows down
    for ten minutes slows every arm equally instead of making one look worse.
    """
    check = CHECKERS[tasks[0].kind]

    def one(job: tuple[int, Task]) -> tuple[int, TaskRun]:
        arm, task = job
        session = engine.open(arms[arm][1])
        run_agent = arms[arm][2] if len(arms[arm]) > 2 else agent  # an arm may bring its own agent
        try:
            res = run_agent(task, base_url, session.id, model)
        finally:
            records = [r.as_dict() for r in engine.close(session.id).records]
        crashed = res.error is not None
        passed = (not crashed) and check(task, res.answer)
        return arm, TaskRun(task.id, passed, crashed, res.error, res.answer, res.wall_s, records, res.extra)

    jobs = [(a, t) for t in tasks for a in range(len(arms))]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        done = list(pool.map(one, jobs))
    return [
        Batch(arm[0], arm[1].as_dict(), {r.task_id: r for a, r in done if a == i}) for i, arm in enumerate(arms)
    ]


def run_batch(
    engine: Engine, base_url: str, agent, model: str, tasks: list[Task], policy: Policy, label: str, workers: int
) -> Batch:
    return run_batches(engine, base_url, agent, model, tasks, [(label, policy)], workers)[0]


# ---- profile and candidates ---------------------------------------------------------
CACHE_MIN_TOKENS = 1024  # prompts shorter than this were not cached explicitly in our 0G probes


@dataclass
class StepProfile:
    step: str
    label: str
    model_id: str
    calls: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    reasoning: int = 0
    old_tool_chars: int = 0
    cost: float = 0.0
    duplicate_cost: float = 0.0
    repeated_uncached: int = 0  # input tokens repeating the previous turn's prompt, not already cached
    out_tokens: list[int] = field(default_factory=list)


def profile(batch: Batch) -> dict[str, StepProfile]:
    steps: dict[str, StepProfile] = {}
    for run in batch.runs.values():
        seen: Counter = Counter()
        prev_in: dict[str, int] = {}
        for r in run.records:
            if r["status"] != "ok":
                continue
            sp = steps.setdefault(r["step"], StepProfile(r["step"], r["step_label"], r["model_id"]))
            # a loop's next prompt starts with the previous one: that much could be read from cache
            if prev_in.get(r["step"], 0) >= CACHE_MIN_TOKENS:
                sp.repeated_uncached += max(prev_in[r["step"]] - (r["cached_tokens"] or 0), 0)
            prev_in[r["step"]] = r["tokens_in"] or 0
            sp.calls += 1
            sp.tokens_in += r["tokens_in"] or 0
            sp.tokens_out += r["tokens_out"] or 0
            sp.reasoning += r["reasoning_tokens"] or 0
            sp.old_tool_chars += r["old_tool_chars"]
            sp.cost += r["cost_usd"] or 0.0
            sp.out_tokens.append(r["tokens_out"] or 0)
            seen[r["request_hash"]] += 1
            if seen[r["request_hash"]] > 1:
                sp.duplicate_cost += r["cost_usd"] or 0.0
    return steps


@dataclass
class Candidate:
    step: str
    technique: str
    change: dict
    predicted_usd: float
    why: str


def candidates(registry: Registry, prof: dict[str, StepProfile], policy: Policy) -> list[Candidate]:
    out: list[Candidate] = []
    for step, sp in prof.items():
        current = policy.for_step(step)
        model = registry.model(current.route_to or sp.model_id)
        if not current.reasoning_off and model.reasoning_off and sp.reasoning:
            out.append(Candidate(step, "reasoning_off", {"reasoning_off": True}, sp.reasoning * (model.price_out or 0),
                                 f"{sp.reasoning:,} of {sp.tokens_out:,} output tokens were hidden reasoning"))
        if not current.route_to:
            for cheaper_id in registry.model(sp.model_id).cheaper:
                c = registry.model(cheaper_id)
                new_cost = c.cost(sp.tokens_in, sp.tokens_out) or sp.cost
                out.append(Candidate(step, f"route:{cheaper_id}", {"route_to": cheaper_id}, max(sp.cost - new_cost, 0.0),
                                     f"{c.tag} lists ${c.price_in * 1e6:.3f}/${c.price_out * 1e6:.3f} per M tokens "
                                     f"vs {model.tag} ${model.price_in * 1e6:.3f}/${model.price_out * 1e6:.3f}"))
        if not current.cache_marks and model.explicit_cache and model.cached_price_ratio is not None:
            eligible = sp.repeated_uncached
            out.append(Candidate(step, "cache_marks", {"cache_marks": True},
                                 eligible * (model.price_in or 0) * (1 - model.cached_price_ratio),
                                 f"{sp.repeated_uncached:,} input tokens repeated a previous prompt of 1,024+ tokens without a cache hit; "
                                 f"cached tokens bill at {model.cached_price_ratio:.0%} of the input price (measured)"))
        if current.keep_tool_results is None:
            trimmable = max(sp.old_tool_chars - sp.calls * 300, 0) / 4  # ~4 characters per token
            out.append(Candidate(step, "compact:2", {"keep_tool_results": 2}, trimmable * (model.price_in or 0),
                                 f"about {int(trimmable):,} input tokens sit in tool results older than the last two"))
        if not current.reuse:
            out.append(Candidate(step, "reuse", {"reuse": True}, sp.duplicate_cost,
                                 f"identical requests cost ${sp.duplicate_cost:.6f} in the baseline"))
        if not current.max_tokens and sp.out_tokens:
            cap = int(max(sp.out_tokens) * 1.5) + 16
            over = sum(max(0, t - cap) for t in sp.out_tokens)
            out.append(Candidate(step, f"cap:{cap}", {"max_tokens": cap}, over * (model.price_out or 0),
                                 f"longest output was {max(sp.out_tokens)} tokens; a cap above it cuts only runaways"))
    return sorted(out, key=lambda c: -c.predicted_usd)


# ---- firewall -----------------------------------------------------------------------
def _band(a: float, b: float) -> float:
    return abs(a - b) / max(a, b) if max(a, b) > 0 else 0.0


def _gain(base: float, new: float) -> float:
    return (base - new) / base if base > 0 else 0.0


MAX_CRASH_RATE = 0.10  # above this, a run measures the provider, not the agent


def _task_wall(run: TaskRun) -> float:
    """Wall time minus rate-limit back-off, so a throttled provider does not look like a slow agent."""
    return max(run.wall_s - sum(r.get("waited_s") or 0 for r in run.records), 0.0)


def paired_gain(base: list[float], new: list[float], *, resamples: int = 2000, seed: int = 0) -> tuple[float, float, float]:
    """Relative saving sum(base - new) / sum(base) with a 95% bootstrap interval over tasks.

    Paired by task: each task is compared with itself, so an easy task and a hard task do not
    add their difference in difficulty to the noise. The seed is fixed so a verdict reproduces.
    """
    n = len(base)
    if n == 0 or sum(base) <= 0:
        return 0.0, 0.0, 0.0
    point = (sum(base) - sum(new)) / sum(base)
    rng = random.Random(seed)
    stats = []
    for _ in range(resamples):
        idx = [rng.randrange(n) for _ in range(n)]
        sb = sum(base[i] for i in idx)
        stats.append((sb - sum(new[i] for i in idx)) / sb if sb > 0 else 0.0)
    stats.sort()
    return point, stats[int(0.025 * resamples)], stats[int(0.975 * resamples) - 1]


@dataclass
class Verdict:
    approve: bool
    reasons: list[str]
    broken: list[str]
    flaky: list[str]
    fixed: list[str]
    unstable: list[str]
    passes: tuple[int, int, int]
    cost_gain: float
    cost_ci: tuple[float, float]
    latency_gain: float
    latency_ci: tuple[float, float]
    tokens_gain: float
    cost_band: float = 0.0  # |run1 - run2| / max of the two unchanged runs, for reference
    latency_band: float = 0.0
    latency_reference: str = "mean of the two unchanged runs"

    def as_dict(self) -> dict:
        return self.__dict__ | {"passes": list(self.passes), "cost_ci": list(self.cost_ci), "latency_ci": list(self.latency_ci)}


def judge(b1: Batch, b2: Batch, cand: Batch, recheck: Batch | None = None, control: Batch | None = None) -> Verdict:
    """APPROVE only when nothing broke and cost or time improved with 95% confidence.

    - broken: passed in both unchanged runs, failed with the change, and failed again on recheck
    - cost: per-task cost against the mean of every unchanged run of that task
    - time: per-task time against `control`, an unchanged run made alongside the change, when given
    """
    all_ids = list(b1.runs)
    unchanged_arms = [b1, b2] + ([control] if control else [])
    # a crash is not an answer: a task that crashed in an unchanged run cannot be compared
    ids = [t for t in all_ids if not any(b.runs[t].crashed for b in unchanged_arms)]
    stable_pass = [t for t in ids if b1.runs[t].passed and b2.runs[t].passed]
    stable_fail = [t for t in ids if not b1.runs[t].passed and not b2.runs[t].passed]
    unstable = [t for t in ids if b1.runs[t].passed != b2.runs[t].passed]
    first_fail = [t for t in stable_pass if not cand.runs[t].passed]
    if recheck is not None:
        broken = [t for t in first_fail if not recheck.runs[t].passed]
        flaky = [t for t in first_fail if recheck.runs[t].passed]
    else:
        broken, flaky = first_fail, []
    fixed = [t for t in stable_fail if cand.runs[t].passed]
    s1, s2, sc = b1.summary(), b2.summary(), cand.summary()
    unchanged = [b1, b2] + ([control] if control else [])

    # a crashed run stops early and looks cheap: cost and time compare only runs that finished
    done = [t for t in ids if not cand.runs[t].crashed]
    base_cost = [statistics.fmean(b.runs[t].cost for b in unchanged) for t in done]
    cost_gain, *cost_ci = paired_gain(base_cost, [cand.runs[t].cost for t in done])
    if control:
        base_wall = [_task_wall(control.runs[t]) for t in done]
        ref = "an unchanged run made alongside the change"
    else:
        base_wall = [(_task_wall(b1.runs[t]) + _task_wall(b2.runs[t])) / 2 for t in done]
        ref = "mean of the two unchanged runs"
    lat_gain, *lat_ci = paired_gain(base_wall, [_task_wall(cand.runs[t]) for t in done])
    base_tok = (s1["tokens_in"] + s1["tokens_out"] + s2["tokens_in"] + s2["tokens_out"]) / 2

    v = Verdict(
        approve=False, reasons=[], broken=broken, flaky=flaky, fixed=fixed, unstable=unstable,
        passes=(s1["passed"], s2["passed"], sc["passed"]),
        cost_gain=cost_gain, cost_ci=tuple(cost_ci), latency_gain=lat_gain, latency_ci=tuple(lat_ci),
        tokens_gain=_gain(base_tok, sc["tokens_in"] + sc["tokens_out"]),
        cost_band=_band(s1["cost_usd"], s2["cost_usd"]), latency_band=_band(s1["mean_wall_s"], s2["mean_wall_s"]),
        latency_reference=ref,
    )
    rate, worst = max((sum(r.crashed for r in b.runs.values()) / max(len(b.runs), 1), b.label) for b in [*unchanged_arms, cand])
    if rate > MAX_CRASH_RATE:
        v.reasons.insert(0, f"no verdict: {rate:.0%} of runs in '{worst}' crashed. "
                            "Fix what crashed (the errors are in the report) and run again")
        v.approve = False
        return v
    if broken:
        v.reasons.append(f"{len(broken)} task(s) that passed in both unchanged runs failed twice: {', '.join(broken[:5])}")
    p1 = sum(b1.runs[t].passed for t in ids)
    p2 = sum(b2.runs[t].passed for t in ids)
    passes_after_recheck = sum(cand.runs[t].passed for t in ids) + len(flaky)
    if passes_after_recheck < min(p1, p2):
        v.reasons.append(f"passes fell to {passes_after_recheck} from {min(p1, p2)} (of {len(ids)} comparable tasks)")
    if sc["crashed"] > max(s1["crashed"], s2["crashed"]):
        v.reasons.append(f"{sc['crashed']} crashed run(s), unchanged runs had {max(s1['crashed'], s2['crashed'])}")
    cost_win, lat_win = v.cost_ci[0] > 0, v.latency_ci[0] > 0
    if not (cost_win or lat_win):
        v.reasons.append(
            f"no saving with 95% confidence: cost {cost_gain:+.1%} [{v.cost_ci[0]:+.1%}, {v.cost_ci[1]:+.1%}], "
            f"time {lat_gain:+.1%} [{v.latency_ci[0]:+.1%}, {v.latency_ci[1]:+.1%}]"
        )
    if v.cost_ci[1] < 0:
        v.reasons.append(f"cost rose {-cost_gain:.1%} [{v.cost_ci[0]:+.1%}, {v.cost_ci[1]:+.1%}]")
    if v.latency_ci[1] < 0 and lat_gain < -0.10:
        v.reasons.append(f"time rose {-lat_gain:.1%} [{v.latency_ci[0]:+.1%}, {v.latency_ci[1]:+.1%}]")
    v.approve = not v.reasons
    return v


# ---- the search -----------------------------------------------------------------------
@dataclass
class Trial:
    candidate: Candidate
    verdict: Verdict
    measured_usd: float
    summary: dict


@dataclass
class SearchResult:
    policy: Policy
    baseline: tuple[Batch, Batch]
    trials: list[Trial]
    skipped: list[Candidate]
    batches: list[Batch]


def search(
    engine: Engine, base_url: str, agent, model: str, calib: list[Task], *,
    workers: int = 8, min_gain: float = 0.03, max_trials: int = 6, log=print,
) -> SearchResult:
    registry = engine.registry
    b1, b2 = run_batches(engine, base_url, agent, model, calib,
                         [("calib unchanged 1", Policy.empty()), ("calib unchanged 2", Policy.empty())], workers)
    for b in (b1, b2):
        s = b.summary()
        log(f"  {b.label}: {s['passed']}/{s['tasks']} passed, ${s['cost_usd']:.5f}, {s['median_wall_s']:.1f}s median")
    base_cost = (b1.summary()["cost_usd"] + b2.summary()["cost_usd"]) / 2
    policy = Policy.empty()
    current = b1
    tried: set[tuple[str, str]] = set()
    trials: list[Trial] = []
    skipped: dict[tuple[str, str], Candidate] = {}
    batches = [b1, b2]
    while len(trials) < max_trials:
        pool = [c for c in candidates(registry, profile(current), policy) if (c.step, c.technique.split(":")[0]) not in tried]
        worth = [c for c in pool if c.predicted_usd >= min_gain * base_cost]
        for c in pool:
            if c not in worth:
                skipped[(c.step, c.technique)] = c
        if not worth:
            break
        cand = worth[0]
        tried.add((cand.step, cand.technique.split(":")[0]))
        skipped.pop((cand.step, cand.technique), None)
        trial_policy = policy.with_change(cand.step, **cand.change)
        t0 = time.perf_counter()
        # the change and an unchanged control, task by task, so both see the same provider speed
        batch, control = run_batches(engine, base_url, agent, model, calib,
                                     [(f"calib try {cand.technique}", trial_policy), (f"control for {cand.technique}", policy)], workers)
        recheck = None
        first = [t for t in calib if b1.runs[t.id].passed and b2.runs[t.id].passed and not batch.runs[t.id].passed]
        if first:
            recheck = run_batch(engine, base_url, agent, model, first, trial_policy, f"recheck {cand.technique}", workers)
            batches.append(recheck)
            recheck = Batch(recheck.label, recheck.policy, {**batch.runs, **recheck.runs})
        batches += [batch, control]
        verdict = judge(b1, b2, batch, recheck, control)
        measured = control.summary()["cost_usd"] - batch.summary()["cost_usd"]
        trials.append(Trial(cand, verdict, measured, batch.summary()))
        log(
            f"  try {cand.technique} on {cand.step}: predicted -${cand.predicted_usd:.5f}, measured "
            f"{'-' if measured >= 0 else '+'}${abs(measured):.5f} vs control; passes {verdict.passes}; "
            f"cost {verdict.cost_gain:+.1%} [{verdict.cost_ci[0]:+.1%}, {verdict.cost_ci[1]:+.1%}], "
            f"time {verdict.latency_gain:+.1%} [{verdict.latency_ci[0]:+.1%}, {verdict.latency_ci[1]:+.1%}]; "
            f"{'APPROVE' if verdict.approve else 'BLOCK'} ({time.perf_counter() - t0:.0f}s)"
            + ("" if verdict.approve else f": {'; '.join(verdict.reasons)}")
        )
        if verdict.approve:
            policy = trial_policy
            current = batch
    return SearchResult(policy, (b1, b2), trials, list(skipped.values()), batches)


def evaluate(engine: Engine, base_url: str, agent, model: str, test: list[Task], policy: Policy, *, workers: int = 8):
    """Held-out measurement: the unchanged agent twice and the optimized agent once, all interleaved."""
    if not policy.steps:
        b1, b2 = run_batches(engine, base_url, agent, model, test,
                             [("test unchanged 1", Policy.empty()), ("test unchanged 2", Policy.empty())], workers)
        return b1, b2, None, None
    b1, b2, opt = run_batches(engine, base_url, agent, model, test,
                              [("test unchanged 1", Policy.empty()), ("test unchanged 2", Policy.empty()),
                               ("test optimized", policy)], workers)
    return b1, b2, opt, judge(b1, b2, opt)


def per_step(batch: Batch) -> dict[str, dict]:
    out: dict[str, dict] = defaultdict(lambda: {"calls": 0, "cost_usd": 0.0, "tokens_in": 0, "tokens_out": 0})
    for run in batch.runs.values():
        for r in run.records:
            d = out[r["step"]]
            d["label"] = r["step_label"]
            d["calls"] += 1
            d["cost_usd"] += r["cost_usd"] or 0.0
            d["tokens_in"] += r["tokens_in"] or 0
            d["tokens_out"] += r["tokens_out"] or 0
    return dict(out)
