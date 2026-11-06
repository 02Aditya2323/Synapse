"""Build the frozen corpus from bench/corpus/manifest.yaml.

    py -3 -m bench.corpus.build             # download what is missing, write every tasks.jsonl
    py -3 -m bench.corpus.build --workflow rag_qa

Raw downloads go to bench/corpus/raw/ (gitignored). The tasks.jsonl files are committed,
so everyone, and the deployed API, benchmarks exactly the same tasks without downloading.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from collections.abc import Iterator
from pathlib import Path

import yaml

from synapse0g.graph.bench.corpus import review_tasks, shop_tasks
from synapse0g.graph.bench.tasks import CORPUS_DIR, Task, write_tasks

MANIFEST = CORPUS_DIR / "manifest.yaml"
RAW_DIR = CORPUS_DIR / "raw"
HOTPOT_FILE = "hotpotqa_distractor_validation.jsonl"
GSM8K_FILE = "gsm8k_main_test.jsonl"


def _write_raw(path: Path, rows: Iterator[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8", newline="\n") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    tmp.replace(path)


def download_hotpot(path: Path) -> None:
    from datasets import load_dataset

    ds = load_dataset("hotpotqa/hotpot_qa", "distractor", split="validation")
    _write_raw(path, (
        {
            "id": r["id"], "question": r["question"], "answer": r["answer"], "type": r["type"], "level": r["level"],
            "supporting_titles": sorted(set(r["supporting_facts"]["title"])),
            "paragraphs": [
                {"title": t, "text": " ".join(s.strip() for s in sents)}
                for t, sents in zip(r["context"]["title"], r["context"]["sentences"], strict=True)
            ],
        }
        for r in ds
    ))


def download_gsm8k(path: Path) -> None:
    from datasets import load_dataset

    ds = load_dataset("openai/gsm8k", "main", split="test")
    _write_raw(path, ({"id": i, "question": r["question"], "answer": r["answer"]} for i, r in enumerate(ds)))


def _rows(path: Path, download) -> Iterator[dict]:
    if not path.exists():
        print(f"downloading {path.name} ...")
        download(path)
    with path.open(encoding="utf-8") as f:
        for line in f:
            yield json.loads(line)


def gold_number(answer: str) -> str:
    """The gold number follows '####' in a GSM8K answer."""
    if "####" not in answer:
        raise ValueError(f"GSM8K answer has no '####' marker: {answer[-80:]!r}")
    return answer.rsplit("####", 1)[1].strip().replace(",", "")


def hotpot_task(row: dict, workflow: str, checker: str) -> Task:
    return Task(
        task_id=f"hotpot_{row['id']}",
        workflow=workflow,
        checker=checker,
        input={"question": row["question"], "paragraphs": row["paragraphs"]},
        expected={"answer": row["answer"], "supporting_titles": row["supporting_titles"]},
        meta={"type": row.get("type"), "level": row.get("level")},
    )


def gsm8k_task(row: dict, workflow: str, checker: str) -> Task:
    return Task(task_id=f"gsm8k_{row['id']:04d}", workflow=workflow, checker=checker,
                input={"question": row["question"]}, expected=gold_number(row["answer"]))


def select(entry: dict, workflow: str, raw_dir: Path = RAW_DIR) -> list[Task]:
    """The first entry["n"] source rows matching entry["filter"], in dataset order."""
    source, n, checker = entry["source"], entry["n"], entry["checker"]
    wanted = entry.get("filter") or {}
    if source == "shop_generated":
        return shop_tasks.load(n)
    if source == "review_generated":
        return review_tasks.load(n)
    if source == "hotpotqa":
        rows, make = _rows(raw_dir / HOTPOT_FILE, download_hotpot), hotpot_task
    elif source == "gsm8k":
        rows, make = _rows(raw_dir / GSM8K_FILE, download_gsm8k), gsm8k_task
    else:
        raise KeyError(f"unknown source {source!r} for {workflow}")
    tasks: list[Task] = []
    for row in rows:
        if all(row.get(k) == v for k, v in wanted.items()):
            tasks.append(make(row, workflow, checker))
            if len(tasks) >= n:
                break
    if len(tasks) < n:
        raise ValueError(f"{workflow}: manifest wants {n} tasks, source has only {len(tasks)} matching")
    return tasks


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Write bench/corpus/<workflow>/tasks.jsonl from the manifest.")
    ap.add_argument("--workflow", help="only this workflow (default: all)")
    args = ap.parse_args(argv)
    manifest = yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))
    for workflow, entry in manifest.items():
        if args.workflow and workflow != args.workflow:
            continue
        tasks = select(entry, workflow)
        write_tasks(tasks, CORPUS_DIR / workflow / "tasks.jsonl")
        print(f"{workflow}: {len(tasks)} tasks, {dict(sorted(Counter(t.split for t in tasks).items()))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
