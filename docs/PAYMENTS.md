# Customers pay synapse0g's 0G compute account directly

The paid agent uses direct customer deposits. The current quote includes a 50% optimizer
fee on estimated compute. Comfy is a separate funding integration; its recorded status
and remaining setup are described in [COMFY.md](COMFY.md).

## The loop

1. **Quote** (`POST /api/pay/quote`, page `#/pay`): the customer supplies their wallet and an
   example request or custom source/input. Expected compute plus a 50% optimizer fee is rounded
   up and subject to the configured minimum charge. Supplier estimates use the saved study;
   custom estimates use step count and measured example-call costs. Working room for concurrent
   calls is checked separately against the optimizer account; it is not added to the customer's
   bill. The quote is an estimate, not a measured cost for an unexecuted custom agent.
2. **Pay**: one transaction from the customer's wallet: `depositFor(agent_account)` on 0G's Payment
   Layer (`0xA3b1...cE32`), value = the quote. No contract of ours, no wallet of ours in between, no
   private key on our server. The credit cannot be withdrawn: it can only buy compute.
3. **Confirm** (`POST /api/pay/confirm`, and a watcher every 15 s): the receipt must succeed and carry
   `Deposited(from = the quoted payer, user = the agent account, amount >= the quote)` before the
   quote expires. A deposit pays one quote, once. Team wallets are labelled and never counted as revenue.
4. **Run**: the job starts: the selected supplier or custom graph, every routed call paid by the agent's
   own capped key, two at a time, each reserved first (`spend.py`), capped at what the job needs.
5. **Evidence** (`GET /api/pay/<quote>`, `/api/pay/history`): the payment tx, the account's balances at
   the quote, after the payment and after the job (contract and 0G), each call's request id and 0G
   charge, and the result. Unspent credit stays in the account for the next job.

## Checked on chain, 7 Nov 2026

- PaymentVault's verified source (beacon implementation `0x0487f33f...`): `depositFor(address user)
  payable` needs only `user != 0` and `value > 0`; emits `Deposited(address indexed from, address
  indexed user, uint256 amount)`; there is no user withdrawal.
- 258 of the last 259 mainnet deposits were made for another account, so this is the usual path.
- Historical quote under the earlier admission-cost formula: 0.069 0G for the first job
  (compile included), 0.043 after. These are not current prices; use the quote endpoint.
  One optimized run costs 0.0042 0G on average, 0.0051 at most (20 held-out runs).

## Honest limits

- A payment from a team wallet proves the mechanics, not revenue. One outside payer makes it revenue.
- The agent key's cap must cover the jobs: raise its credit limit before longer use.
- The Router's view of a fresh deposit is the docs' "within seconds"; the first real payment measures it.

## Generated delivery and current scope

The paid path accepts pasted/uploaded Python source and input JSON, or a request
for the bundled supplier-check example. Custom source is imported and executed on
the server through `custom.py`; it is not sandboxed. `service.deliver`
uses `codegen.generate_langgraph` to lower the optimized SIR and selected call policy
into Python without an LLM writing the output.

The ZIP includes `optimized_agent.py`, `supplier_agent.py`, `sir_before.json`,
`sir_after.json`, `policy.json`, `OPTIMIZATION.md`, `README.md`, `input.json`, and
`run.json`. It is downloaded through `/api/pay/<quote>/package`. The page also
shows generated source with optimization line markers.

The generated code imports the source agent functions. Run it in an environment
with the original agent's dependencies, including synapse0g and LangGraph; the ZIP
does not bundle those dependencies. Importing these functions is execution reuse
within the delivered app, not a statement about this repository's provenance.
The saved study verdict is distinct from the paid request's own outcome.

## Spend enforcement

`spend.py` reserves funds before calls, limits concurrent calls, and applies the job
cap. When the Router key has a credit limit, that limit is used. Otherwise the server
uses `SYNAPSE0G_AGENT_CAP_0G`, and the page identifies synapse0g as the enforcer.
That fallback constrains this server's guarded calls; it is not a Router-enforced
limit on every possible use of the key. Jobs that stop for lack of available credit
can be resumed using `/api/pay/resume`; completed jobs clear their stop note.

## Redesigned payment interface

`#/pay` starts the quote/payment flow, `#/jobs` lists jobs and payments, and
`#/job/<quote>` shows a job's progress and delivery. Shared UI lives in `paykit.jsx`.
Custom-agent packages include the submitted source rather than `supplier_agent.py`;
actual delivery contents and dependencies depend on the selected agent.
See [DEPLOY.md](DEPLOY.md) before exposing custom source execution publicly.
