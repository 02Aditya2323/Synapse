# Results, 6 Nov 2026

Every number below was measured on real provider calls. 0G costs are 0G's own billed amount per
request (`x_0g_trace.billing`). "Held-out" means tasks the search never saw. Full per-call
records, with 0G request ids, are in `reports/`.

## What to quote

| Agent | Provider | Tasks | Result | Firewall |
|---|---|---|---|---|
| LangGraph maths agent (GSM8K) | Groq `gpt-oss-120b` | 12 held-out | **77.5% cheaper (95% CI 71–82%), 63% faster, 48% fewer tokens, 12/12 → 12/12** | APPROVE |
| tau-bench retail (refunds, exchanges) | 0G `qwen3.8-flash` | 30 held-out | 70% cheaper and 24% faster, but **broke 5 tasks** (29 → 25) and made **more irreversible actions** (37 → 42) | **BLOCK** |

The BLOCK is the demo's strongest moment: a large saving that looked fine on tuning tasks,
refused because it broke answers and took extra money-moving actions.

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

0G: $1.50 billed for these runs. Groq: $0.27.

## Third-party benchmarks

GSM8K and HotpotQA tasks are downloaded at run time. tau-bench (Sierra Research, MIT licence) is
cloned at a pinned commit into `data/vendor/` (not committed); its tasks, tools, policy and reward
are used unchanged so its scores mean what they mean in the benchmark.
