# synapse0g

An optimizer for AI agents. It makes an agent cheaper and faster, and checks on the agent's own
tasks that nothing broke before any change ships.

It works at two levels, in one package:

- **The workflow graph** (`synapse0g.graph`). A workflow is compiled to an effect-typed graph:
  steps, explicit dependencies, and what each step does to the world (`pure`, `read`, `write`,
  `external`, `irreversible`). Passes then rewrite it: independent steps run in parallel, pure
  steps are memoized, a step moves to a cheaper model. Each pass states the precondition that
  makes it safe, and none may cache, reorder, re-run or speculate a step that changes the world.
  Specs: [docs/IR_SPEC.md](docs/IR_SPEC.md), [docs/PASSES.md](docs/PASSES.md),
  [docs/CORRECTNESS.md](docs/CORRECTNESS.md).
- **Each model call** (`synapse0g.proxy`). Agents point their OpenAI-compatible client at the
  synapse0g endpoint. Every call is measured: tokens, hidden reasoning tokens, time, the
  provider's request id, and on 0G the amount 0G says it billed. A per-step policy then applies
  techniques: switching hidden reasoning off, routing a step to a cheaper model on the same
  provider, shortening old tool results, capping runaway output, reusing identical read-only
  requests, and marking the stable start of each prompt for 0G's prompt cache (cached tokens bill
  at about 11% of the input price, measured).

Both end in the same rule: a change ships only when a firewall run on real tasks finds nothing
broken. Providers: 0G Compute's router and Groq, through the OpenAI-compatible chat API.
Results: [RESULTS.md](RESULTS.md).

## Three ways to connect an agent

| | What you change | What you get |
|---|---|---|
| **Paste the code** (dashboard, Connect) | nothing | The code is read, never run. Each function's effect comes from what its body calls, with the evidence; the entry function's statements become the graph; the passes show what could run in parallel and what must never move. ([synapse0g/graph/frontend/automap.py](synapse0g/graph/frontend/automap.py)) |
| **Wrap the client** | one line: `client = synapse0g.wrap(OpenAI())` | Every call measured on the app's own provider and key. At exit the calls are lifted into a graph (a call depends on an earlier one when that answer appears in its prompt) and the passes run on it. Saved: graph and measurements, never prompt text. ([examples/wrap_agent.py](examples/wrap_agent.py)) |
| **Compile a LangGraph app** | three lines | The compiled app runs the rewritten graph, every model call goes through the call-level optimizer, and the firewall decides on held-out tasks. |

The first two show what could be saved. Only the third changes how the app runs, which is why only
it has measured results.

## Connect a LangGraph app

Three lines, and nothing else in the app changes ([examples/supplier_agent.py](examples/supplier_agent.py)):

```python
from synapse0g.openai import OpenAI          # was: from openai import OpenAI
app = synapse0g.compile(builder, sample=request, effects=EFFECTS)   # was: builder.compile()
state = app.invoke(request)                  # as before
```

`EFFECTS` says which steps touch the world (`place_order` is `irreversible`). Any step not listed
is treated as touching it, so it is never traced, moved, cached or repeated. `compile` runs the
declared read-only steps once on `sample` and records which state keys each one really reads and
writes. The edges come from that data flow, not from the order the app was written in. Then the
graph passes run. In the supplier agent, the policy summary and the three supplier lookups were
written as a chain but read nothing from each other, so they run together; the policy summary,
the same every time, is computed once and reused; `place_order` stays last and is never run in a
test. `app.explain()` prints all of it, including what each pass refused and why. Every model
call goes through the call-level optimizer, and `synapse0g bench --agent supplier` measures the
app as written against the compiled one on held-out tasks, with the firewall deciding.

Graphs with conditional edges, cycles or reducer state keys are refused with a message, not
approximated.

## Compile a plain Python agent

No framework needed ([examples/procurement_agent.py](examples/procurement_agent.py)):

```python
from synapse0g.openai import OpenAI                       # was: from openai import OpenAI
app = synapse0g.compile(handle_request, effects=EFFECTS)  # handle_request is an ordinary function
draft = app.invoke(request=..., policy_path=..., quotation_paths=...)
```

The entry function's own statements become the graph, each a call to a function in the same file.
`[extract_quotation(d) for d in documents]` becomes a map whose items run side by side. Each step's
effect is inferred from what its code calls (an unrecognised call makes it `external`, the safe
default) and `EFFECTS` overrides that where the developer knows better. A loop, branch or computed
value in the entry function is refused with its line number. A step declared `pure` and `seeded` is
cached by its arguments within a `cache_scope` (a customer, a team) and the agent's source, so the
same document read for the same customer under the same prompt is read once.

## The procurement agent

An office manager asks for 15 monitors at most Rs 12,000 each within seven days, and three supplier
quotations arrive as a PDF, a spreadsheet and an email. The agent reads the request and the company
purchasing policy, reads each quotation with references to its page, row or line, checks every
offer against the policy in code (expiry, stock, delivery, price before GST, advance payment,
specifications, refurbished goods), has the model explain the choice, drafts the purchase request
with its evidence and files it for a person's approval. A missing price or date makes an offer
incomplete, never a guess. Approval submits it once; a second approval changes nothing.

36 scenarios plus the demo (`synapse0g/agents/procurement_data/`), with right outcomes computed by
code from the facts behind the documents. `synapse0g bench --agent procurement` measures it.

Measured on 0G, 18 held-out requests: 4.5x faster per task (136 → 30 s), 87.5% cheaper billed,
15/18 passed vs 13.5/18 as written; firewall APPROVE. The parallel graph was refused on its tuning
check (one noisy request); details in [RESULTS.md](RESULTS.md).

## synapse0g as a paid agent

`synapse0g agent` runs synapse0g as a service that optimizes other people's agents and pays for its
own model calls:

```bash
synapse0g agent quote procurement           # what a study costs: runs x calls x cost per call (measured)
synapse0g agent submit study:procurement    # or inspect:<path> for any agent's code (free, static)
synapse0g agent status                      # balance and budget mode
synapse0g agent run                         # work through the queue
```

Before each job it reads its balance and chooses a mode: **full** study, **reduced** study (fewer tasks
and trials, said so in the result), **static** (inspect jobs only; studies wait, with the reason), or
**paused**. Every study runs under a hard cap of at most the balance above a reserve. The balance comes
from a local ledger today (`synapse0g agent deposit`); the agent's 0G compute account replaces it
when the launchpad is connected. API: `GET /api/agent`, `GET/POST /api/jobs`.

Funding comes through comfy, 0G's token launchpad: trades of the agent's token pay fees, a compute
share of them becomes 0G compute credit, and the agent's model calls spend it.
`synapse0g/funding.py` reads every stage from where it happens (the chain, the 0G Router, the
agent's own billed calls) and the dashboard's **Funding and jobs** page (`#/agent`) shows it; a
stage that has not happened says so. Setup steps and what was checked on chain: [docs/COMFY.md](docs/COMFY.md).
API: `GET /api/funding`.

## How a technique gets approved

`synapse0g bench` runs the whole loop:

1. Run the agent unchanged on tuning tasks, twice, to learn which tasks are stable and how much
   cost and time move by chance.
2. Profile each step and predict what each technique would save. Techniques predicted to save
   under 3% are not tried, and the report says why.
3. Try the best-predicted technique, alongside an unchanged control run on the same tasks at the
   same time, so a provider slowing down affects both. Keep it only if no task that passed both
   unchanged runs now fails (checked twice), the pass count did not drop, and cost or time improved
   with 95% confidence (paired by task, bootstrap over tasks). It is blocked if it makes the agent
   clearly more than 10% slower. Blocked techniques that saved money are listed as trade-offs.
4. Repeat until nothing is left worth trying.
5. Measure the final policy against the unchanged agent on held-out tasks the search never saw.
   Those are the reported numbers. If nothing helped, the report says "no beneficial change".

## Safety rules

- Routing never crosses providers.
- A model response asking for any tool that is not read-only is never reused: reusing it would
  repeat the action. Tools are labelled from their names and descriptions. An unknown tool is
  treated as irreversible until a developer labels it.
- A spending cap stops paid calls before they are sent (`SYNAPSE0G_SPEND_CAP_USD`, or `--cap`).

## Run it

```bash
uv venv .venv && uv pip install -e ".[agents,dev]"
# .env holds 0G_API_KEY and GROQ_API_KEY; never commit it
.venv/bin/python -m pytest -q                  # offline, no keys, no spend
synapse0g models                                # prices from 0G's live catalog
synapse0g bench --agent research --model zg-main --calib 30 --test 50 --cap 1.5
synapse0g bench --agent math --model zg-main --calib 30 --test 50 --cap 1.5

synapse0g workflow show shop_ops                # compile a workflow, show passes and protected steps (offline)
synapse0g workflow bench research_fanout --split calib --n 10   # measure unchanged vs optimized on 0G
```

Reports land in `reports/` (workflow-graph runs in `reports/synapse_graph/`): a markdown summary and a JSON file with every call's request id,
tokens and billed amount.

## Dashboard

```bash
cd web && npm install && npm run build && cd ..
synapse0g ui                # http://127.0.0.1:8000
# or, while editing the UI:  synapse0g ui  and, in web/,  npm run dev  (http://localhost:5173)
```

Pages: **Live run** (race the supplier app as written and compiled on real 0G calls),
**Funding and jobs** (funding evidence and the service queue), **Results** (the supplier-check app compiled on 0G, then the three earlier results),
**Connect an agent** (paste code to see its graph; the wrap and compile snippets), **Recorded runs**
(apps wrapped with `synapse0g.wrap`), **Workflows** and **Run history**. Every graph is the
engine's own SIR graph: as written above, after the passes below, each step coloured by its effect,
with every transformation and every refusal beside it.

It reads `reports/` through `/api/feed` (`synapse0g/showcase.py`), compiling the bundled
workflows with the graph engine on each request, and shows the compiled supplier-check study
alongside three other results: faster research on 0G, cheaper maths on Groq, and a refused
retail optimization on 0G.
Each one has the before and after graph, every measure with its confidence interval, what the
search tried, every task as fixed / unchanged / broken / crashed, the retail agent's actions, and
where each cost figure comes from (0G's bill, or tokens x listed price). The run history lists every
saved run, including those with no beneficial change and the invalid ones. The Workflows page
shows each of the five bundled workflows as written and after the passes, with the steps the
optimizer refused to touch and why. If the API is down,
the page says so and shows no numbers.

Dashboard design and implementation: [docs/DASHBOARD.md](docs/DASHBOARD.md).
