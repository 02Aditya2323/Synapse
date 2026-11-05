# retail agent on zg-main (zerog)

Run 2026-11-05T21:27:02Z. Agent: tau-bench's reference ToolCallingAgent loop on the OpenAI SDK. Tasks: tau-bench retail (Sierra, MIT): tuning on its train split, reporting on its test split; simulated customer on zg-main.
Tuning tasks: 20 (`calib`). Reported tasks: 30 (`test`, never seen by the search).
Prices: {"provider": "zerog", "tag": "qwen3.8-flash", "usd_per_mtok_in": 0.19125, "usd_per_mtok_out": 0.59925, "price_source": "zerog catalog"}.

## Held-out result (the numbers to quote)

| measure | unchanged run 1 | unchanged run 2 | optimized | change vs mean of unchanged |
|---|---|---|---|---|
| tasks passed | 29 | 29 | 25 | -13.8% |
| cost, total (0G billed when given) | $0.16250 | $0.15297 | $0.04747 | -69.9% |
| cost per task | $0.00542 | $0.00510 | $0.00158 | -69.9% |
| cost per passed task | $0.00560 | $0.00527 | $0.00190 | -65.1% |
| input tokens | 2,501,124 | 2,399,550 | 2,575,397 | +5.1% |
| output tokens | 97,494 | 85,970 | 37,510 | -59.1% |
| of which hidden reasoning | 52,422 | 45,219 | 0 | -100.0% |
| model calls | 372 | 362 | 395 | +7.6% |
| mean time per task | 113.22 s | 106.36 s | 83.45 s | -24.0% |
| median time per task | 108.07 s | 107.22 s | 76.38 s | -29.0% |
| mean time, rate-limit waits removed | 113.22 s | 106.36 s | 83.45 s | -24.0% |
| rate-limit retries | 0 | 0 | 0 | n/a |
| crashed runs | 0 | 0 | 0 | n/a |

**Firewall on held-out tasks: BLOCK.** Broke 5, fixed 1, unstable in the unchanged runs 0. Cost saved +69.9% (95% interval +66.5% to +73.2%); time saved +24.0% (+17.5% to +30.3%, against mean of the two unchanged runs); tokens saved -2.8%.
Reasons: 5 task(s) that passed in both unchanged runs failed twice: tau-retail-test-019, tau-retail-test-022, tau-retail-test-027, tau-retail-test-028, tau-retail-test-029; passes fell to 25 from 29 (of 30 comparable tasks)
Broken tasks: tau-retail-test-019, tau-retail-test-022, tau-retail-test-027, tau-retail-test-028, tau-retail-test-029

## What the search tried (tuning tasks only)

| step | technique | why it was considered | predicted saving | measured saving | passes (base 1, base 2, try) | verdict |
|---|---|---|---|---|---|---|
| s-9bf3df3a | route:zg-lite | qwen3.7-flash lists $0.029/$0.117 per M tokens vs qwen3.8-flash $0.191/$0.599 | $0.03992 | $0.04219 | (17, 17, 17) | APPROVE |
| s-9bf3df3a | reasoning_off | 50,910 of 73,644 output tokens were hidden reasoning | $0.00594 | $0.00836 | (17, 17, 14) | APPROVE |

Not tried, because the predicted saving was under the threshold:

- reuse on s-9bf3df3a: predicted $0.000000. identical requests cost $0.000000 in the baseline.
- cap:2218 on s-9bf3df3a: predicted $0.000000. longest output was 1468 tokens; a cap above it cuts only runaways.
- compact:2 on s-9bf3df3a: predicted $0.001059. about 36,317 input tokens sit in tool results older than the last two.
- cap:4505 on s-9bf3df3a: predicted $0.000000. longest output was 2993 tokens; a cap above it cuts only runaways.
- cap:583 on s-9bf3df3a: predicted $0.000000. longest output was 378 tokens; a cap above it cuts only runaways.

Final policy: s-9bf3df3a: reasoning_off, route:zg-lite

## Spend

This whole run (search and held-out measurement) cost $0.9803 over 3652 paid calls. Cap: $2.50.

Steps: s-9bf3df3a = # Retail agent policy As a [tools: calculate, cancel_pending_order, exchange_delivered_order_items, find_user_id_by_email, find_user_id_by_name_zip, get_order_details, get_product_details, get_user_details, list_all_product_types, modify_pending_order_address, modify_pending_order_items, modify_pending_order_payment, modify_user_address, return_delivered_order_items, think, transfer_to_human_agents]

Every call's request id, tokens and billed amount is in the JSON file next to this one.
