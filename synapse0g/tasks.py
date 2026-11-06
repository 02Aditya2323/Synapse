"""Public task sets with automatic answer checks, and the tuning / held-out split.

The split is by a hash of the task id. The tuner only sees `calib` tasks; every reported number
comes from `test` tasks it never saw. Tuning and reporting on the same tasks would make every
saving look better than it is.
"""

from __future__ import annotations

import hashlib
import json
import re
import string
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from synapse0g.registry import ROOT, USER_AGENT

RAW = ROOT / "data" / "raw"


@dataclass(frozen=True)
class Task:
    id: str
    kind: str
    question: str
    gold: str
    context: tuple = field(default=(), compare=False)  # HotpotQA: ((title, text), ...)


def split_of(task_id: str) -> str:
    bucket = int(hashlib.sha256(task_id.encode()).hexdigest(), 16) % 100
    return "calib" if bucket < 50 else "test"


def pick(tasks: list[Task], split: str, n: int) -> list[Task]:
    return [t for t in tasks if split_of(t.id) == split][:n]


# ---- GSM8K: grade-school maths, answer is a number ------------------------------
def load_gsm8k() -> list[Task]:
    path = RAW / "gsm8k_test.jsonl"
    if not path.exists():
        _download(
            "https://raw.githubusercontent.com/openai/grade-school-math/master/grade_school_math/data/test.jsonl",
            path,
        )
    out = []
    for i, line in enumerate(path.read_text(encoding="utf-8").splitlines()):
        row = json.loads(line)
        gold = row["answer"].split("####")[-1].strip().replace(",", "")
        out.append(Task(f"gsm8k-{i:04d}", "math", row["question"], gold))
    return out


def _last_number(text: str) -> str | None:
    m = re.findall(r"-?\d[\d,]*\.?\d*", text or "")
    return m[-1].replace(",", "").rstrip(".") if m else None


def check_math(task: Task, answer: str) -> bool:
    tail = (answer or "").split("Answer:")[-1]
    got = _last_number(tail)
    if got is None:
        return False
    try:
        return abs(float(got) - float(task.gold)) < 1e-6
    except ValueError:
        return False


# ---- HotpotQA: multi-hop questions over 10 paragraphs ---------------------------
def load_hotpot() -> list[Task]:
    out = []
    for offset in (0, 100):
        path = RAW / f"hotpot_{offset}.json"
        if not path.exists():
            _download(
                "https://datasets-server.huggingface.co/rows?dataset=hotpotqa/hotpot_qa&config=distractor"
                f"&split=validation&offset={offset}&length=100",
                path,
            )
        for row in json.loads(path.read_text(encoding="utf-8"))["rows"]:
            r = row["row"]
            ctx = tuple(
                (title, "".join(sents)) for title, sents in zip(r["context"]["title"], r["context"]["sentences"], strict=True)
            )
            out.append(Task(f"hotpot-{r['id']}", "research", r["question"], r["answer"], ctx))
    return out


def _normalize(s: str) -> str:
    s = (s or "").lower()
    s = "".join(ch for ch in s if ch not in set(string.punctuation))
    s = re.sub(r"\b(a|an|the)\b", " ", s)
    return " ".join(s.split())


def check_research(task: Task, answer: str) -> bool:
    """Pass when the normalized gold answer appears in the agent's final answer.

    yes/no questions must answer yes or no and nothing contradictory.
    """
    gold, got = _normalize(task.gold), _normalize((answer or "").split("Answer:")[-1])
    if gold in ("yes", "no"):
        words = got.split()
        other = "no" if gold == "yes" else "yes"
        return gold in words[:3] and other not in words[:3]
    return bool(gold) and f" {gold} " in f" {got} "


def check_retail(task: Task, answer: str) -> bool:
    return (answer or "").startswith("reward=1")


def check_supplier(task: Task, answer: str) -> bool:
    """The order names the supplier the policy allows (or none), for the full requested quantity."""
    try:
        order = json.loads(answer or "{}")
    except json.JSONDecodeError:
        return False
    got = order.get("supplier_id") or "none"
    if got != task.gold:
        return False
    if task.gold == "none":
        return True
    try:
        return int(order.get("quantity")) == int(json.loads(task.question)["quantity"])
    except (TypeError, ValueError):
        return False


def check_procurement(task: Task, answer: str) -> bool:
    """Right outcome (buy / ask for clarification / no eligible offer), right supplier, landed cost to
    the rupee, and a justification that names the supplier it recommends."""
    try:
        draft = json.loads(answer or "{}")
    except json.JSONDecodeError:
        return False
    want, got = json.loads(task.gold), draft.get("decision") or {}
    if got.get("status") != want["status"]:
        return False
    if want["status"] != "selected":
        return True
    try:
        close = abs(float(got.get("landed_total")) - want["landed_total"]) <= 1.0
    except (TypeError, ValueError):
        return False
    return got.get("supplier") == want["supplier"] and close and want["supplier"].lower() in (draft.get("justification") or "").lower()


CHECKERS = {"math": check_math, "research": check_research, "retail": check_retail, "supplier": check_supplier,
            "procurement": check_procurement}


def _download(url: str, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=60) as r:
        path.write_bytes(r.read())
