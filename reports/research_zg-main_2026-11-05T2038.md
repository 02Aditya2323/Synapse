# research agent on zg-main (zerog)

Run 2026-11-05T20:38:26Z. Agent: LangGraph `create_react_agent`, unchanged. Tasks: HotpotQA distractor validation.
Tuning tasks: 30 (`calib`). Reported tasks: 50 (`test`, never seen by the search).
Prices: {"provider": "zerog", "tag": "qwen3.8-flash", "usd_per_mtok_in": 0.19125, "usd_per_mtok_out": 0.59925, "price_source": "zerog catalog"}.

## Held-out result (the numbers to quote)

| measure | unchanged run 1 | unchanged run 2 | optimized | change vs mean of unchanged |
|---|---|---|---|---|
| tasks passed | 37 | 36 | - | - |
| cost, total (0G billed when given) | $0.03306 | $0.03025 | - | - |
| cost per task | $0.00066 | $0.00060 | - | - |
| cost per passed task | $0.00089 | $0.00084 | - | - |
| input tokens | 151,356 | 139,129 | - | - |
| output tokens | 17,070 | 16,417 | - | - |
| of which hidden reasoning | 12,239 | 11,648 | - | - |
| model calls | 137 | 133 | - | - |
| mean time per task | 10.40 s | 9.83 s | - | - |
| median time per task | 7.83 s | 7.30 s | - | - |
| mean time, rate-limit waits removed | 10.40 s | 9.83 s | - | - |
| rate-limit retries | 0 | 0 | - | - |
| crashed runs | 0 | 0 | - | - |

**No beneficial change found.** The search approved nothing, so the agent ships unchanged.

## What the search tried (tuning tasks only)

| step | technique | why it was considered | predicted saving | measured saving | passes (base 1, base 2, try) | verdict |
|---|---|---|---|---|---|---|
| s-17890b4a | route:zg-lite | qwen3.7-flash lists $0.029/$0.117 per M tokens vs qwen3.8-flash $0.191/$0.599 | $0.01442 | $0.01398 | (23, 24, 22) | BLOCK: 2 task(s) that passed in both unchanged runs failed twice: hotpot-5a828c8355429966c78a6a50, hotpot-5a7d54165542995f4f402256; passes fell to 22 from 23 |
| s-17890b4a | reasoning_off | 7,556 of 9,982 output tokens were hidden reasoning | $0.00453 | $-0.00143 | (23, 24, 19) | BLOCK: 2 task(s) that passed in both unchanged runs failed twice: hotpot-5abbf698554299114383a0b5, hotpot-5a87c13f5542996e4f30890c; passes fell to 21 from 23; no saving with 95% confidence: cost +2.4% [-24.4%, +23.0%], time +6.4% [-21.4%, +27.8%] |

Blocked trade-offs. The firewall refused these; a developer could still choose one knowingly:

- route:zg-lite: 80% cheaper, -14% time saved, passes 22 vs 23.5 unchanged (mean), 2 stable task(s) broken.
- reasoning_off: 2% cheaper, +6% time saved, passes 19 vs 23.5 unchanged (mean), 2 stable task(s) broken.

Not tried, because the predicted saving was under the threshold:

- cache_marks on s-17890b4a: predicted $0.000469. 2,760 input tokens repeated a previous prompt of 1,024+ tokens without a cache hit; cached tokens bill at 11% of the input price (measured).
- compact:2 on s-17890b4a: predicted $0.000068. about 353 input tokens sit in tool results older than the last two.
- reuse on s-17890b4a: predicted $0.000000. identical requests cost $0.000000 in the baseline.
- cap:1829 on s-17890b4a: predicted $0.000000. longest output was 1209 tokens; a cap above it cuts only runaways.

Final policy: unchanged

## Spend

This whole run (search and held-out measurement) cost $0.1549 over 768 paid calls. Cap: $1.50.

Steps: s-17890b4a = You are a research assistant. Answer [tools: search]

Every call's request id, tokens and billed amount is in the JSON file next to this one.
