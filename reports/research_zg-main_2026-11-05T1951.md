# research agent on zg-main (zerog)

Run 2026-11-05T19:51:24Z. Agent: LangGraph `create_react_agent`, unchanged. Tasks: HotpotQA distractor validation.
Tuning tasks: 30 (`calib`). Reported tasks: 50 (`test`, never seen by the search).
Prices: {"provider": "zerog", "tag": "qwen3.8-flash", "usd_per_mtok_in": 0.19125, "usd_per_mtok_out": 0.59925, "price_source": "zerog catalog"}.

## Held-out result (the numbers to quote)

| measure | unchanged run 1 | unchanged run 2 | optimized | change vs mean of unchanged |
|---|---|---|---|---|
| tasks passed | 40 | 36 | - | - |
| cost, total (0G billed when given) | $0.03036 | $0.03235 | - | - |
| cost per task | $0.00061 | $0.00065 | - | - |
| cost per passed task | $0.00076 | $0.00090 | - | - |
| input tokens | 141,845 | 155,005 | - | - |
| output tokens | 15,003 | 18,638 | - | - |
| of which hidden reasoning | 10,402 | 13,752 | - | - |
| model calls | 133 | 137 | - | - |
| mean time per task | 9.74 s | 10.83 s | - | - |
| median time per task | 7.67 s | 8.00 s | - | - |
| rate-limit retries | 0 | 0 | - | - |
| crashed runs | 0 | 0 | - | - |

**No beneficial change found.** The search approved nothing, so the agent ships unchanged.

## What the search tried (tuning tasks only)

| step | technique | why it was considered | predicted saving | measured saving | passes (base 1, base 2, try) | verdict |
|---|---|---|---|---|---|---|
| s-17890b4a | route:zg-lite | qwen3.7-flash lists $0.029/$0.117 per M tokens vs qwen3.8-flash $0.191/$0.599 | $0.01292 | $0.01266 | (23, 25, 23) | BLOCK: 2 task(s) that passed in both baseline runs now fail twice: hotpot-5ae6050f55429929b0807a5e, hotpot-5a828c8355429966c78a6a50 |
| s-17890b4a | reasoning_off | 5,733 of 8,017 output tokens were hidden reasoning | $0.00344 | $0.00330 | (23, 25, 22) | BLOCK: 2 task(s) that passed in both baseline runs now fail twice: hotpot-5ae6050f55429929b0807a5e, hotpot-5abbf698554299114383a0b5; passes fell to 22 from 23 |

Blocked trade-offs. The firewall refused these; a developer could still choose one knowingly:

- route:zg-lite: 81% cheaper, -10% time saved, passes 23 vs 24 unchanged (mean), 2 stable task(s) broken.
- reasoning_off: 22% cheaper, +21% time saved, passes 22 vs 24 unchanged (mean), 2 stable task(s) broken.

Not tried, because the predicted saving was under the threshold:

- compact:2 on s-17890b4a: predicted $0.000000. about 0 input tokens sit in tool results older than the last two.
- reuse on s-17890b4a: predicted $0.000000. identical requests cost $0.000000 in the baseline.
- cap:718 on s-17890b4a: predicted $0.000000. longest output was 468 tokens; a cap above it cuts only runaways.

Final policy: unchanged

## Spend

This whole run (search and held-out measurement) cost $0.1127 over 582 paid calls. Cap: $1.50.

Steps: s-17890b4a = You are a research assistant. Answer [tools: search]

Every call's request id, tokens and billed amount is in the JSON file next to this one.
