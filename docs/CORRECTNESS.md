# CORRECTNESS.md — preconditions, equivalence, and the regression firewall

This is the examined part of the problem statement. "Without compromising correctness"
appears twice in the PS, and *Correctness Validation* is a named feature with the words
**preconditions** and **test cases** in it.

Most teams will treat correctness as an assertion. We treat it as something to prove,
and — more importantly for the demo — as something that can **fail visibly**. A check
that never fails is not visibly a check. Our strongest single demo moment is Synapse
refusing to ship an optimization.

---

## 1. The three layers

```
   Layer 1  PRECONDITIONS      static, per pass, before anything runs
                               "is this transformation legal on this graph?"
                                          │
   Layer 2  EQUIVALENCE        dynamic, per task, after both versions run
                               "did the optimized workflow still behave?"
                                          │
   Layer 3  FIREWALL           aggregate, over a corpus
                               "do we ship this pipeline?"  APPROVE / BLOCK
```

Each layer catches what the one above cannot. Preconditions are cheap and catch
category errors (you cannot cache a node that sends email). Equivalence is expensive and
catches semantic errors (this cache key was too narrow). The firewall is the policy
decision that turns per-task results into a shippable verdict.

---

## 2. Layer 1 — preconditions

Every pass implements `preconditions(graph, profile) -> list[Finding]`. An empty list
means the pass is legal on this graph. A non-empty list is a set of reasons it is not,
each naming a node, the rule that failed, and a human sentence.

```python
Finding(node="n7",
        precondition="effect<=read",
        message="place_order writes to the outside world and moves money; "
                "it is never cached, re-ordered or re-run.")
```

The evaluator lives in `correctness/preconditions.py`, separate from the passes, so it
can be run on its own. The dashboard's **"why was this not optimized?"** panel calls it
directly, and that panel is a feature: a compiler that explains its refusals is far more
convincing than one that silently does less.

**Preconditions are necessary, never sufficient.** `cse` can satisfy every static
precondition and still be wrong if `cache_key_fields` omits a field that changes the
result. That is what layer 2 exists for.

---

## 3. Layer 2 — equivalence relations

"Correct" is not one thing for an AI workflow. Four relations, pluggable, and **every
reported number states which one produced it**.

| Relation | Definition | Use when | Strength |
|---|---|---|---|
| `exact` | byte equality of the final output | deterministic or seeded workflows | strongest, often too strict |
| `checker` | the task's own checker passes on both outputs | agent tasks with many acceptable answers | **the default** |
| `invariant` | declared invariants hold on the optimized run | safety-critical workflows | orthogonal, composes with the others |
| `semantic` | an LLM judge against a fixed rubric | free-text output with no checker | weakest; never used alone |

### 3.1 `checker` is the default, and why

For agent workflows the question is not "is the output identical" but "does it still do
the job". A multi-hop QA workflow can phrase an answer differently and still be right;
a maths workflow must still reach the right number. The task's checker already encodes
that judgement, and it is the same checker used to measure accuracy, so equivalence and
accuracy are measured on one consistent definition.

### 3.2 `invariant` composes

Invariants are declared on the workflow, not the task:

```python
@invariant("no order exceeds the per-order risk limit")
def _(execution): return all(o.qty <= 50 for o in execution.orders)

@invariant("every citation in the answer refers to a retrieved document")
def _(execution): return set(execution.citations) <= set(execution.retrieved_ids)
```

An invariant violation is a BLOCK regardless of what the primary relation said. This is
how `shop_ops` demonstrates safety: an optimization that happened to produce an
acceptable answer while placing a bad order still fails.

### 3.3 `semantic` is quarantined

An LLM judge is itself a model call and can be wrong. It is used only where no checker
can exist, never as the sole basis for an APPROVE, and every table it touches is marked.
This follows the rule used throughout this project: a model never decides the
ground truth used to evaluate it.

### 3.4 Stochastic workflows

Exact equality is meaningless at temperature > 0. Three options, in order of preference:

1. **Seed it.** `determinism="seeded"` with a recorded seed makes the run reproducible
   and `exact` usable. Best for benchmarking.
2. **Use `checker`.** Accept any output the task's checker accepts.
3. **Run *n* times and compare pass rates** with a confidence interval. Expensive;
   reserved for the headline accuracy claim.

Whichever is used is named in the result object and on the slide.

---

## 4. Layer 3 — the regression firewall

`correctness/firewall.py` implements an APPROVE/BLOCK verdict across many runs.

### 4.1 The procedure

1. Take the corpus for this workflow, restricted to the **held-out `test` split**.
2. Run the original graph on every task. Record outputs, outcomes, and measurements.
3. Run the optimized graph on every task, same inputs, same seeds where seeded.
4. Apply the equivalence relation per task, and classify:
   - **unchanged** — equivalent before and after
   - **fixed** — failed before, passes now (rare but real: pruning noisy context
     sometimes *helps* accuracy, and we report it rather than hiding it)
   - **broken** — passed before, fails now
   - **crashed** — a runtime error; counted separately, never as a workflow failure
5. Measure the aggregate latency, token and cost deltas, cold and warm.

### 4.2 The verdict

```
APPROVE  iff  broken == 0
          and crashed == 0
          and the measured win exceeds the noise band
          and every declared invariant held on every task

BLOCK    otherwise, with the reason and the per-task breakdown
```

```python
class Verdict(BaseModel):
    decision: Literal["APPROVE", "BLOCK"]
    reason: str
    relation: str                 # which equivalence relation was used
    split: str                    # must be "test" for a reported number
    n_tasks: int
    unchanged: int; fixed: int; broken: int; crashed: int
    d_latency_pct: float; d_tokens_pct: float; d_cost_pct: float | None
    noise_band_pct: float
    invariant_failures: list[str]
    cold: Measurements; warm: Measurements
```

The verdict object is attached to **every** optimization result and rendered in the UI.
An optimization without a verdict is a draft, not a result (docs/CORRECTNESS.md).

### 4.3 The noise band

Run-to-run variance in wall-clock is substantial when calling remote providers. The
noise band is measured by running the **unmodified** graph twice on the corpus and
taking the spread. A "gain" inside that band is not a gain, the manager refuses to apply
a pass that predicts one, and the band is drawn on the comparison chart.

This single piece of rigour is what separates a believable 3.2× from a suspicious 1.04×.

A band covers latency and, when the model has a price, cost. `calib` saves it with the
graph id and model it measured, and `test` refuses a band from another graph or model, or
one with fewer than 8 pairs (`bench/suite.py` `load_band`). `test` is touched once, so a
stale band has to stop the run rather than quietly decide it. A model with no price in
`models.yaml` has no cost band, so on such a model only a latency win can pass.

### 4.4 Proposed: a net-correctness rule, `net-1` (not in force)

**Status: a proposal. The strict verdict in 4.2 is the verdict until the team agrees.** Every
benchmark run prints `net-1`'s decision beside the strict one and stores it as
`proposed_net_verdict`. Code: `synapse/correctness/net_rule.py`.

**Why.** On a real model the strict rule cannot approve. research_fanout on `test` fixed 5 tasks
and broke 2 and was blocked, while re-running the *unmodified* graph broke 2 and fixed 1 by
itself. Any rule that treats one flipped answer as proof of a regression is measuring the server's
noise, not the optimization.

**The rule.** `net-1` approves only when all four hold:

1. **Hard blocks are untouched.** Every strict reason other than a count of broken tasks still
   blocks: a crash, a failed invariant, changed side effects, no cold win above the noise band,
   mixed splits or conditions (`VerificationReport.other_reasons`). A broken task whose side
   effects changed, or that failed an invariant, blocks by itself.
2. **Accuracy does not fall:** tasks fixed >= tasks broken, on cold runs.
3. **Churn stays within repeat noise.** The answers that changed, fixed plus broken, must not be
   significantly more than the answers that changed when the unmodified graph ran again on the same
   tasks (one-sided Fisher exact test, alpha = 0.05). An optimization that breaks ten and fixes ten
   has changed behaviour even though it nets to zero.
4. **No repeat run, no net verdict.** Without the original run twice there is no yardstick, so the
   rule blocks and says so.

McNemar's exact two-sided p-value on fixed against broken is reported for the reader. It decides
nothing.

**Frozen on 1 Nov 2026, before any run judged it.** It was written after the 1 Nov `test` results
had been seen, so those results cannot be evidence for it, and we do not quote what it "would have
said" on them. It is judged on the next labelled run. A changed threshold after that run is a new
rule with a new id, because changing it then is tuning on the evidence.

**How it is judged.** `test` for research_fanout, rag_qa and code_review has been used once, so a
new run must be labelled, and `bench/suite.py` refuses it otherwise:

```bash
py -3 -m bench.suite --workflow research_fanout --split test --final --label "second run"
```

Its report is `bench/reports/research_fanout_test_second_run.json`, and `bench/report.py` marks
it as a labelled run. The questions for the team: on that run, did `net-1` approve anything the
strict rule blocked, and does each such approval hold up when the tasks are read by hand?

---

## 5. Chaos testing — proving the checks work

`correctness/chaos.py`.

A check nobody has seen fail is not trusted. So we deliberately plant unsafe
optimizations and assert the system catches each one. This is simultaneously a test
suite and a demo slide.

| # | Planted unsafe transformation | Which layer should catch it |
|---|---|---|
| U1 | CSE across two `stochastic` unseeded model calls | L1 precondition (determinism) |
| U2 | Memoize a `read` node past its `staleness_s` | L1 precondition (staleness) |
| U3 | Narrow `cache_key_fields` so two different calls collide | **L2 equivalence** — static checks cannot see this |
| U4 | Parallelize two `write` nodes touching the same key | L1 precondition (effect edge) |
| U5 | Reorder an `external` node | L1 + the scheduler's independent re-check |
| U6 | Cache an `irreversible` node (`place_order`) | L1, and the executor refuses regardless |
| U7 | Prune a context entry the model actually relies on | **L2 equivalence** |
| U8 | Downgrade a model that cannot do the task | **L2 equivalence** |
| U9 | Cut a loop whose state hash omits a condition input | **L2 equivalence** |
| U10 | Early-exit threshold calibrated on the test split | L3 split discipline check |

The deliverable is a table: planted / caught by / verdict. Ten out of ten caught, with
the layer named, is a far better slide than another throughput chart — and if one is
*not* caught, we have found a real bug before a judge did.

---

## 6. Split discipline

`bench/splits.py`.

Tasks are split by `hash(task_id)` into `train` / `calib` / `test`. A task and all its
variants share a split — never random-split executions, because two executions of the
same task are not independent samples.

- `train` — fitting the learned pass policy and the cost model.
- `calib` — thresholds: the early-exit gate, the noise band, risk priors.
- `test` — **every headline number.** Touched once, at the end.

If we tune passes on the tasks we benchmark on, every number we show is inflated, and
this is a question an experienced judge asks. The answer should be a document reference,
not an improvisation.

---

## 7. What we say when a judge pushes

Rehearse these.

> **"Is the speedup just caching?"**
> Cold and warm are reported separately in every table. Cold-cache shows the structural
> wins — parallel scheduling, CSE within a run, context pruning. Warm adds memoization
> on top. Here are both columns.

> **"How do you know the optimized version is still correct?"**
> Three layers. Preconditions are static and per pass. Equivalence runs both versions
> across a held-out corpus under a stated relation. The firewall aggregates into
> APPROVE or BLOCK. And here is the table of ten unsafe optimizations we planted and the
> layer that caught each.

> **"Your equivalence relation is doing a lot of work."**
> It is, which is why it is pluggable and named in every result. The default is the
> task's own checker — the same function that measures accuracy, so we are not grading
> ourselves on a softer scale than we report.

> **"Did you tune on your test set?"**
> No. Splits are by `hash(task_id)`, fitting happens on train and calib, and the test
> split produced only the numbers on this slide.

> **"What happens with side effects?"**
> They are in the type system. `external` and `irreversible` nodes are never re-ordered,
> re-run, cached or speculated, and `irreversible` nodes are never executed at all
> during optimization search or equivalence runs. Here is Synapse declining to optimize
> a workflow that places an order, with the reason.

## Noise band formula changed, 1 Nov 2026

The latency band is now the p95 of `|a - b| / max(a, b)`. It was `|a - b| / a`, which can exceed 100%
while a gain cannot, so a noisy network made a latency win impossible. Radhesh found this and Aryan
decided it. **We chose this after seeing the Groq `calib` results**, and said so. It was committed
before any `test` run used it. On that data the bands went from 105.5 / 131.5 / 174.8% to
52.8 / 56.7 / 64.9%. Bands saved under the old formula no longer load.
