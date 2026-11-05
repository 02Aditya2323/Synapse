# Invalid runs: kept as evidence, never quoted

`math_groq-main_2026-11-05T*`: 8 of 12 tuning runs and 18 of 20 held-out runs of the unchanged
agent crashed. Nearly all were Groq HTTP 429 (free tier, 8,000 tokens per minute); a few were
Groq HTTP 400 (strict tool-argument validation). The harness then counted those crashes as wrong
answers, so a cheaper model on a separate rate-limit bucket looked like it "fixed" tasks.

Fixed afterwards: crashed tasks are left out of comparisons, a run with more than 10% crashes gets
no verdict, and a 429 waits as long as the provider asks. Regression tests:
`test_crashes_in_the_unchanged_runs_are_not_counted_as_fixed_by_the_change`,
`test_no_verdict_when_too_many_runs_crashed`.

`math_groq-main_2026-11-05T2120*` (model `qwen/qwen3.8-27b`): no rate-limit crashes after rotating two
keys, but 30-40% of runs in every arm crashed with Groq HTTP 400 "tool call validation failed": the
model wrote garbled tool calls (for example `<parameter= Networks>` in place of `expression`). The
firewall gave no verdict, as designed. Groq's baseline moved to `openai/gpt-oss-120b`.
