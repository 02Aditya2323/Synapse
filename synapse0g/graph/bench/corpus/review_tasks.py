"""Code review tasks, generated: five real functions, one with a planted logic bug.

The functions come from the Python standard library, so they are real code nobody wrote
for this benchmark. Every function appears in one task only, so no code crosses a split.
Each is re-printed from its syntax tree, which strips comments and hides the formatting of
the edit. The bug swaps one comparison or boolean operator (`<` for `<=`, `and` for `or`).
Whether a swap changes behaviour on real input is not proven for each function; the checker
asks only which file was edited.

Five files means guessing scores about 20 %.

    load(210)  -> code_review_0000 .. code_review_0209

The function pool depends on the Python version, so tasks.jsonl is committed and frozen.
Each task records the version that built it.
"""

from __future__ import annotations

import ast
import copy
import difflib
import os
import platform
import random
from pathlib import Path

from synapse0g.graph.bench.tasks import Task

N_TASKS = 210
FILES_PER_TASK = 5
SEED = 20261031
MIN_LINES, MAX_LINES = 5, 35
SKIP_DIRS = {"test", "tests", "idlelib", "site-packages", "turtledemo", "__pycache__", "lib2to3", "_pyrepl",
             "pip", "setuptools", "ensurepip", "_vendor"}
SKIP_MODULES = {"antigravity", "this", "__future__", "__hello__", "__phello__", "_pydecimal", "_pyio", "_strptime"}

SWAPS = {ast.Lt: ast.LtE, ast.LtE: ast.Lt, ast.Gt: ast.GtE, ast.GtE: ast.Gt, ast.Eq: ast.NotEq, ast.NotEq: ast.Eq,
         ast.Is: ast.IsNot, ast.IsNot: ast.Is, ast.In: ast.NotIn, ast.NotIn: ast.In}


def sites(fn: ast.FunctionDef) -> list[tuple]:
    """Every operator a bug can be planted on, in a stable order."""
    found: list[tuple] = []
    for node in ast.walk(fn):
        if isinstance(node, ast.Compare):
            found += [(node, i) for i, op in enumerate(node.ops) if type(op) in SWAPS]
        elif isinstance(node, ast.BoolOp):
            found.append((node, None))
    return found


def plant(fn: ast.FunctionDef, rng: random.Random) -> tuple[ast.FunctionDef, str]:
    """A copy of `fn` with one operator swapped, and which swap it was."""
    copied = copy.deepcopy(fn)
    node, index = rng.choice(sites(copied))
    if index is None:
        old = type(node.op)
        node.op = ast.Or() if old is ast.And else ast.And()
        return copied, f"{old.__name__} -> {type(node.op).__name__}"
    old = type(node.ops[index])
    node.ops[index] = SWAPS[old]()
    return copied, f"{old.__name__} -> {SWAPS[old].__name__}"


def pool(root: Path | None = None) -> list[tuple[str, ast.FunctionDef]]:
    """(file name, function) for every usable stdlib function, in a stable order."""
    root = root or Path(os.__file__).parent
    found: dict[str, ast.FunctionDef] = {}
    for path in sorted(root.rglob("*.py")):
        parts = path.relative_to(root).with_suffix("").parts
        if SKIP_DIRS & set(parts) or parts[-1] in SKIP_MODULES:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError, ValueError):
            continue  # a stdlib file this Python cannot read as UTF-8 is simply not in the pool
        for fn in tree.body:
            if not isinstance(fn, ast.FunctionDef) or fn.name.startswith("__"):
                continue
            if MIN_LINES <= len(ast.unparse(fn).splitlines()) <= MAX_LINES and sites(fn):
                found.setdefault("_".join([*parts, fn.name]).strip("_") + ".py", fn)
    return list(found.items())


def changed_lines(before: str, after: str) -> list[str]:
    return [line for line in difflib.ndiff(before.splitlines(), after.splitlines()) if line[:2] in ("- ", "+ ")]


def build(n: int = N_TASKS, functions: list | None = None) -> list[Task]:
    functions = list(functions if functions is not None else pool())
    rng = random.Random(SEED)
    rng.shuffle(functions)
    if len(functions) < n * FILES_PER_TASK:
        raise ValueError(f"{len(functions)} functions cannot fill {n} tasks without reuse")
    tasks = []
    for index in range(n):
        chosen = functions[index * FILES_PER_TASK:(index + 1) * FILES_PER_TASK]
        buggy = rng.randrange(FILES_PER_TASK)
        planted, mutation = plant(chosen[buggy][1], rng)
        files = [{"name": name, "code": ast.unparse(planted if i == buggy else fn)}
                 for i, (name, fn) in enumerate(chosen)]
        gold = files[buggy]["name"]
        tasks.append(Task(
            task_id=f"code_review_{index:04d}",
            workflow="code_review",
            checker="review_file",
            input={"files": files},
            expected={"answer": gold, "files": [f["name"] for f in files]},
            meta={"mutation": mutation, "changed": changed_lines(ast.unparse(chosen[buggy][1]), files[buggy]["code"]),
                  "python": platform.python_version()},
        ))
    return tasks


def load(n: int | None = None) -> list[Task]:
    return build(n or N_TASKS)
