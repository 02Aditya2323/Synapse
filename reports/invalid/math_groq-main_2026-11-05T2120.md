# math agent on groq-main (groq)

Run 2026-11-05T21:20:08Z. Agent: LangGraph `create_react_agent`, unchanged. Tasks: GSM8K test.
Tuning tasks: 10 (`calib`). Reported tasks: 12 (`test`, never seen by the search).
Prices: {"provider": "groq", "tag": "qwen/qwen3.8-27b", "usd_per_mtok_in": 0.8, "usd_per_mtok_out": 4.0, "price_source": "models.yaml"}.

## Held-out result (the numbers to quote)

| measure | unchanged run 1 | unchanged run 2 | optimized | change vs mean of unchanged |
|---|---|---|---|---|
| tasks passed | 8 | 8 | - | - |
| cost, total (0G billed when given) | $0.02720 | $0.02630 | - | - |
| cost per task | $0.00227 | $0.00219 | - | - |
| cost per passed task | $0.00340 | $0.00329 | - | - |
| input tokens | 17,409 | 15,191 | - | - |
| output tokens | 3,319 | 3,536 | - | - |
| of which hidden reasoning | 0 | 0 | - | - |
| model calls | 30 | 29 | - | - |
| mean time per task | 17.77 s | 23.33 s | - | - |
| median time per task | 14.51 s | 20.36 s | - | - |
| mean time, rate-limit waits removed | 4.90 s | 5.21 s | - | - |
| rate-limit retries | 51 | 65 | - | - |
| crashed runs | 4 | 4 | - | - |

**No beneficial change found.** The search approved nothing, so the agent ships unchanged.

## What the search tried (tuning tasks only)

| step | technique | why it was considered | predicted saving | measured saving | passes (base 1, base 2, try) | verdict |
|---|---|---|---|---|---|---|
| s-bf5d66a9 | route:groq-lite | openai/gpt-oss-120b lists $0.150/$0.600 per M tokens vs qwen/qwen3.8-27b $0.800/$4.000 | $0.02351 | $0.02740 | (6, 5, 9) | BLOCK: no verdict: 40% of runs in 'calib unchanged 2' crashed (provider errors). Fix the provider or rate limit and run again |

Blocked trade-offs. The firewall refused these; a developer could still choose one knowingly:

- route:groq-lite: 88% cheaper, +48% time saved, passes 9 vs 5.5 unchanged (mean), 0 stable task(s) broken.

Not tried, because the predicted saving was under the threshold:

- compact:2 on s-bf5d66a9: predicted $0.000000. about 0 input tokens sit in tool results older than the last two.
- reuse on s-bf5d66a9: predicted $0.000000. identical requests cost $0.000000 in the baseline.
- cap:316 on s-bf5d66a9: predicted $0.000000. longest output was 200 tokens; a cap above it cuts only runaways.

Final policy: unchanged

## Spend

This whole run (search and held-out measurement) cost $0.1412 over 180 paid calls. Cap: $1.00.

Steps: s-bf5d66a9 = You solve maths word problems. Work [tools: calculator]

Every call's request id, tokens and billed amount is in the JSON file next to this one.
