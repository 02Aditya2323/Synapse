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

## Not known yet: settle in the integration spike, before building on it

- Whether the compute share lands in the **same Router account** the inference key spends from. The
  split's `destination` word is a bytes32 we have not matched to an account yet.
- What `comfy-cli compute key mint` returns: is it an `sk-` Router key for `router-api.0g.ai/v1`?
  The comfy docs do not say. Our code needs only a base URL and a key (`models.yaml`, `.env`).
- When credit is delivered after a trade (the docs give no timing). The page records the Router
  balance each time it is read, and shows a rise in deposits only when it has seen one.
- That the account bills in 0G: new 0G console accounts bill in USD; comfy requires 0G.

## Steps on deployment day (needs mainnet 0G in the owner wallet)

```bash
npx -y comfy-cli@latest --help              # Node 22+. Read the npm page's connection steps first
npx -y comfy-cli@latest agent register --name 'Synapse' --description 'Optimizes AI agents: faster, cheaper, proven unchanged' --owner <OWNER_ADDRESS>
# open the claim link within 15 minutes, sign in with the owner wallet (pays gas), then:
npx -y comfy-cli@latest agent register --complete <REQUEST_ID>
```

Then launch the token from the agent's page on comfy.fun (the CLI cannot launch on mainnet: comfy does
not publish the manifest). Keep the compute share on. Then:

```bash
npx -y comfy-cli@latest compute key mint --agent <AGENT_ID> --limit-og <DAILY_0G_LIMIT>
```

Fill in `comfy.yaml`: `agent_id`, `owner`, `token`, `launch_tx`, `token_page`, and `team_wallets`
(every wallet the team trades from). Put the minted key and a Router management key in `.env`:
`ZG_API_KEY=...`, `ZG_MANAGEMENT_KEY=mk-...`. Never commit either.

## What the prize asks for, and where it is shown

| Requirement ("Agents That Pay for Themselves") | Where |
|---|---|
| Incoming funds → credit → inference, with tx hashes and request ids | `#/agent`: trades (tx links), credit arrivals, receipts (request ids) |
| Runs on its own, no manual top-up | `synapse0g agent run`; the inference stage says "team deposit" until earned credit has arrived |
| Spend cap and graceful low funds | per-job cap, the reserve, and modes full / reduced / static / paused (`service.py`) |
| Agent ID, token address, fees tab showing compute delivered | identity strip on `#/agent`; the token's comfy fees tab |
| Not: revenue only from the team trading its own token | trades from `team_wallets` are shown, greyed and never counted |
