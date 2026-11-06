# PASSES.md — the optimization pass catalogue

Twelve passes. Each one states what it does, the precondition that makes it
semantics-preserving, how it predicts its own gain, what it typically wins, and how it
can go wrong. A pass that cannot fill in the "precondition" row does not ship
(docs/CORRECTNESS.md).

---

## 0. The pass protocol — `passes/base.py`

```python
class Finding(BaseModel):
    node: str
    precondition: str            # the rule that failed, e.g. "effect<=read"
    message: str                 # a human sentence for the dashboard

class Estimate(BaseModel):
    d_latency_ms: float          # negative = faster
    d_tokens: int                # negative = fewer
    d_cost_usd: float | None     # None when a price is unknown
    risk: float                  # 0..1, prior probability of breaking equivalence
    confidence: float            # 0..1, how much profile data backs this

class Transformation(BaseModel):
    pass_name: str
    nodes_before: list[str]
    nodes_after: list[str]
    description: str             # shown in the UI, hoverable to highlight nodes

class Pass(Protocol):
    name: str
    def preconditions(self, g: Graph, p: Profile) -> list[Finding]: ...
    def predict(self, g: Graph, p: Profile) -> Estimate: ...
    def apply(self, g: Graph) -> tuple[Graph, list[Transformation]]: ...
```

**Findings are a feature, not an error state.** The dashboard's "why was this not
optimized?" view renders them directly, and being able to explain a refusal is a large
part of why this looks like a compiler rather than a bag of heuristics.

Every pass must satisfy two universal invariants, enforced by a shared test harness:

- `apply()` returns a graph that passes `Graph.validate()`.
- `apply()` never changes the relative order of two `external` or `irreversible` nodes,
  and never removes, duplicates or caches one. This is re-checked independently of what
  the pass claims — defence in depth against a buggy pass.

---

## 1. `cse` — common subexpression elimination

**Does:** finds nodes with an identical `node_hash` and collapses them into one,
rewriting every `Ref` to the survivor.

**Precondition:** both nodes are `pure`, or both are `read` **and** the two executions
fall inside `staleness_s`. Both must be `deterministic` or `seeded` with the same seed —
collapsing two `stochastic` samples changes the output distribution.

**Predicts:** removes the duplicate's full latency and token cost. Latency gain only
materialises if the duplicate was on the critical path; the estimate consults
`slack_ms`.

**Typical win:** large on tool-heavy agents, which re-fetch the same value constantly.
A recorded trading-agent run in our corpus calls `get_price("INFY")` twice with an
identical hash inside one execution — the fix is free and provable from the recording.

**Fails when:** `cache_key_fields` is too narrow, so two genuinely different calls hash
the same. This is the most dangerous bug in the whole system because it produces a
plausible wrong answer rather than a crash. Mitigation: `cache_key_fields` defaults to
all arguments, narrowing it is explicit in the DSL, and `correctness/chaos.py` plants a
narrowed key and asserts the firewall catches it.

---

## 2. `memoize` — cross-run caching

**Does:** serves a node from the content-addressed cache instead of executing it.

**Precondition:** `pure`, or `read` with the cache entry younger than `staleness_s`.
Determinism as for `cse`. The cache key must cover every input that can change the
output.

**Predicts:** on a cache hit, the node's entire latency and cost. The estimate uses the
measured `cache_hit_rate` from the profile, so a node that never hits predicts nothing.

**Typical win:** very large on repeated traffic, and the main reason the second run of a
benchmark is much faster than the first.

**Fails when:** staleness is set too generously and the workflow serves yesterday's
search results. Mitigation: `staleness_s` is required on every `read` node — there is no
default — so the author has to make a decision.

**Reporting rule:** cold-cache and warm-cache numbers are always reported separately
(docs/CORRECTNESS.md, docs/IR_SPEC.md). A judge will ask whether the speedup is just a
cache, and the answer must already be on the slide.

---

## 3. `parallelize` — concurrent scheduling

**Does:** list-schedules nodes with no path between them to run concurrently, up to a
global and per-provider concurrency limit.

**Precondition:** no data edge and no effect edge between the nodes. `write` nodes may
run in parallel only when their `writes` sets are disjoint. `external` and
`irreversible` nodes never participate.

**Predicts:** the new critical path length under the concurrency limit, computed by the
scheduler simulator in `profile/costmodel.py`. Zero token change — this pass is pure
latency.

**Typical win:** the headline. On `research_fanout` with *k* independent sub-queries,
the critical path drops from roughly `2k + 2` node latencies to 4.

**Fails when:** the concurrency limit or a provider rate limit is the real bottleneck,
in which case the simulator should predict no gain and the pass should not fire. A pass
that fires and wins nothing is worse than one that declines, because it adds risk for no
return.

**Note:** this is the pass most teams will implement, and implementing it is not the
differentiator. Implementing it *provably safely*, by reading effect edges rather than
assuming independence, is.

---

## 4. `context_prune` — drop unreachable context

**Does:** removes entries from a model call's context that the node does not actually
reference.

**Precondition:** the dropped reference is not reachable in the data-flow from this
node's prompt template, and the node is not `stochastic` in a way that makes the removal
observable through changed sampling. Verified against the corpus before it ships.

**Predicts:** the token count of the dropped entries, scaled by how often the node runs.
Directly reduces cost and, on token-bound calls, latency.

**Typical win:** large and under-appreciated. Agent loops habitually append every prior
step to the next model call, so tokens grow quadratically in step count. In our recorded
corpus a step at index 8 consumed the outputs of steps 0, 1, 2, 4, 5, 6 and 7. Most of
that was never referenced.

**Fails when:** the model was implicitly relying on context the static analysis cannot
see — few-shot conditioning rather than explicit reference. This is why the pass is
validated on the corpus and not trusted from analysis alone, and why its `risk` is
higher than `cse`'s.

---

## 5. `downgrade` — cheaper model selection

**Does:** moves a model call to a cheaper registry model.

**Precondition:** re-running the workflow on the corpus with only that node downgraded
still passes the equivalence relation. This is an **empirical** precondition, checked by
execution rather than by analysis — the pass is honest about that.

**Predicts:** the measured price difference × measured tokens. Returns `d_cost_usd=None`
when either model lacks a price in `models.yaml`, rather than guessing.

**Typical win:** the largest pure-cost win available, often with no latency penalty and
sometimes with a gain, since smaller models are frequently faster.

**Fails when:** nodes are downgraded together without being tested together. Two
individually safe downgrades can interact. The pass tests one node at a time and the
pass manager re-validates the combination, and the result note says so explicitly.

This pass implements the model downgrade policy by testing one proposed downgrade at a time.

---

## 6. `loop_cutoff` — fixpoint detection

**Does:** stops a `loop` region when two successive iterations produce the same state
hash.

**Precondition:** the loop body contains no `external` or `irreversible` node, and the
state hash covers everything the continuation condition reads.

**Predicts:** the cost of the iterations saved, from the measured iteration-count
distribution.

**Typical win:** substantial on agents that spin. In our corpus a QA agent emitted the
identical note three times across three iterations before answering — three model calls
and three retrievals that could not change the outcome.

**Fails when:** the state hash omits something the condition reads, so a loop that was
still making progress gets cut. Mitigation: the hash is computed over the full region
state, and narrowing it requires an explicit declaration.

---

## 7. `dce` — dead node elimination

**Does:** removes nodes whose outputs are not reachable from the workflow outputs.

**Precondition:** no node in the removed cone has effect `external` or `irreversible`
(`docs/IR_SPEC.md` allows a dead `write` to go). A node that sends an email is never dead, however unused its return
value is.

**Predicts:** the full cost of the removed nodes.

**Typical win:** modest on hand-written workflows, meaningful on lifted traces, where
agents routinely compute things they then ignore.

---

## 8. `batch` — call fusion

**Does:** marks *N* calls to one target that declares batch support, so they could share one
provider round-trip (embedding batches, multi-query retrieval). Never chat completions: one
request is one conversation, and *N* questions in one prompt is a different call.

**Precondition:** the target declares batch support in the registry; all calls are
`pure` or `read`; no effect edges between them; batching does not change per-call
semantics (no shared conversational state).

**Predicts:** saves `N−1` round-trip latencies; token change is usually zero, though
some providers price batches differently.

**Status:** marks nodes only, off by default, and it stays that way. Decided 1 Nov: it does not
fire on `code_review`, whose calls are all chat. The one target declaring `batch: true` is
`embed-small`, and no workflow calls an embeddings endpoint. Retrieval here runs in-process, so
fusing it would save no round trip. Real batching needs a workflow with embedding calls.

---

## 9. `speculate` — prefetch and early start

**Does:** starts a node before it is known to be needed, inside a budget, and discards
the result if it turns out not to be.

**At run time:** the scheduler starts a marked node when its inputs exist and a slot is
free. A taken branch adopts the result. Otherwise a finished guess keeps its measured
spend, and a running one is cancelled, which makes that execution's model totals unknown.

**Precondition:** the node is `pure` or `read`; a speculation budget is configured; the
discard path is free. Never applies to `write`, `external` or `irreversible`.

**Predicts:** expected latency saved = P(needed) × node latency, minus the expected
wasted cost = (1 − P(needed)) × node cost. P(needed) comes from branch frequencies in
the profile.

**Typical win:** moderate, and genuinely risky under a quota — wasted calls are real
spend. **Off by default**, enabled explicitly.

**Fails when:** the provider quota is tight. Our OpenRouter free tier is 50 requests per
day per account, which makes speculation a poor trade during this build. Implement it,
demonstrate it, leave it off.

---

## 10. `constfold` — constant folding

**Does:** evaluates `deterministic` `pure` `transform` nodes whose arguments are all
literals at compile time, replacing them with their result.

**Precondition:** kind is `transform`, effect is `pure`, determinism is
`deterministic`, every argument is a literal.

**Predicts:** the node's full (usually tiny) cost.

**Typical win:** small. It is in the catalogue because it is textbook, nearly free to
implement, and it makes the pass list legibly a compiler's pass list.

---

## 11. `early_exit` — confidence-gated short circuit

**Does:** inserts a gate that ends the workflow early when a confidence signal exceeds a
calibrated threshold.

**Precondition:** the threshold is calibrated on the `calib` split, and the measured
accuracy drop on `test` is within a declared tolerance. This pass **changes behaviour by
design**, so it is the one pass that must declare a tolerance rather than claim
equivalence.

**Predicts:** expected cost saved = P(exit early) × the cost of the skipped remainder.

**Typical win:** large on workflows with an easy majority of tasks.

**Fails when:** the threshold is calibrated on the same tasks used to report accuracy —
the classic leak. Split discipline (docs/CORRECTNESS.md) is what prevents it.

**Honesty rule:** because this pass trades accuracy for cost, every number it produces
is reported with the accuracy delta beside it. Never show the saving alone.

---

## 11a. `context_trim` — clip what is sent

**Does:** clips each non-system message of a model call to a character limit. Unlike
`context_prune`, this removes tokens that were really being sent.

**Precondition:** empirical. The corpus still passes with the clipped prompts. Off by default.

**Fails when:** the answer is in the clipped part. That is what the firewall is for.

## 12. Pass interactions

Passes are not independent, which is why the manager searches over pipelines rather than
applying a fixed list.

| Interaction | Effect |
|---|---|
| `context_prune` then `cse` | Pruning changes content hashes and can **create** new CSE opportunities. Run prune first. |
| `cse` then `parallelize` | Collapsing duplicates removes nodes from the critical path and changes what is worth parallelizing. Re-profile between them. |
| `parallelize` then `downgrade` | Once latency is dominated by one slow node, downgrading *that* node is worth more. |
| `memoize` then anything | A warm cache changes every measured latency. Always re-profile; never carry a cold profile into a warm decision. |
| `loop_cutoff` then `dce` | Cutting a loop frequently makes downstream nodes dead. |
| `speculate` with `downgrade` | Speculating a downgraded call is cheap enough to be worth it more often. |

The manager handles this by re-profiling between passes in greedy mode, and by searching
orderings in beam mode. The default ordering, used when there is no time to search:

```
constfold → dce → context_prune → cse → loop_cutoff → memoize → parallelize → downgrade
```

Rationale: shrink the graph first (cheap, structural), then remove redundancy, then
schedule what remains, then choose models last — because model choice depends on which
nodes ended up critical.
