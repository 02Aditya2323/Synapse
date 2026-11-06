"""The benchmark corpus: one frozen `bench/corpus/<workflow>/tasks.jsonl` per workflow.

`bench/corpus/build.py` writes those files from `bench/corpus/manifest.yaml`. Everything
downstream (bench driver, firewall, API) reads tasks through `load_tasks`, so a split can
never be computed two different ways.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from synapse0g.graph.bench.checkers import Outcome, check
from synapse0g.graph.bench.splits import SPLITS, split_for

CORPUS_DIR = Path(__file__).parent / "corpus"


class Task(BaseModel):
    task_id: str
    workflow: str
    checker: str                   # a name in bench.checkers.CHECKERS
    input: dict[str, Any]          # the workflow parameters
    expected: Any                  # the workflow never sees this
    meta: dict[str, Any] = {}

    @property
    def split(self) -> str:
        return split_for(self.task_id)

    def check(self, answer: Any) -> Outcome:
        return check(self.checker, answer, self.expected)


def corpus_dir() -> Path:
    configured = os.environ.get("SYNAPSE_CORPUS_DIR")
    return Path(configured) if configured else CORPUS_DIR


def load_tasks(workflow: str, split: str | None = None, n: int | None = None, root: Path | None = None) -> list[Task]:
    """Tasks of a workflow in corpus order, optionally one split, optionally the first n of it."""
    if split is not None and split not in SPLITS:
        raise ValueError(f"unknown split {split!r}. Known: {', '.join(SPLITS)}")
    path = (root or corpus_dir()) / workflow / "tasks.jsonl"
    if not path.exists():
        raise FileNotFoundError(f"{path} is missing. Run: py -3 -m bench.corpus.build")
    tasks: list[Task] = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            task = Task.model_validate_json(line)
            if split is None or task.split == split:
                tasks.append(task)
            if n is not None and len(tasks) >= n:
                break
    return tasks


def write_tasks(tasks: list[Task], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8", newline="\n") as f:
        for task in tasks:
            f.write(json.dumps(task.model_dump(mode="json"), ensure_ascii=False, sort_keys=True) + "\n")
    tmp.replace(path)
