# math agent on groq-main (groq)

Run 2026-11-05T20:57:19Z. Agent: LangGraph `create_react_agent`, unchanged. Tasks: GSM8K test.
Tuning tasks: 12 (`calib`). Reported tasks: 20 (`test`, never seen by the search).
Prices: {"provider": "groq", "tag": "qwen/qwen3.8-27b", "usd_per_mtok_in": 0.8, "usd_per_mtok_out": 4.0, "price_source": "models.yaml"}.

## Held-out result (the numbers to quote)

| measure | unchanged run 1 | unchanged run 2 | optimized | change vs mean of unchanged |
|---|---|---|---|---|
| tasks passed | 2 | 6 | 20 | +400.0% |
| cost, total (0G billed when given) | $0.01523 | $0.02651 | $0.00370 | -82.3% |
| cost per task | $0.00076 | $0.00133 | $0.00018 | -82.3% |
| cost per passed task | $0.00762 | $0.00442 | $0.00018 | -96.9% |
| input tokens | 6,771 | 16,323 | 9,706 | -15.9% |
| output tokens | 2,454 | 3,363 | 3,736 | +28.5% |
| of which hidden reasoning | 0 | 0 | 2,117 | n/a |
| model calls | 16 | 28 | 40 | +81.8% |
| mean time per task | 19.60 s | 19.16 s | 1.93 s | -90.0% |
| median time per task | 17.96 s | 17.91 s | 1.65 s | -90.8% |
| mean time, rate-limit waits removed | 4.25 s | 4.81 s | 1.93 s | -57.3% |
| rate-limit retries | 91 | 96 | 0 | -100.0% |
| crashed runs | 18 | 14 | 0 | -100.0% |

**Firewall on held-out tasks: APPROVE.** Broke 0, fixed 13, unstable in the unchanged runs 6. Cost saved +82.3% (95% interval +71.2% to +88.0%); time saved +57.3% (+48.7% to +63.6%, against mean of the two unchanged runs); tokens saved +7.0%.

## What the search tried (tuning tasks only)

| step | technique | why it was considered | predicted saving | measured saving | passes (base 1, base 2, try) | verdict |
|---|---|---|---|---|---|---|
| s-bf5d66a9 | route:groq-lite | openai/gpt-oss-120b lists $0.150/$0.600 per M tokens vs qwen/qwen3.8-27b $0.800/$4.000 | $0.01342 | $0.00875 | (4, 1, 11) | APPROVE |
| s-bf5d66a9 | reasoning_off | 6,124 of 7,278 output tokens were hidden reasoning | $0.00367 | $0.00370 | (4, 1, 10) | APPROVE |

Not tried, because the predicted saving was under the threshold:

- compact:2 on s-bf5d66a9: predicted $0.000000. about 0 input tokens sit in tool results older than the last two.
- reuse on s-bf5d66a9: predicted $0.000000. identical requests cost $0.000000 in the baseline.
- cap:308 on s-bf5d66a9: predicted $0.000000. longest output was 195 tokens; a cap above it cuts only runaways.
- cap:638 on s-bf5d66a9: predicted $0.000000. longest output was 415 tokens; a cap above it cuts only runaways.
- cap:434 on s-bf5d66a9: predicted $0.000000. longest output was 279 tokens; a cap above it cuts only runaways.

Final policy: s-bf5d66a9: reasoning_off, route:groq-lite

## Spend

This whole run (search and held-out measurement) cost $0.1007 over 224 paid calls. Cap: $1.00.

Steps: s-bf5d66a9 = You solve maths word problems. Work [tools: calculator]

Every call's request id, tokens and billed amount is in the JSON file next to this one.
