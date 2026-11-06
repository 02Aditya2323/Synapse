# synapse0g as a paid agent on comfy

How synapse0g pays for its own model calls, what has been checked against the chain and the docs,
and the steps left to do once the team has mainnet 0G. The dashboard page `#/agent` ("Funding and
jobs") reads every stage below live; until a stage happens it says so.

## The loop

1. Someone buys or sells the Synapse token on comfy (0G mainnet, chain 16661).
2. The trade pays fees in native 0G. On chain: a `ChargeTaken` event on the hook, beside the pool's `Swap`.
3. The fees are split per the token: comfy's part, the creators' part, and a compute share of the
   creators' part. On chain: `splitOf(poolId)` on the fee splitter.
4. The compute share is credited to a 0G compute account. Read from the 0G Router:
   `GET /v1/account/balance` (needs a management key, `mk-...`, with `account:read`).
5. The agent's model calls spend that credit through a capped key. Each call returns a request id and
   a billed cost, which synapse0g already records per call (`upstream.CallRecord`).

Kept separate everywhere: fees charged on chain, credit delivered, credit spendable, inference spent,
creator earnings claimable. One is never computed from another and shown as if read.

## Checked on 6 Nov 2026

| Fact | How it was checked |
|---|---|
| Contract addresses and event topics in `comfy.yaml` | Read 145 `Launched` events from the launcher over the public RPC |
| `ChargeTaken(bytes32 indexed poolId, address indexed router, bool isBuy, uint256 quoteAmount, uint256 baseFee, uint256 openingFee, uint256 creatorTax, address currency)` | keccak of this signature equals the event's topic0 on chain. The docs page prints the topic with one extra character |
| `splitOf` returns `(set, protocol bps, creator bps, compute bps, destination)` | Recent launches: 3000 / 7000 split of the base fee; compute share 0, 500 or 2500 bps of the creators' part |
| The base fee was 1% of the trade on the pools read | A 10 0G buy paid 0.1 0G (tx `0xa5b2e784…8115`); a 0.32 0G buy paid 0.0032 0G |
| The whole read path works | `funding.snapshot` on a live token with a 25% compute share: trade, fee, split and compute part all read from chain |
| Router balance and usage endpoints | docs.0g.ai Router "Deposits & Billing": `/v1/account/balance`, `/v1/account/usage/stats`, `/v1/account/usage/history`; `402 insufficient_balance` when empty |
| 0G to USD rate | Implied by the Router catalog, which prices each model in neuron and in USD (1 0G = $0.1466 on 6 Nov) |

## Checked on 7 Nov 2026, before launch

| Fact | How it was checked |
|---|---|
| Launch policy at block 49,482,450: base fee 100 bps, max creator tax 1,000 bps, max opening fee 9,800 bps, max delay 495 s, max decay 3,600 s, split 3,000 protocol / 7,000 creators | `launchConfig()` (`0x5293fc6f`) on the launcher, decoded with the mainnet `Config` struct from comfy-cli 0.9.4's SDK (`launch/index.js`) |
| Launch fee 0; a launch costs gas only, 0.0056 to 0.0099 0G on the last 23 | `launchFee()` (`0xcf3cf573`); value and gas of each recent launch transaction |
| Compute share up to 95% with no co-recipients: the creator keeps at least 5% of creator fees | `CREATOR_FLOOR_BPS = 500`, `computeRoomBps` in the SDK's `launch/compute.js` |
| Fees are taken from the amount the wallet sends: on a token with 10% tax, a buy of X pays 0.01 X base fee and 0.10 X tax | 13 taxed trades on chain: `quoteAmount` equals the transaction's value; one buyer paid an 87% opening fee on 0.5 0G |
| Credit reaches the compute account through comfy's keeper, which can hold it (`below_delivery_floor`, `delivery_cap`, `keeper_off`, `share_queued`) | comfy-cli 0.9.4 `compute.js`, `waitWords`; the real state is read with `comfy compute status --agent <id> --verbose` |
| 0G admits a request only if deposit + credit - pending + 0.8 x Payment Layer vault covers 0.01 0G | Router reference, "Admission" (0gfoundation.github.io/0g-router) |
| Both agent models are on comfy's and 0G's lists: qwen3.8-flash, qwen3.7-flash | `comfy compute models`; the Router catalog |

## Our launch settings

| Setting | Value | Why |
|---|---|---|
| Name / ticker / category | Synapse / SYNAPSE / tools | final at launch |
| Pitch | Compiles AI agents to run faster and cheaper, validated against your tests. | can change later |
| Image | `assets/synapse-token.png` | can change later; comfy screens it |
| Creator tax | 10% on buys and sells (if the live policy still allows it) | funds inference; shown to every trader |
| Opening fee | 0% | a default opening fee takes up to 98% of an early buy |
| Compute share | 95%, no co-recipients | the most the creator floor allows |
| Trading opens | as soon as allowed | |

Per 0G a trader sends, at these settings: 0.003 to comfy, 0.107 to the creators, of which 0.10165 to compute
and 0.00535 claimable by the creator. A 0.30 0G buy: about 0.0305 0G of compute credit, if the keeper
delivers it. 0G admits nothing under 0.01 0G, so with an empty Router ledger about 0.0125 0G must sit in the
vault (0.123 0G of trading) before the first call can run. None of this is shown as true until measured.

## Inference: direct to the Router, never the team's key

The agent calls `https://router-api.0g.ai/v1` with its own capped key, `ZG_AGENT_KEY` (provider
`zerog-agent` in `models.yaml`, models `agent-main` and `agent-lite`). No comfy local proxy, and no
fallback to `0G_API_KEY`: without its key the agent stops and says so. Every call goes through
`synapse0g/spend.py`:

- two calls at a time;
- each reserves its worst case (request bytes x input price + max_tokens x output price, in 0G)
  against the job's cap, the key's remaining limit and the admissible funds, atomically, in SQLite;
- it settles at the charge 0G reports (`x_0g_trace.billing.total_cost`, wei);
- a timeout or a 5xx keeps its worst case held as uncertain until matched in the usage history;
  a 4xx refusal is released (not billed);
- 402 and the caps (403, 429 `credit_limit_exceeded`) stop the job as waiting, never as crashed;
  other 429s wait as long as `Retry-After` asks, three or four attempts at most.

`synapse0g agent check` reads the account (read-only management key `ZG_MANAGEMENT_KEY` with
`account:read` and `keys:read`): billing currency, address, admissible funds, the key's caps as 0G
echoes them, and the two models. `synapse0g agent submit run:<task> --paid` then `agent run --once --paid`
runs one supplier request on the compiled graph, paid by the agent, with request ids and charges.

## Steps, in order (signatures and keys are the owner's; read-only checks are ours)

1. On pc.0g.ai as wallet A: confirm billing has switched to 0G; pause any other jobs on that account.
   Make the read-only `mk-` key (`account:read`, `keys:read`) and the agent key `synapse-agent`
   (credit limit, daily reset, expiry, models qwen3.8-flash and qwen3.7-flash). Both into `.env`.
2. `synapse0g agent check`: no blockers except funds.
3. `comfy agent register --owner 0x0C81...8178` (we run it); the owner opens the claim link within 15
   minutes and signs. Inspect whether the claim already launched and linked a token before launching.
4. Launch with the settings above; confirm the share on chain (`splitOf`), including a queued share.
5. `comfy agent register --complete <request id>`; fill `comfy.yaml` (`agent_id`, `token`, `launch_tx`,
   `token_page`); `comfy compute status --agent <id> --verbose`; confirm its compute destination is
   the account `agent check` read.
6. Quote a 0.30 0G buy on the trade desk; fund wallet B with that plus gas; buy once the opening fee
   shows 0.
7. Watch: ChargeTaken, then delivery (`compute status`), then admissible funds (`agent check`). Then one
   bounded paid run. No claim of creator fees in the first test.

## What the prize asks for, and where it is shown

| Requirement ("Agents That Pay for Themselves") | Where |
|---|---|
| Incoming funds → credit → inference, with tx hashes and request ids | `#/agent`: trades (tx links), credit arrivals, receipts (request ids) |
| Runs on its own, no manual top-up | `synapse0g agent run`; the inference stage says "team deposit" until earned credit has arrived |
| Spend cap and graceful low funds | per-job cap, the reserve, and modes full / reduced / static / paused (`service.py`) |
| Agent ID, token address, fees tab showing compute delivered | identity strip on `#/agent`; the token's comfy fees tab |
| Not: revenue only from the team trading its own token | trades from `team_wallets` are shown, greyed and never counted |
