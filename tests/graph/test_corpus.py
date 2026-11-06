"""The committed corpus is what every number comes from; these hold it to the manifest."""

import json

import pytest
import yaml

from synapse0g.graph.bench.corpus import shop_tasks
from synapse0g.graph.bench.corpus.build import MANIFEST, gold_number, select
from synapse0g.graph.bench.tasks import Task, load_tasks, write_tasks

MANIFEST_ENTRIES = yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))


@pytest.mark.parametrize("workflow", sorted(MANIFEST_ENTRIES))
def test_corpus_matches_its_manifest(workflow):
    entry = MANIFEST_ENTRIES[workflow]
    tasks = load_tasks(workflow)
    assert len(tasks) == entry["n"]
    assert len({t.task_id for t in tasks}) == len(tasks)
    assert all(t.workflow == workflow and t.checker == entry["checker"] for t in tasks)


@pytest.mark.parametrize("workflow", sorted(MANIFEST_ENTRIES))
def test_every_workflow_has_40_test_tasks(workflow):
    assert len(load_tasks(workflow, split="test")) >= 40


@pytest.mark.parametrize("workflow", sorted(MANIFEST_ENTRIES))
def test_gold_answer_passes_its_own_checker(workflow):
    # a checker the right answer cannot pass measures nothing
    for task in load_tasks(workflow):
        if workflow == "shop_ops":
            answer = {"reply": "done", "db": {**task.expected, "prices": task.input["db"]["prices"]}}
        else:
            answer = task.expected["answer"] if isinstance(task.expected, dict) else task.expected
        assert task.check(answer).success, task.task_id


def test_a_task_never_shows_its_answer_to_the_workflow():
    for workflow in ("research_fanout", "rag_qa"):
        for task in load_tasks(workflow, n=20):
            assert set(task.input) == {"question", "paragraphs"}


def test_splits_are_disjoint_and_complete():
    all_ids = {t.task_id for t in load_tasks("research_fanout")}
    by_split = [{t.task_id for t in load_tasks("research_fanout", split=s)} for s in ("train", "calib", "test")]
    assert by_split[0] | by_split[1] | by_split[2] == all_ids
    assert sum(len(s) for s in by_split) == len(all_ids)


def test_load_tasks_rejects_an_unknown_split():
    with pytest.raises(ValueError, match="unknown split"):
        load_tasks("rag_qa", split="dev")


def test_write_then_load_round_trips(tmp_path):
    task = Task(task_id="x_1", workflow="wf", checker="math_exact", input={"question": "1+1"}, expected="2")
    write_tasks([task], tmp_path / "wf" / "tasks.jsonl")
    assert load_tasks("wf", root=tmp_path) == [task]


def test_select_takes_the_first_matching_rows_in_order(tmp_path):
    rows = [{"id": str(i), "question": "q", "answer": "a", "type": "bridge" if i % 2 else "comparison",
             "supporting_titles": [], "paragraphs": []} for i in range(10)]
    (tmp_path / "hotpotqa_distractor_validation.jsonl").write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    entry = {"source": "hotpotqa", "n": 2, "checker": "qa_f1", "filter": {"type": "bridge"}}
    assert [t.task_id for t in select(entry, "rag_qa", tmp_path)] == ["hotpot_1", "hotpot_3"]
    with pytest.raises(ValueError, match="only 5 matching"):
        select({**entry, "n": 6}, "rag_qa", tmp_path)


def test_gold_number_needs_the_gsm8k_marker():
    assert gold_number("She pays 2 + 2\n#### 1,234") == "1234"
    with pytest.raises(ValueError, match="####"):
        gold_number("no marker here")


def test_shop_tasks_are_reproducible():
    assert shop_tasks.make_task(3) == shop_tasks.make_task(3)
    assert shop_tasks.make_task(3) != shop_tasks.make_task(4)
