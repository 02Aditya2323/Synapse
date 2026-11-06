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

It reads `reports/` through `/api/feed` (`synapse0g/showcase.py`), compiling the bundled
workflows with the graph engine on each request, and shows three results: faster
(research workflow on 0G), cheaper (maths agent on Groq) and refused (tau-bench retail on 0G).
Each one has the before and after graph, every measure with its confidence interval, what the
search tried, every task as fixed / unchanged / broken / crashed, the retail agent's actions, and
where each cost figure comes from (0G's bill, or tokens x listed price). The run history lists every
saved run, including those with no beneficial change and the invalid ones. The Workflows page
shows each of the five bundled workflows as written and after the passes, with the steps the
optimizer refused to touch and why. If the API is down,
the page says so and shows no numbers.
