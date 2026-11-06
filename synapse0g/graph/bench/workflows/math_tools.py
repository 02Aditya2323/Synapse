"""math_tools: GSM8K with a calculator tool. Showcases cse, then downgrade and loop_cutoff.

    plan(model)  [step(model)  calculate(tool)] x 1..MAX_CALCS  step(model, DONE)  check(model)

The model never does arithmetic itself: it names one calculation at a time and the
calculator computes it. `calculator` is pure and deterministic, so an expression the model
writes twice is a duplicate node, and small models write the same expression constantly.
"""

from __future__ import annotations

from typing import Any

from synapse0g.graph.bench.tools.calculator import calculator
from synapse0g.graph.bench.tools.parsing import clip, field
from synapse0g.graph.bench.workflows._llm import declare, guard, last_given, prompt, transform
from synapse0g.graph.bench.workflows.research_fanout import Llm
from synapse0g.graph.core.sir import Effect, Graph
from synapse0g.graph.frontend.dsl import workflow as trace

MAX_CALCS = 5
MAX_TURNS = 6  # step visits, including the one that says DONE

PLAN_SYSTEM = (
    "You plan how to solve a math word problem. List the calculations needed, in order, "
    "one short line each: what is being found and from which numbers in the problem. "
    "Do not compute the results. At most 5 lines. The last line must find what the question asks."
)
STEP_SYSTEM = (
    "You solve a math word problem one calculation at a time. A calculator does the arithmetic. "
    "Do the next line of the plan that has no result yet. Reply with exactly one line:\n"
    "CALC: <one arithmetic expression using only numbers and + - * / ( )>\n"
    "or, when every line of the plan has a result:\nDONE"
)
CHECK_SYSTEM = (
    "You check the solution of a math word problem. Given the problem and the calculator results, "
    "state the final answer to the question asked. Reply with exactly one line:\n"
    "FINAL: <number only, no units>"
)


def format_results(results: list[dict]) -> str:
    if not results:
        return "(none yet)"
    return "\n".join(f"{i + 1}. {r['expr']} = {r['value']}" for i, r in enumerate(results))


def plan_messages(question: str) -> list[dict]:
    return [{"role": "system", "content": PLAN_SYSTEM}, {"role": "user", "content": f"Problem: {question}"}]


def step_messages(question: str, plan: str, results: list[dict]) -> list[dict]:
    return [{"role": "system", "content": STEP_SYSTEM},
            {"role": "user", "content": (f"Problem: {question}\n\nPlan:\n{plan}\n\n"
                                         f"Results so far:\n{format_results(results)}")}]


@declare("math_tools.calculate", kind="tool", effect=Effect.PURE)
def calculate(expression: str) -> Any:
    """A bad expression is a result the model reads next turn, not a crash of the workflow."""
    try:
        return calculator(expression)
    except (ValueError, ArithmeticError, TypeError) as exc:
        return f"error: {clip(str(exc), 120)}"


def check_messages(question: str, results: list[dict]) -> list[dict]:
    return [{"role": "system", "content": CHECK_SYSTEM},
            {"role": "user", "content": f"Problem: {question}\n\nCalculator results:\n{format_results(results)}"}]


def run(task_input: dict[str, Any], llm: Llm) -> str:
    question = task_input["question"]
    plan = clip(llm(plan_messages(question), 160), 600)
    results: list[dict] = []
    for turn in range(1, MAX_TURNS + 1):
        expr = field(llm(step_messages(question, plan, results), 48), "CALC")
        if expr is None or len(results) >= MAX_CALCS or turn >= MAX_TURNS:
            break
        results.append({"expr": expr, "value": calculate(expr)})
    text = llm(check_messages(question, results), 32)
    return field(text, "FINAL") or clip(text, 80)


@prompt("math_tools.plan_prompt")
def plan_prompt(question: str) -> dict:
    return {"messages": plan_messages(question), "max_tokens": 160}


@transform("math_tools.parse_plan")
def parse_plan(text: str) -> str:
    return clip(text, 600)


@transform("math_tools.no_results")
def no_results(plan: str) -> list:
    return []


@prompt("math_tools.step_prompt")
def step_prompt(question: str, plan: str, results: list) -> dict:
    return {"messages": step_messages(question, plan, results), "max_tokens": 48}


@transform("math_tools.parse_step", outputs=["expr", "go"])
def parse_step(text: str) -> dict:
    expr = field(text, "CALC")
    return {"expr": expr, "go": expr is not None}


@transform("math_tools.add_result")
def add_result(results: list, expr: str, value: Any) -> list:
    return results + [{"expr": expr, "value": value}]


@prompt("math_tools.check_prompt")
def check_prompt(question: str, r0: list, r1: list | None, r2: list | None, r3: list | None, r4: list | None,
                 r5: list | None) -> dict:
    return {"messages": check_messages(question, last_given(r0, r1, r2, r3, r4, r5)), "max_tokens": 32}


@transform("math_tools.final_answer")
def final_answer(text: str) -> str:
    return field(text, "FINAL") or clip(text, 80)


def graph() -> Graph:
    turns: list = []

    @trace(name="math_tools")
    def math(question: str) -> str:
        plan = parse_plan(plan_prompt(question))
        results = no_results(plan)
        gathered = [results]
        go = None
        for _ in range(MAX_CALCS):
            text = step_prompt(question, plan, results)
            expr, next_go = parse_step(text)
            if go is not None:
                turns.append(([text, expr], go.ref))
            value = calculate(expr)
            results = add_result(results, expr, value)
            turns.append(([value, results], next_go.ref))
            gathered.append(results)
            go = next_go
        return final_answer(check_prompt(question, *gathered))

    return guard(math.compile(), turns)


workflow = graph  # `synapse compile bench.workflows.math_tools:workflow`
