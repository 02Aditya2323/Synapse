# BENCHMARKS.md — what we measure and how we report it

The problem statement asks us to "demonstrate measurable improvements in execution time
or cost while preserving required output behavior". This document defines what gets
measured, against what, and how it is reported so the numbers survive a hostile
question.

---

## 1. The five benchmark workflows

Each one is chosen to showcase a different pass, and each has a shape real agent code
actually has. A contrived workflow that exists only to make a pass look good is worth
nothing on stage — a judge can spot it, and it invites the question we do not want.

| Workflow | Shape | Primary pass | Secondary | Why it is realistic |
|---|---|---|---|---|
| `research_fanout` | plan → *k* independent sub-queries → synthesize | **parallelize** | batch, memoize | Deep-research agents are written exactly like this, and written sequentially |
| `rag_qa` | multi-hop retrieval QA over HotpotQA | **context_prune** | parallelize, cse | The standard RAG loop; context grows every hop |
| `math_tools` | reasoning with calculator / python tools, GSM8K | **cse** | downgrade, loop_cutoff | Tool agents re-compute the same value constantly |
| `code_review` | map over files → reduce to a summary | **batch** | parallelize, downgrade | Map/reduce over a repo; embarrassingly parallel, usually serial |
| `shop_ops` | tool-heavy agent containing an `irreversible` node | **none — this is the safety demo** | — | Shows Synapse refusing to optimize, with the reason |

`shop_ops` earns its place by producing *no* optimization. It is the workflow where we
demonstrate that the effect system is real.

---

## 2. Metrics

Per execution:

| Metric | Source | Notes |
|---|---|---|
| wall-clock p50 / p95 | measured | the headline latency number |
| critical path length | computed from the realized schedule | explains *why* latency changed |
| tokens in / out | **provider response** | `None` if the provider did not report; never estimated |
| cost USD | tokens × `price_per_mtok` from `models.yaml` | `None` if either model lacks a price |
| provider calls | counted | the quota-relevant number |
| cache hit rate | counted | the cold/warm honesty check |
| accuracy | the task's checker | pass rate on the split |
| crashed executions | counted separately | **never** folded into latency or accuracy |

Derived: speedup (`t_original / t_optimized`), cost reduction (%), tokens saved (%),
accuracy delta (percentage points).

---

## 3. Baselines

Beating only our own unoptimized version is a weak claim. Three baselines:

1. **Original, sequential.** The workflow as written, executed naively. This is the
   honest "before" and the one the PS implies.
2. **Naive cache-only.** A simple memoization layer with no graph analysis — roughly
   what a competent engineer would add in an afternoon. **This is the baseline that
   matters**, because it is what a judge will imagine as the obvious alternative. If
   Synapse does not clearly beat it, the compiler is not earning its complexity.
3. **Framework default.** Where the workflow has a natural LangChain/smolagents
   equivalent, run that too. Stretch; only if M3 lands early.

---

## 4. Cold and warm, always separately

**Every table has both columns. Every time. No exceptions.**

- **Cold** — empty cache. Shows the *structural* wins: parallel scheduling, CSE within a
  single execution, context pruning, dead-node elimination, loop cutoff. These are the
  wins that prove the compiler is doing something a cache cannot.
- **Warm** — populated cache. Adds memoization across executions. Larger numbers, but
  partly attributable to caching rather than compilation.

Reporting only the warm number is the easiest way to produce a headline that falls apart
under one question. The first thing a sharp judge asks about a 10× speedup is whether it
is a cache. The answer must already be on the slide.

---

## 5. Splits

`bench/splits.py`, split by `hash(task_id)`:

| Split | Share | Used for |
|---|---|---|
| `train` | 60% | fitting the learned pass policy and the cost model |
| `calib` | 20% | thresholds: early-exit gate, noise band, risk priors |
| `test` | 20% | **every headline number**, touched once |

A task and all its variants share a split. Never random-split executions — two
executions of the same task are not independent samples.

---

## 6. The noise band

Wall-clock variance against remote providers is large. Before reporting any gain:

1. Run the **unmodified** graph over the corpus twice.
2. Take the spread of the per-task differences.
3. That spread is the noise band, reported as a percentage alongside every result and
   drawn on the comparison chart.

A gain inside the band is not a gain. The pass manager refuses to apply a pass whose
predicted improvement falls inside it.

---

## 7. The report

`synapse bench --report` emits markdown plus JSON. The shape:

```
Workflow: research_fanout      split: test      n = 40      relation: checker
Noise band: ±4.1%              cold cache

                      wall p50   wall p95   tokens    cost USD   accuracy   calls
original (sequential)   18.4 s     24.1 s    12,840    0.0214      0.82       14
cache-only baseline     16.9 s     22.0 s    11,200    0.0187      0.82       12
synapse optimized        5.1 s      7.3 s     9,310    0.0155      0.82        9
                        ─────                ──────    ──────     ─────
speedup                  3.6x                 -27.5%   -27.6%      +0.00pp

pipeline: context_prune -> cse -> parallelize
firewall: APPROVE   unchanged 40  fixed 0  broken 0  crashed 0
```

Every number in that block is measured. Every one of them names its split, its
equivalence relation, and whether the cache was cold. Confidence intervals accompany
accuracy, from `bench/report.py`.

### Ablation: which pass earns the gain

A pipeline's speedup does not say which pass earned it, so `bench/ablation.py` measures it:

```bash
synapse bench --ablation --workflow code_review --n 20    # calib by default, never test
```

Every task runs on the unmodified graph twice, on the full pipeline, and on the full pipeline
with each applied pass removed, after one unscored warm-up. The start rotates through all five
variants, so use a task count divisible by the number of variants. Each variant gets its own
firewall verdict. A removal that stays inside the noise band means that pass did not cause
the gain. A removal measures what a pass adds given the others.

Studies are saved under `bench/reports/ablation/`, the one part of `bench/reports/` that is
committed, so `synapse bench --report` publishes them from any checkout. They are diagnosis on
`calib`, and every table says it is not a headline number.

---

## 8. Reporting rules

1. **Accuracy delta is always shown beside any cost saving.** A cost reduction with an
   accuracy drop hidden in a different table is a lie of omission, and `early_exit` in
   particular trades one for the other by design.
2. **Crashed executions are reported, never dropped.** A harness that silently discards
   crashes reports fake reliability (`CLAUDE.md` rule 10).
3. **`None` is printed as `—`, never as `0`.** An unreported token count is not zero.
4. **Sample counts appear on every aggregate.** A p95 from three samples is labelled as
   such.
5. **If a pass makes something worse, the table shows it.** We have eleven passes; some
   will lose on some workflows. Showing a loss and explaining it is more credible than
   eleven wins, and it is the difference between a results table and a marketing slide.
6. **Quote the live numbers, never remembered ones.** Re-run the report before the
   pitch and read from its output.

---

## 9. Quota budget

The OpenRouter free tier is 50 requests/day/account and repeated benchmark runs can exhaust it
mid-session. Plan:

- **Development: local Ollama models only.** All five workflows must run end to end on
  a local model. This is a hard requirement, not a preference — it is what makes the
  build quota-independent.
- **Remote quota is reserved** for the final `test`-split benchmark run (H15–H16) and
  the demo.
- `models.yaml` carries a `fallback:` chain so an exhausted provider degrades to a local
  model rather than failing a run.
- The benchmark driver logs its provider-call count before starting and refuses to begin
  a run it cannot finish within the remaining budget.

---

## 10. Measured facts about the local provider

Pilot measurements on one RTX 3050 6 GB with `qwen2.5:3b`, `train` tasks only. They are
feasibility checks and never headline numbers.

- **`OLLAMA_NUM_PARALLEL=4` must be set, or parallelism measures nothing.** With the
  setting unset on this machine, Ollama queued requests: 4 concurrent calls took as long
  as 4 sequential ones (0.90x and 1.05x). With it set: 2.48x and 2.84x. Set it before
  starting Ollama, on every machine that benchmarks.
- **`seed` does not make a local model byte-reproducible.** Over 10 tasks run four times
  each, the final answer matched on only 6/10 sequential-vs-sequential pairs, 8/10
  concurrent pairs and 7/10 sequential-vs-concurrent pairs. Checker verdicts flipped on
  0/10. So `exact` is unusable for local model workflows even on an unmodified graph, and
  `broken == 0` needs an estimate of the repeat flip rate on a larger sample.
