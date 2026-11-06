"""Arithmetic for math_tools. Parses the expression; never `eval`s it."""

from __future__ import annotations

import ast
import math
import operator
import re

_THOUSANDS = re.compile(r"(\d),(\d{3})(?!\d)")
_PERCENT = re.compile(r"(\d+(?:\.\d+)?)\s*%(?!\s*\d)")  # "20%" is a percentage, "20 % 3" is modulo

_BINARY = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_UNARY = {ast.UAdd: operator.pos, ast.USub: operator.neg}
_FUNCS = {"abs": abs, "round": round, "min": min, "max": max, "sqrt": math.sqrt, "floor": math.floor,
          "ceil": math.ceil}
_MAX_POWER = 100  # 9**9**9 would hang the run


def _eval(node: ast.AST) -> float:
    if isinstance(node, ast.Expression):
        return _eval(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _BINARY:
        left, right = _eval(node.left), _eval(node.right)
        if isinstance(node.op, ast.Pow) and abs(right) > _MAX_POWER:
            raise ValueError(f"exponent {right} is too large")
        return _BINARY[type(node.op)](left, right)
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY:
        return _UNARY[type(node.op)](_eval(node.operand))
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _FUNCS and not node.keywords:
        return _FUNCS[node.func.id](*[_eval(a) for a in node.args])
    raise ValueError(f"unsupported expression element: {ast.dump(node)[:60]}")


def clean_expression(expression: str) -> str:
    """Forgive what small models write: `$1,200 x 3 = 3600` -> `1200 * 3`."""
    expr = str(expression).split("=", 1)[0].strip()  # the calculator computes; a stated result is ignored
    expr = expr.replace("$", "").replace("×", "*").replace("÷", "/").replace("^", "**")
    expr = expr.replace(" x ", " * ").replace(" X ", " * ")
    # thousands separators only: a comma between a digit and exactly 3 digits
    while (new := _THOUSANDS.sub(r"\1\2", expr)) != expr:
        expr = new
    return _PERCENT.sub(r"(\1/100)", expr)


def calculator(expression: str) -> float:
    """Evaluate one arithmetic expression, e.g. "(48 / 2) * 3". Raises ValueError on anything else."""
    expr = clean_expression(expression)
    if not expr:
        raise ValueError("empty expression")
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as exc:
        raise ValueError(f"not an arithmetic expression: {expression!r}") from exc
    value = _eval(tree)
    if isinstance(value, float) and value.is_integer() and abs(value) < 1e15:
        return int(value)
    return round(value, 6) if isinstance(value, float) else value
