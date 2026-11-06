"""The procurement agent under test: the same plain-Python agent run three ways on the same documents.

- `as_written`: `handle_request(...)` called directly, every step one after another.
- `compiled`: the same function after `synapse0g.compile`, run by the synapse0g runtime.
- `hand_parallel`: the same step functions, with the requirements and every quotation read at the
  same time in a thread pool: what a developer could write by hand, knowing which steps are
  independent.

Comparison runs never file anything: `PROCUREMENT_STAGING=1` makes the filing step a no-op in the
two hand-run arms, and the compiled arm skips it as it skips every external step in a test. All
three read the same files, so a difference is the optimizer's, not the data's.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from functools import cache

from synapse0g.agents.langgraph_agents import AgentResult
from synapse0g.agents.procurement_desk import DATA, index
from synapse0g.openai import routed
from synapse0g.registry import ROOT
from synapse0g.tasks import Task, split_of

APP_PATH = ROOT / "examples" / "procurement_agent.py"
os.environ["PROCUREMENT_STAGING"] = "1"  # nothing is filed during a comparison


@cache
def example():
    spec = importlib.util.spec_from_file_location("procurement_agent", APP_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules["procurement_agent"] = module  # so its functions can be found by module name
    spec.loader.exec_module(module)
    return module


def load_procurement() -> list[Task]:
    return [Task(f"procurement-{r['id']}", "procurement", json.dumps({"scenario": r["id"]}), json.dumps(r["answer"]))
            for r in index() if r["id"] != "PR-DEMO"]


def split(name: str, n: int) -> list[Task]:
    return [t for t in load_procurement() if split_of(t.id) == name][:n]


def inputs(task_or_id) -> dict:
    sid = task_or_id if isinstance(task_or_id, str) else json.loads(task_or_id.question)["scenario"]
    folder = DATA / sid
    return {"request": (folder / "request.txt").read_text(encoding="utf-8"),
            "policy_path": str(DATA / "purchasing_policy.txt"),
            "quotation_paths": sorted(str(p) for p in folder.glob("quotation_*"))}


def _result(draft, error, t0) -> AgentResult:
    wall = time.perf_counter() - t0
    if error:
        return AgentResult("", error, wall, 0, {})
    return AgentResult(json.dumps(draft, default=str), None, wall, 0, {"decision": draft.get("decision")})


def as_written(task: Task, base_url: str, token: str, model: str) -> AgentResult:
    t0 = time.perf_counter()
    try:
        with routed(base_url, token):
            draft = example().handle_request(**inputs(task))
        return _result(draft, None, t0)
    except Exception as e:  # a crash is a crashed task, never a wrong answer
        return _result(None, f"{type(e).__name__}: {e}"[:500], t0)


def hand_parallel(task: Task, base_url: str, token: str, model: str) -> AgentResult:
    ex = example()
    t0 = time.perf_counter()
    try:
        with routed(base_url, token):
            args = inputs(task)
            policy = ex.load_policy(args["policy_path"])
            documents = ex.load_documents(args["quotation_paths"])
            with ThreadPoolExecutor(max_workers=len(documents) + 1) as pool:
                # threads do not inherit the route by themselves: each call runs inside it
                req = pool.submit(lambda: _in(base_url, token, ex.read_requirements, args["request"], policy))
                offers = list(pool.map(lambda d: _in(base_url, token, ex.extract_quotation, d), documents))
                requirements = req.result()
            evaluation = ex.check_offers(requirements, offers)
            recommendation = ex.recommend(requirements, evaluation)
            draft = ex.draft_purchase_request(requirements, evaluation, recommendation)
            ex.file_for_approval(draft)
        return _result(draft, None, t0)
    except Exception as e:
        return _result(None, f"{type(e).__name__}: {e}"[:500], t0)


def _in(base_url, token, fn, *args):
    with routed(base_url, token):
        return fn(*args)


def compiled_agent(app):
    from synapse0g.graph.runtime.executor import ExecutionMode

    def run(task: Task, base_url: str, token: str, model: str) -> AgentResult:
        t0 = time.perf_counter()
        try:
            with routed(base_url, token):
                execution = app.run(inputs(task), mode=ExecutionMode.BENCH)
            if execution.crashed:
                return _result(None, execution.error or "crashed", t0)
            res = _result(execution.outputs["result"], None, t0)
            res.extra["cache_hits"] = execution.cache_hits
            return res
        except Exception as e:
            return _result(None, f"{type(e).__name__}: {e}"[:500], t0)

    return run


def spec(calib_n: int, test_n: int):
    import synapse0g
    from synapse0g.study import StudySpec

    ex = example()
    return StudySpec(
        name="procurement",
        agent_impl="examples/procurement_agent.py: plain Python, eight steps one after another, no framework",
        dataset="36 purchase requests with 3-4 supplier quotations each (PDF, spreadsheet, email text) and one "
                "purchasing policy; right outcomes computed by code from the facts behind the documents",
        compile=lambda: synapse0g.compile(ex.handle_request, effects=ex.EFFECTS, name="procurement"),
        as_written=as_written, compiled_agent=compiled_agent, hand_parallel=hand_parallel,
        hand_label="test hand-parallel (thread pool)",
        calib=split("calib", calib_n), test=split("test", test_n),
        integration=["from synapse0g.openai import OpenAI      # was: from openai import OpenAI",
                     "app = synapse0g.compile(handle_request, effects=EFFECTS)",
                     "draft = app.invoke(request=..., policy_path=..., quotation_paths=...)"],
    )
