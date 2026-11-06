# IR_SPEC.md — the Synapse Intermediate Representation (SIR)

`synapse/core/sir.py` is the implementation of this document and is **the contract**.
Every other module reads it. Changing a field needs a `SIR_VERSION` bump, a migration
note in the PR, regenerated `fixtures/`, and a message to the team before merging.
Adding an optional field with a default is allowed without a bump.

---

## 1. Design principles

1. **Every dependency is explicit.** If a node depends on something, that dependency is
   in its `args` or its `reads`. There are no hidden channels. This is the property that
   makes every optimization decidable rather than guessed.
2. **Values are SSA.** A value is produced once by exactly one node and never
   reassigned. Data dependencies are therefore syntactic facts.
3. **Effects are typed, and the default is restrictive.** An unknown effect is
   `external`, never `pure`. Optimizations are opt-in per node, not opt-out.
4. **Control flow is structured, not unrolled.** Loops and branches are regions. A
   300-node unrolled graph is not a picture a human can read, and legibility is a
   product requirement here.
5. **Profiles are attached, not embedded.** The frontend never writes profile data; the
   profiler does. A graph is meaningful without ever having been run.

---

## 2. Core types

```python
SIR_VERSION = 1

NodeKind    = Literal["model", "tool", "retrieval", "transform", "control", "io"]
Effect      = Literal["pure", "read", "write", "external", "irreversible"]
Determinism = Literal["deterministic", "seeded", "stochastic"]

class Ref(BaseModel):
    """A dependency on another node's output."""
    node: str
    output: str = "value"

class Literal_(BaseModel):
    value: Any

Arg = Ref | Literal_

class NodeProfile(BaseModel):
    """Attached by profile/collect.py. Never written by a frontend."""
    samples: int
    latency_p50_ms: float
    latency_p95_ms: float
    tokens_in_mean: float | None = None      # None when the provider did not report
    tokens_out_mean: float | None = None
    cost_usd_mean: float | None = None       # None when the model has no price
    failure_rate: float = 0.0
    cache_hit_rate: float = 0.0
    slack_ms: float | None = None            # distance from the critical path

class Node(BaseModel):
    id: str
    kind: NodeKind
    target: str                              # registry model id / tool name / retriever
    args: dict[str, Arg] = {}
    outputs: list[str] = ["value"]
    effect: Effect = "external"              # restrictive default, deliberately
    determinism: Determinism = "deterministic"
    reads: list[str] = []                    # state keys observed
    writes: list[str] = []                   # state keys mutated
    cache_key_fields: list[str] | None = None  # None = all args
    staleness_s: float | None = None         # for `read`: cache validity window
    region: str | None = None                # enclosing region id
    profile: NodeProfile | None = None
    origin: dict = {}                        # {"file":..,"line":..} or {"run":..,"step":..}
    meta: dict = {}

class Region(BaseModel):
    id: str
    kind: Literal["loop", "branch", "map"]
    body: list[str]                          # node ids
    condition: Ref | None = None             # loop continuation / branch predicate
    max_iterations: int | None = None

class Graph(BaseModel):
    id: str
    sir_version: int = SIR_VERSION
    name: str
    nodes: list[Node]
    regions: list[Region] = []
    inputs: dict[str, Any] = {}              # workflow parameters
    outputs: dict[str, Ref] = {}             # what the workflow returns
    derived_from: str | None = None          # the graph this was optimized from
    pipeline: list[str] = []                 # passes applied to reach this graph
    meta: dict = {}
```

---

## 3. Edges

Edges are derived from nodes, not stored separately, so they cannot drift out of sync.

### 3.1 Data edges

For every `Ref` in a node's `args`, a data edge runs from the referenced node to this
one. A data edge is an absolute ordering constraint — you cannot consume a value that
does not exist yet — but it constrains *only* the two nodes it connects.

### 3.2 Effect edges

Derived from the effect lattice and the `reads` / `writes` sets:

- Two nodes that touch the same state key, where at least one `writes` it, get an
  ordering edge in program order (read-after-write, write-after-write,
  write-after-read).
- Every `external` or `irreversible` node gets ordering edges to all `external` and
  `irreversible` nodes before and after it in program order. Observable side effects
  keep their relative order, always.
- `pure` and `read` nodes get no effect edges.

**The distinction between the two edge types is the entire point of the IR.** Two nodes
with no path between them in the combined graph may run concurrently. Two nodes joined
only by a data edge may be reordered if the data is already available. Two nodes joined
by an effect edge may never be reordered. `parallelize` is correct because this
distinction is explicit, not because we hope the nodes are independent.

---

## 4. The effect lattice

Ordered from most permissive to most restrictive. `core/effects.py` owns these
predicates; passes call them rather than reimplementing the logic.

| Effect | Meaning | Example |
|---|---|---|
| `pure` | Same inputs produce the same outputs; nothing observable happens | a seeded model call, a string transform, a parse |
| `read` | Observes state outside the workflow that may change | web search, vector retrieval, reading a file, `now()` |
| `write` | Mutates state inside the workflow | writing to the agent's scratchpad or memory |
| `external` | Mutates the world outside the workflow | POST to an API, sending an email, writing a file |
| `irreversible` | Mutates the world in a way that cannot be undone, or moves money | placing an order, charging a card, deleting a record |

### 4.1 What each tier permits

| Transformation | `pure` | `read` | `write` | `external` | `irreversible` |
|---|:--:|:--:|:--:|:--:|:--:|
| CSE (collapse duplicates) | ✔ | ✔ within `staleness_s` | ✖ | ✖ | ✖ |
| Memoize across runs | ✔ | ✔ within `staleness_s` | ✖ | ✖ | ✖ |
| Reorder | ✔ | ✔ among reads | disjoint keys only | ✖ | ✖ |
| Run in parallel | ✔ | ✔ | disjoint keys only | ✖ | ✖ |
| Speculate / prefetch | ✔ | ✔ | ✖ | ✖ | ✖ |
| Batch with siblings | ✔ | ✔ | ✖ | ✖ | ✖ |
| Eliminate if dead | ✔ | ✔ | ✔ | ✖ | ✖ |
| Execute during optimization search | ✔ | ✔ | ✔ | ✖ | ✖ |
| Execute during equivalence runs | ✔ | ✔ | ✔ | stubbed | **never** |

### 4.2 Determinism interacts with purity

`determinism` is a second axis, and conflating it with `effect` is the most likely
source of a wrong optimization:

- `deterministic` — same inputs always give the same output.
- `seeded` — stochastic but reproducible given a recorded seed, which is stored in the
  cache entry.
- `stochastic` — a model call at temperature > 0, or any unseeded sampling.

A `pure` + `stochastic` node (the common case for a creative model call) is **not**
CSE-able or memoizable by default, because collapsing two samples changes the
distribution of the output even though neither call touched the world. It becomes
eligible only when the active equivalence relation tolerates variation, and the result
object records that this was the case.

### 4.3 The restrictive default

`Node.effect` defaults to `external`. A frontend that cannot determine a tool's effect
produces a node nothing will optimize, which is slow and correct. The opposite default
is fast and occasionally catastrophic. A replay
re-executed a `place_order` call.

---

## 5. Content hashing

```python
def node_hash(node: Node, resolved: dict[str, Any]) -> str:
    fields = node.cache_key_fields or sorted(node.args)
    return fingerprint({
        "kind": node.kind,
        "target": node.target,
        "effect": node.effect,
        "args": {k: resolved[k] for k in fields},
    })
```

`fingerprint` is SHA-256 over canonical JSON (sorted keys, no whitespace,
`ensure_ascii=False`). The same logical call
must hash identically on every machine or CSE, memoization and the cross-run cache all
silently stop working — "silently" being the dangerous part, since the failure mode is a
missing optimization rather than a crash.

`cache_key_fields` exists because some arguments should not enter the key: a request id,
a timestamp, a trace header. Listing them wrongly is a correctness bug — too few fields
and you serve the wrong cached value — so the DSL requires the author to be explicit
when they narrow it, and `None` (all args) is the default.

---

## 6. Regions

```python
Region(id="r1", kind="loop", body=["n4","n5","n6"], condition=Ref("n6","continue"),
       max_iterations=8)
```

- `loop` — body repeats while `condition` holds. `loop_cutoff` operates here: if the
  state hash after iteration *k* equals the hash after *k−1*, the loop has reached a
  fixpoint and further iterations cannot change the result.
- `branch` — body executes if `condition` holds. Both arms are present in the graph;
  the scheduler executes one. `speculate` may start the likely arm early when every node
  in it is `pure` or `read`.
- `map` — body executes once per element of a collection. The elements are independent
  by construction, which makes `map` the easiest and largest parallelization win, and
  the reason `research_fanout` and `code_review` are in the benchmark suite.

---

## 7. Validation

`Graph.validate()` runs on every construction and after every pass. A pass that produces
an invalid graph is a bug that must fail loudly, immediately, rather than producing a
subtly wrong execution.

1. Every `Ref` names an existing node and an output that node declares.
2. The data-edge graph is acyclic (cycles must be expressed as a `loop` region).
3. Every node id is unique; every `region.body` entry exists; regions do not overlap.
4. Every `Ref` in `outputs` resolves.
5. No node with effect `write` declares an empty `writes` list, and no node with
   `pure` declares a non-empty `reads` or `writes`.
6. `staleness_s` is set only on `read` nodes.
7. `sir_version` matches the running `SIR_VERSION`.

Validation failures raise. They are never warnings, and they are never swallowed.

---

## 8. Worked example

The `research_fanout` workflow, written in the obvious sequential style, lifted to SIR.

```python
@workflow
def research(question: str) -> str:
    subs  = plan(question)                  # n0  model     pure/seeded
    hits  = [search(s) for s in subs]       # n1  retrieval read   (map region)
    notes = [summarize(h) for h in hits]    # n2  model     pure/seeded (map region)
    return synthesize(question, notes)      # n3  model     pure/seeded
```

```
nodes:
  n0 plan        kind=model     effect=pure  determinism=seeded
                 args={question: Literal}            outputs=[subqueries]
  n1 search      kind=retrieval effect=read  staleness_s=3600  region=r_map1
                 args={query: Ref(n0, subqueries)}   outputs=[docs]
  n2 summarize   kind=model     effect=pure  determinism=seeded  region=r_map2
                 args={docs: Ref(n1, docs)}          outputs=[note]
  n3 synthesize  kind=model     effect=pure  determinism=seeded
                 args={question: Literal, notes: Ref(n2, note)}  outputs=[answer]

regions:
  r_map1 kind=map body=[n1]
  r_map2 kind=map body=[n2]

data edges:   n0 -> n1 -> n2 -> n3
effect edges: none (no writes, nothing external)
outputs:      {answer: Ref(n3, answer)}
```

The chain `n0 → n1 → n2 → n3` looks strictly sequential, and executed naively it is.
But `n1` and `n2` are `map` regions over independent elements with **no effect edges
between their instances**, so with *k* sub-queries the critical path is
`plan → search → summarize → synthesize` — four node latencies — rather than
`1 + k + k + 1`. At k = 6 that is 14 sequential calls collapsed to a 4-deep critical
path.

This is exactly the inefficiency the problem statement's backstory describes ("execute
independent tasks sequentially"), it is a shape that real agent code genuinely has, and
the IR makes the win provable rather than hopeful: the absence of an effect edge is a
fact about the graph, not an assumption about the workload.


## 9. Initial implementation clarifications (31 Oct 2026)

The first SIR v1 implementation is in `synapse/core/sir.py`. `NodeProfile` latency fields
are nullable when no valid latency was observed; additional optional/defaulted fields
carry latency sample count, token variance and partial-data names. Predictions reject
missing latency rather than treating it as zero. Validation runs on model construction
and explicit `validate_graph()` / `Graph.model_validate(graph.model_dump())` after a pass.

Effect edges include read/write conflicts, including reads that follow writes; the
statement above that reads never get effect edges does not apply to those conflicts.
Protected nodes act as conservative barriers against all nodes. No data dependency is
removed by scheduling. `pure` describes effects, independently of determinism. Seeded
reuse requires a recorded seed in `Node.meta["seed"]`. Structured regions are represented
but must be expanded to an execution DAG before the current schedule simulator or local
validation runner executes them; those components reject unresolved regions explicitly.
