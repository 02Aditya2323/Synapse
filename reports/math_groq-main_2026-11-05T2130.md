# math agent on groq-main (groq)

Run 2026-11-05T21:30:24Z. Agent: LangGraph `create_react_agent`, unchanged. Tasks: GSM8K test.
Tuning tasks: 10 (`calib`). Reported tasks: 12 (`test`, never seen by the search).
Prices: {"provider": "groq", "tag": "openai/gpt-oss-120b", "usd_per_mtok_in": 0.15, "usd_per_mtok_out": 0.6, "price_source": "models.yaml"}.

## Held-out result (the numbers to quote)

| measure | unchanged run 1 | unchanged run 2 | optimized | change vs mean of unchanged |
|---|---|---|---|---|
| tasks passed | 12 | 12 | 12 | +0.0% |
| cost, total (0G billed when given) | $0.00425 | $0.00438 | $0.00097 | -77.5% |
| cost per task | $0.00035 | $0.00036 | $0.00008 | -77.5% |
| cost per passed task | $0.00035 | $0.00036 | $0.00008 | -77.5% |
| input tokens | 9,454 | 9,758 | 5,654 | -41.1% |
| output tokens | 4,713 | 4,859 | 1,819 | -62.0% |
| of which hidden reasoning | 3,820 | 3,946 | 1,110 | -71.4% |
| model calls | 36 | 37 | 24 | -34.2% |
| mean time per task | 6.52 s | 8.41 s | 1.80 s | -75.9% |
| median time per task | 6.68 s | 8.89 s | 1.71 s | -78.0% |
| mean time, rate-limit waits removed | 4.23 s | 5.45 s | 1.80 s | -62.9% |
| rate-limit retries | 30 | 46 | 0 | -100.0% |
| crashed runs | 0 | 0 | 0 | n/a |

**Firewall on held-out tasks: APPROVE.** Broke 0, fixed 0, unstable in the unchanged runs 0. Cost saved +77.5% (95% interval +71.2% to +81.9%); time saved +62.9% (+48.5% to +72.3%, against mean of the two unchanged runs); tokens saved +48.1%.

## What the search tried (tuning tasks only)

| step | technique | why it was considered | predicted saving | measured saving | passes (base 1, base 2, try) | verdict |
|---|---|---|---|---|---|---|
| s-bf5d66a9 | reasoning_off | 5,565 of 6,716 output tokens were hidden reasoning | $0.00334 | $0.00279 | (9, 9, 9) | APPROVE |
| s-bf5d66a9 | route:groq-lite | openai/gpt-oss-20b lists $0.075/$0.300 per M tokens vs openai/gpt-oss-120b $0.150/$0.600 | $0.00106 | $0.00101 | (9, 9, 9) | APPROVE |

Not tried, because the predicted saving was under the threshold:

- compact:2 on s-bf5d66a9: predicted $0.000000. about 0 input tokens sit in tool results older than the last two.
- reuse on s-bf5d66a9: predicted $0.000000. identical requests cost $0.000000 in the baseline.
- cap:692 on s-bf5d66a9: predicted $0.000000. longest output was 451 tokens; a cap above it cuts only runaways.
- cap:434 on s-bf5d66a9: predicted $0.000000. longest output was 279 tokens; a cap above it cuts only runaways.
- cap:271 on s-bf5d66a9: predicted $0.000000. longest output was 170 tokens; a cap above it cuts only runaways.

Final policy: s-bf5d66a9: reasoning_off, route:groq-lite

## Spend

This whole run (search and held-out measurement) cost $0.0306 over 262 paid calls. Cap: $1.00.

Steps: s-bf5d66a9 = You solve maths word problems. Work [tools: calculator]

Every call's request id, tokens and billed amount is in the JSON file next to this one.
