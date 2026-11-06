# Results, 6 Nov 2026

Every number below was measured on real provider calls. 0G costs are 0G's own billed amount per
request (`x_0g_trace.billing`). "Held-out" means tasks the search never saw. Full per-call
records, with 0G request ids, are in `reports/`.

## What to quote

| Agent | Provider | Tasks | Result | Firewall |
|---|---|---|---|---|
| **Supplier-check app** (plain LangGraph chain, 3 lines added): graph passes + per-call policy | 0G `qwen3.8-flash` | 20 held-out | **3.48x faster (median per task, 55.5 → 15.4 s), 72% cheaper billed (95% CI 71–74%), 20/20 → 20/20** | APPROVE |
| Research workflow graph (HotpotQA): plan, search each sub-question in parallel, summarise, answer | 0G `qwen3.8-flash` | 20 held-out | **1.99x faster (median per task, 11.87 → 6.00 s), 18% cheaper, 11% fewer tokens, 19/20 → 19/20** | APPROVE |
| LangGraph maths agent (GSM8K) | Groq `gpt-oss-120b` | 12 held-out | **77.5% cheaper (95% CI 71–82%), 63% faster, 48% fewer tokens, 12/12 → 12/12** | APPROVE |
| tau-bench retail (refunds, exchanges) | 0G `qwen3.8-flash` | 30 held-out | 70% cheaper and 24% faster, but **broke 5 tasks** (29 → 25) and made **more irreversible actions** (37 → 42) | **BLOCK** |
| Code-review workflow graph, with a step moved to the cheaper model | 0G | 10 tuning | 72% cheaper, but 10/10 → 4/10 | **BLOCK** |

The research workflow's cost is measured tokens x 0G's listed price: that run did not store 0G's
per-request bill, so no 0G credit saving is claimed from it. The tau-bench costs are 0G's own
bill (1,129 of 1,129 requests billed; $0.36 billed vs $1.13 at list price, because 0G charges
cached prompt tokens at about 11% of the input price).

The BLOCKs are the demo's strongest moment: large savings refused because they broke answers or
took extra money-moving actions.

## The supplier-check app, compiled

`examples/supplier_agent.py` is an ordinary LangGraph app written as a chain: read the purchasing
policy, look up quotes, screen reviews, check certificates, decide, place the order. Three lines
connect it (`from synapse0g.openai import OpenAI`, `synapse0g.compile(...)`, `app.invoke(...)`).
Tasks: 60 requests in a synthetic supplier marketplace (`synapse0g/agents/supplier_desk.py`), each with
one right answer computed by code from the policy. Report: `reports/supplier_zg-main_2026-11-06T0719.json`.

1. **Compile** (5 traced calls): the policy summary and the three lookups read nothing from each
   other, so they run together; the policy summary is the same every time, so it is computed once
   and reused; `place_order` is irreversible, so nothing moves, caches or repeats it, and it never
   runs in a test.
2. **Graph changes, 8 tuning tasks**: APPROVE (8/8 in all arms; 49% faster, 16% cheaper).
3. **Per-call changes on top, 8 tuning tasks**: thinking off on quotes, certificates and decision;
   decision and certificate steps routed to `qwen3.7-flash`. Routing the quote step was blocked: it made
   the app 37% slower.
4. **Held-out, 20 tasks**, all arms in one pool, task by task:

| How it ran | Median time | Cost per task (0G billed) | Passed | Orders placed in the test |
|---|---|---|---|---|
| As written (LangGraph runs the chain) | 55.5 s | $0.00225 | 20/20 | 17 (test marketplace) |
| Same nodes, fan-out wired by hand in LangGraph | 30.6 s | $0.00226 | 20/20 | 17 (test marketplace) |
| Compiled by synapse0g | 15.4 s | $0.00063 | 20/20 | 0 (skipped; the order it would place is checked) |

The hand-wired arm is the framework-native comparison: LangGraph gets about half the speed-up when
someone already knows which steps are independent, and none of the cost saving. 280 of 280 held-out
requests carry 0G's bill. An earlier run of the same study (06T0652) had its call-level search
voided by connection errors on our own local endpoint (fixed: its listen backlog was 5); its graph
result was 1.99x faster and 22% cheaper, APPROVE, and is kept in `reports/`.

## The tau-bench BLOCK, task by task

The 5 tasks below passed in both unchanged runs and failed under the cheaper policy (route to
`qwen3.7-flash` plus thinking off). State-changing actions the agent took, in order, from the
per-call records in `reports/retail_zg-main_2026-11-05T2127.json` (tool names are recorded;
arguments are not):

| Task | Unchanged (both runs) | Cheaper policy | What went wrong |
|---|---|---|---|
| 028 | 3 returns | 3 returns, **then cancelled an order** | an order the customer never asked to cancel; database wrong |
| 027 | exchange, transfer to human | **a return**, then exchange, transfer | a refund the task did not call for; database wrong |
| 022 | change user address, change order address, change user address | same, **plus a second order-address change** | extra write; database wrong |
| 029 | 2 exchanges | **no action** | the requested exchanges never happened |
| 019 | returns and exchange | return and exchange | database right, but the agent never told the customer one of the amounts |

So 3 of the 5 are unwanted money-or-order actions (028, 027, 022); 029 failed by doing nothing and
019 by omission. One task (005) went the other way, failing unchanged and passing cheaper. The
firewall blocked the policy, and the agent ships unchanged.

## No beneficial change (reported, not hidden)

- LangGraph research agent (HotpotQA), 0G, two runs: the cheaper model saved ~80% but broke 2
  stable tasks both times; thinking off gave no clear saving. Ships unchanged.
- LangGraph maths agent, 0G: the cheaper model was 52% cheaper but 2.1x slower; thinking off gave no
  clear saving. Ships unchanged. (Trade-off listed in the report.)

## Facts about 0G we measured

- 0G bills cached input tokens at about 11% of the input price ($0.0213 vs $0.1917 per M on
  `qwen3.8-flash`, fitted from 565 billed calls, max error 1 micro-dollar). The docs call this a
  roadmap item; the bills say it is live.
- Explicit `cache_control` marks work through 0G on Qwen: 3,030 of 3,046 tokens cached, billed 88%
  less. Long-prompt agents (tau-bench) already get ~82% of input cached implicitly.
- Turning hidden thinking off (`enable_thinking: false`) cut one answer from 233 to 3 output tokens;
  on `deepseek-v4-flash` it gave a wrong answer. It must go through the firewall.

## Known gaps

- On Groq, `qwen/qwen3.8-27b` writes garbled tool calls that Groq rejects (HTTP 400) in about a
  third of tasks. Groq's baseline is `gpt-oss-120b`. Invalid runs are kept in `reports/invalid/`.
- The held-out report's "failed twice" wording is wrong: held-out tasks are not rechecked, so read
  it as "failed".
- Groq's free tier is 8,000 tokens per minute per key; runs there are small (12 held-out tasks).

## Spend

0G: $1.50 billed for the call-level runs, about $0.25 for the workflow-graph runs, $0.74 for the two supplier-app studies. Groq: $0.27.

## Third-party benchmarks

GSM8K and HotpotQA tasks are downloaded at run time. tau-bench (Sierra Research, MIT licence) is
cloned at a pinned commit into `data/vendor/` (not committed); its tasks, tools, policy and reward
are used unchanged so its scores mean what they mean in the benchmark.
