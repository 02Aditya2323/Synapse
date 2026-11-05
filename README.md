# synapse0g

An optimizer for AI agents. It makes an agent cheaper and faster, and checks on the agent's own
tasks that nothing broke before any change ships.

It works on each model call. Agents point their OpenAI-compatible client at the synapse0g
endpoint. Every call is measured: tokens, hidden reasoning tokens, time, the provider's request
id, and on 0G the amount 0G says it billed. A per-step policy then applies techniques: switching
hidden reasoning off, routing a step to a cheaper model on the same provider, shortening old tool
results, capping runaway output, reusing identical read-only requests, and marking the stable
start of each prompt for 0G's prompt cache (cached tokens bill at about 11% of the input price,
measured).

Providers: 0G Compute's router and Groq, both through the OpenAI-compatible chat API. Results:
[RESULTS.md](RESULTS.md).

## How a technique gets approved

`synapse0g bench` runs the whole loop:

1. Run the agent unchanged on tuning tasks, twice, to learn which tasks are stable and how much
   cost and time move by chance.
2. Profile each step and predict what each technique would save. Techniques predicted to save
   under 3% are not tried, and the report says why.
3. Try the best-predicted technique, alongside an unchanged control run on the same tasks at the
   same time, so a provider slowing down affects both. Keep it only if no task that passed both
   unchanged runs now fails (checked twice), the pass count did not drop, and cost or time improved
   with 95% confidence (paired by task, bootstrap over tasks). It is blocked if it makes the agent
   clearly more than 10% slower. Blocked techniques that saved money are listed as trade-offs.
4. Repeat until nothing is left worth trying.
5. Measure the final policy against the unchanged agent on held-out tasks the search never saw.
   Those are the reported numbers. If nothing helped, the report says "no beneficial change".

## Safety rules

- Routing never crosses providers.
- A model response asking for any tool that is not read-only is never reused: reusing it would
  repeat the action. Tools are labelled from their names and descriptions. An unknown tool is
  treated as irreversible until a developer labels it.
- A spending cap stops paid calls before they are sent (`SYNAPSE0G_SPEND_CAP_USD`, or `--cap`).

## Run it

```bash
uv venv .venv && uv pip install -e ".[agents,dev]"
# .env holds 0G_API_KEY and GROQ_API_KEY; never commit it
.venv/bin/python -m pytest -q                  # offline, no keys, no spend
synapse0g models                                # prices from 0G's live catalog
synapse0g bench --agent research --model zg-main --calib 30 --test 50 --cap 1.5
synapse0g bench --agent math --model zg-main --calib 30 --test 50 --cap 1.5
```

Reports land in `reports/`: a markdown summary and a JSON file with every call's request id,
tokens and billed amount.
