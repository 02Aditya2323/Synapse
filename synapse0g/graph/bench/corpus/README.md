# The benchmark corpus

Every number Synapse reports comes from these tasks. `tasks.jsonl` is committed for each
workflow, so everyone benchmarks exactly the same tasks without downloading anything.
Rebuild with `py -3 -m bench.corpus.build`. **The corpus freezes at H15.**

| Workflow | Source | Licence | Tasks | train | calib | test | Checker |
|---|---|---|---|---|---|---|---|
| `research_fanout` | HotpotQA distractor validation, `comparison` questions | CC BY-SA 4.0 | 260 | 156 | 63 | 41 | `qa_f1` |
| `rag_qa` | HotpotQA distractor validation, `bridge` questions | CC BY-SA 4.0 | 260 | 158 | 52 | 50 | `qa_f1` |
| `math_tools` | GSM8K main test | MIT | 260 | 163 | 51 | 46 | `math_exact` |
| `code_review` | Python standard library functions with a planted bug | PSF-2.0 | 210 | 124 | 45 | 41 | `review_file` |
| `shop_ops` | generated, seeded | ours | 200 | 117 | 42 | 41 | `ops_state` |

## How tasks were chosen

- **Selection:** the first `n` rows matching the manifest filter, in dataset order. Nothing
  is hand-picked. `n` is the smallest round number that gives 40 `test` tasks, and it was
  set from split sizes alone, before any result existed.
- **Splits:** 60/20/20 by SHA-1 of the task id (`bench/splits.py`). A variant shares its base
  task's split. Python's `hash()` is salted per process, so it is not used.
- **No overlap:** the two HotpotQA workflows share no question, and no standard library
  function appears in two `code_review` tasks.

## Checkers

No model decides what counts as correct.

- `qa_f1`: SQuAD-normalised word-overlap F1 of at least 0.6. A yes/no question reads the
  first word only.
- `math_exact`: the last number in the answer equals the gold number.
- `ops_state`: the database the workflow left equals the correct one, as computed by the
  shop's own tools.
- `review_file`: the answer names the buggy file and no other file in the task.

## Limits

- **Small corpus.** Each `test` split has 41 to 50 tasks, so accuracy intervals are wide.
- **`code_review` bugs are planted.** One comparison or boolean operator is swapped in a
  real function. Each swap is a syntactic change: it is not proven to change behaviour on
  real input. Five files per task means guessing scores about 20 %.
- **`code_review` depends on the Python version** that built it (recorded in each task's
  `meta`), so rebuilding on another version gives different tasks. Keep the committed file.
- **`shop_ops` cannot pass in a benchmark.** Its tool calls are stubbed, so the database
  never changes and `ops_state` fails by construction. It demonstrates the refusal and is
  not an accuracy number. A permitted live run reaches the correct database.
- **`research_fanout` and `rag_qa` are closed-world.** Retrieval is BM25 over each question's
  own ten paragraphs, not the open web, so search takes almost no time and any latency win
  comes from model calls.
