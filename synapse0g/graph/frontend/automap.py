"""Read an existing codebase and draw its agent as a graph. Nothing is declared and nothing is run.

Nobody declares anything. For Python, the entry function's own statements become the graph, and each
function's effect is worked out from what its body really calls, following calls between local functions.
Everything found here is **inferred**, and the report shows the evidence for each label.

The rules that keep this safe:

- A call we do not recognise makes a function `external`, the restrictive default. Being wrong costs speed.
- Control flow we cannot see through, such as a loop or a branch in the entry function, is refused with its
  line number, never guessed. A model call inside a function counts as stochastic, so it is never merged or cached.

TypeScript is read, not run: its tools and their effects are mapped, so a person can see what could overlap.
"""

from __future__ import annotations

import ast
import importlib.util
import inspect
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from synapse0g.graph.core.sir import Effect, Graph
from synapse0g.graph.frontend import dsl
from synapse0g.graph.frontend.dsl import node, workflow

SKIP_DIRS = {"node_modules", ".git", "venv", ".venv", "__pycache__", "dist", "build", ".next", "site-packages",
             "tests", "test", "scratch"}
RANK = {"pure": 0, "read": 1, "write": 2, "external": 3, "irreversible": 4}

MODEL_CALLS = re.compile(r"(chat\.completions\.create|messages\.create|generate_content|responses\.create"
                         r"|(llm|model|chain|client)\.(invoke|generate|complete|chat)$)")
READ_CALLS = re.compile(r"^(requests\.get|httpx\.get|urllib\.request\.urlopen|urlopen|session\.get|json\.loads?"
                        r"|time\.(sleep|time|perf_counter)|datetime\..*|os\.environ\.get|os\.getenv|urllib\.parse\..*"
                        r"|urllib\.request\.Request|Request)$")
EXTERNAL_CALLS = re.compile(r"(requests\.(post|put|patch|delete)|httpx\.(post|put|patch|delete)|smtplib|subprocess\."
                            r"|os\.(remove|unlink|system|rename)|shutil\.|\.(send|sendmail|commit|insert_one|update_one"
                            r"|delete_one|write|write_text)$|^(send|post|publish|notify|save|write|upload)_\w*$)")
IRREVERSIBLE = re.compile(r"(^|[._])(charge|pay|payment|refund|place_order|transfer_funds|wire_money|delete_account"
                          r"|drop_table|purge|destroy|release_to_prod)(_|$)", re.I)
PURE_BUILTINS = {"len", "str", "int", "float", "bool", "list", "dict", "set", "tuple", "sorted", "sum", "min", "max",
                 "round", "abs", "range", "enumerate", "zip", "map", "filter", "any", "all", "isinstance", "repr",
                 "print", "format", "hash", "reversed", "getattr", "hasattr", "next", "iter", "type", "ValueError",
                 "RuntimeError", "KeyError", "Exception", "TypeError"}
PURE_METHOD = re.compile(r"\.(get|join|split|strip|lower|upper|replace|format|items|keys|values|append|extend|startswith"  # noqa: E501
                         r"|endswith|partition|rsplit|lstrip|rstrip|title|read|decode|encode|pop|update|sort|count"
                         r"|index|find|group|match|search|sub|findall|dumps|loads|load)$")
READ_SQL = re.compile(r"^\s*select\b", re.I)
# A function's own name is evidence. A body that shows nothing is not proof of nothing, and "send_x" means it.
EFFECT_NAME = re.compile(r"^_?(send|post|publish|notify|save|write|upload|delete|remove|update|create|insert|email|submit|"
                         r"deploy|merge|push|commit|book|cancel|order|buy|sell)(_|$)", re.I)
EFFECT_MODULES = {"smtplib", "subprocess", "shutil", "socket", "ftplib", "paramiko", "boto3", "stripe", "twilio",
                  "sendgrid", "psycopg2", "pymongo", "sqlite3", "sqlalchemy", "redis"}


@dataclass
class Func:
    name: str
    file: Path
    line: int
    params: list[str]
    node: ast.FunctionDef
    effect: str = "pure"
    is_model: bool = False
    evidence: list[str] = field(default_factory=list)
    calls: list[str] = field(default_factory=list)
    declared: dict = field(default_factory=dict)  # what the developer stated, which wins over inference


def _call_name(call: ast.Call) -> str:
    parts: list[str] = []
    at: Any = call.func
    while isinstance(at, ast.Attribute):
        parts.append(at.attr)
        at = at.value
    if isinstance(at, ast.Name):
        parts.append(at.id)
    else:  # a method on a call's result or on a literal, such as ", ".join(...)
        parts.append("()")
    return ".".join(reversed(parts))


def _raise(func: Func, effect: str, why: str) -> None:
    func.evidence.append(why)
    if RANK[effect] > RANK[func.effect]:
        func.effect = effect


def _classify(func: Func, local: set[str]) -> None:
    """One function's own evidence, before following calls to other local functions."""
    if IRREVERSIBLE.search(func.name):
        _raise(func, "irreversible", f"its name, {func.name}, says money or destruction")
    elif EFFECT_NAME.search(func.name):
        _raise(func, "external", f"its name, {func.name}, says it changes something")
    for item in ast.walk(func.node):
        names = [a.name for a in item.names] if isinstance(item, ast.Import) else (
            [item.module or ""] if isinstance(item, ast.ImportFrom) else [])
        for name in names:
            if name.split(".")[0] in EFFECT_MODULES:
                _raise(func, "external", f"it imports {name} at line {item.lineno}")
    for call in (n for n in ast.walk(func.node) if isinstance(n, ast.Call)):
        name = _call_name(call)
        line = getattr(call, "lineno", 0)
        short = name.rsplit(".", 1)[-1]
        if short in local and name == short:
            func.calls.append(short)
        elif MODEL_CALLS.search(name):
            func.is_model = True
            _raise(func, "pure", f"model call {name} at line {line}")
        elif IRREVERSIBLE.search(name):
            _raise(func, "irreversible", f"{name} at line {line}")
        elif name == "open":
            mode = call.args[1].value if len(call.args) > 1 and isinstance(call.args[1], ast.Constant) else "r"
            _raise(func, "external" if set(str(mode)) & set("wax+") else "read", f"open(..., {mode!r}) at line {line}")
        elif name.endswith((".execute", ".executemany")):
            first = call.args[0] if call.args else None
            select = isinstance(first, ast.Constant) and READ_SQL.search(str(first.value))
            _raise(func, "read" if select else "external", f"SQL {name} at line {line}")
        elif EXTERNAL_CALLS.search(name):
            _raise(func, "external", f"{name} at line {line}")
        elif READ_CALLS.search(name):
            _raise(func, "read", f"{name} at line {line}")
        elif name in PURE_BUILTINS or PURE_METHOD.search(name):
            continue
        else:
            _raise(func, "external", f"unrecognised call {name or '?'} at line {line}, so it counts as external")


def scan_python(root: Path) -> dict[str, Func]:
    funcs: dict[str, Func] = {}
    files = [root] if root.is_file() else sorted(root.rglob("*.py"))
    for path in files:
        if not root.is_file() and SKIP_DIRS & set(path.relative_to(root).parts):  # a named file is always read
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        for item in tree.body:
            if isinstance(item, ast.FunctionDef) and item.name not in funcs:
                params = [a.arg for a in item.args.args]
                funcs[item.name] = Func(item.name, path, item.lineno, params, item)
    local = set(funcs)
    for func in funcs.values():
        _classify(func, local)
    changed = True
    while changed:  # a function is at least as effectful as what it calls
        changed = False
        for func in funcs.values():
            for callee in set(func.calls):
                other = funcs[callee]
                if RANK[other.effect] > RANK[func.effect]:
                    _raise(func, other.effect, f"it calls {callee}, which is {other.effect}")
                    changed = True
                if other.is_model and not func.is_model:
                    func.is_model = changed = True
    return funcs


class Unmappable(ValueError):
    """The entry function has something we will not guess about. The message says what, and where."""


def _entry_calls(func: Func, funcs: dict[str, Func]) -> list[str]:
    return [c for c in func.calls if c in funcs]


def pick_entry(funcs: dict[str, Func]) -> Func:
    """The function that calls the most local steps, and that nothing else calls."""
    called = {c for f in funcs.values() for c in f.calls}
    ranked = sorted(funcs.values(), key=lambda f: (f.name not in called, len(set(_entry_calls(f, funcs)))),
        reverse=True)
    if not ranked or len(set(_entry_calls(ranked[0], funcs))) < 2:
        raise Unmappable("no function here calls two or more other local functions, so there is no agent to draw")
    return ranked[0]


def _check_body(entry: Func, funcs: dict[str, Func]) -> list[ast.stmt]:
    """Statements the tracer can follow exactly. Anything else is refused with its line."""
    body = list(entry.node.body)
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
        body = body[1:]
    kept: list[ast.stmt] = []
    for stmt in body:
        value = getattr(stmt, "value", None)
        if isinstance(stmt, ast.Expr) and isinstance(value, ast.Call) and _call_name(value) == "print":
            continue  # output for a person, with no bearing on the graph
        allowed = isinstance(stmt, (ast.Assign, ast.Return, ast.Expr))
        for sub in ast.walk(stmt):
            if isinstance(sub, ast.Call) and _call_name(sub) not in funcs and _call_name(sub) not in {"print"}:
                raise Unmappable(f"{entry.name}, line {sub.lineno}: {_call_name(sub) or 'a call'} is not a local step, "
                                 "so the tracer cannot follow the value through it")
            if isinstance(sub, (ast.JoinedStr, ast.BinOp, ast.Subscript, ast.Attribute, ast.IfExp)):
                raise Unmappable(f"{entry.name}, line {stmt.lineno}: the entry function computes a value itself. "
                                 "Move that into a function so it becomes a step")
        if not allowed:
            raise Unmappable(f"{entry.name}, line {stmt.lineno}: {type(stmt).__name__.lower()} control flow decides "
                             "at run time what runs, so it cannot be drawn without guessing")
        kept.append(stmt)
    return kept


def build_graph(entry: Func, funcs: dict[str, Func]) -> Graph:
    """Runs the entry's own statements through the DSL's tracer, with each local function as a declared node."""
    dsl.clear_registry()
    body = _check_body(entry, funcs)
    namespace: dict[str, Any] = {"__synapse_workflow": workflow}
    for name in {c for stmt in body for c in _names_called(stmt)}:
        func = funcs[name]
        namespace[name] = _declare(func)
    params = ", ".join(entry.params)
    module = ast.parse(f"def __traced({params}):\n    pass\n")
    module.body[0].body = body  # type: ignore[attr-defined]
    exec(compile(module, str(entry.file), "exec"), namespace)  # noqa: S102 - only calls that were just checked
    traced = namespace["__traced"]
    traced.__name__ = entry.name
    return workflow(name=entry.name)(traced).compile()


def _names_called(stmt: ast.stmt) -> list[str]:
    return [_call_name(n) for n in ast.walk(stmt) if isinstance(n, ast.Call) and _call_name(n) != "print"]


def _declare(func: Func):
    """A node whose body imports the real function only when it really runs. Scanning executes nothing."""

    def body(*args, **kwargs):
        return _load(func)(*args, **kwargs)

    body.__name__ = func.name
    body.__signature__ = inspect.Signature(  # the DSL reads argument names from here
        [inspect.Parameter(p, inspect.Parameter.POSITIONAL_OR_KEYWORD) for p in func.params])
    d = func.declared
    effect = Effect(str(d.get("effect", func.effect)))
    determinism = d.get("determinism") or ("stochastic" if func.is_model else "deterministic")
    extra: dict[str, Any] = {"staleness_s": float(d.get("staleness_s", 300.0))} if effect is Effect.READ else {}
    if effect is Effect.WRITE:
        extra["writes"] = list(d.get("writes") or [f"state.{func.name}"])
    if d.get("cache_key_fields") is not None:
        extra["cache_key_fields"] = list(d["cache_key_fields"])
    return node(kind="tool", target=func.name, effect=effect, determinism=determinism, **extra)(body)


_loaded: dict[str, Any] = {}


def _with_spare_keys(completions, original, args, kwargs):
    """A demo must survive one key's rate limit. Only keys the developer put in GROQ_API_KEYS, only for Groq's URL."""
    import openai

    client = completions._client
    spare = [k.strip() for k in os.environ.get("GROQ_API_KEYS", "").split(",") if k.strip()]
    if "groq.com" not in str(client.base_url):
        spare = []
    if spare:
        client.max_retries = 0  # its retries sleep on a 429, and that sleep would be measured as the agent's time
    tried = 0
    while True:
        try:
            return original(completions, *args, **{**kwargs, "timeout": kwargs.get("timeout", 45)})
        except openai.RateLimitError:
            if tried >= len(spare):
                raise
            client.api_key = spare[tried]
            tried += 1


def _meter_openai() -> None:
    """Agent code calls its own client, so its tokens and price are taken from each real response. Rule 5: a model
    that models.yaml doesn't price has no cost, rather than a guessed one."""
    try:
        from openai.resources.chat.completions import Completions
    except ImportError:
        return
    if getattr(Completions.create, "_synapse_metered", False):
        return
    from synapse0g.graph.runtime import meter, providers

    original = Completions.create

    def create(self, *args, **kwargs):
        reply = _with_spare_keys(self, original, args, kwargs)
        usage = getattr(reply, "usage", None)
        tokens_in = getattr(usage, "prompt_tokens", None)
        tokens_out = getattr(usage, "completion_tokens", None)
        tag = kwargs.get("model") or getattr(reply, "model", "")
        config = next((m for m in providers.load_registry().models.values() if m.tag == tag), None)
        meter.record(SimpleNamespace(tokens_in=tokens_in, tokens_out=tokens_out, model_id=tag,
                                     provider=config.provider if config else "unknown",
                                     cost_usd=config.cost_usd(tokens_in, tokens_out) if config else None))
        return reply

    create._synapse_metered = True
    Completions.create = create


def _load(func: Func):
    _meter_openai()
    key = str(func.file)
    if key not in _loaded:
        spec = importlib.util.spec_from_file_location(f"synapse_mapped_{abs(hash(key))}", func.file)
        module = importlib.util.module_from_spec(spec)
        sys.path.insert(0, str(func.file.parent))
        spec.loader.exec_module(module)
        _loaded[key] = module
    return getattr(_loaded[key], func.name)


TS_READ = re.compile(r"\bfetch\w*\(|axios\.get|\.findOne\(|await ")
TS_MODEL = re.compile(r"chat\.completions\.create|messages\.create|generateContent")
TS_EXTERNAL = re.compile(r"method:\s*[\"'](POST|PUT|PATCH|DELETE)|\.(save|insertOne|updateOne|deleteOne|create)\(|sendMail")  # noqa: E501
TS_IRREVERSIBLE = re.compile(r"\b(charge|refund|placeOrder|deleteAccount|stripe\.)", re.I)


def _ts_body(text: str, start: int) -> str:
    """The function's own braces, so a helper or a table written after it is not read as part of it."""
    open_at = text.find("{", text.find(")", start))
    depth, at = 0, open_at
    while 0 <= at < len(text):
        depth += {"{": 1, "}": -1}.get(text[at], 0)
        at += 1
        if depth == 0:
            break
    return text[open_at:at]


def scan_typescript(root: Path) -> list[dict]:
    """Exported functions and what they touch. A dispatch table tells which of them an agent can call."""
    found: list[dict] = []
    if root.is_file():
        return found
    for path in sorted(list(root.rglob("*.ts")) + list(root.rglob("*.tsx"))):
        if SKIP_DIRS & set(path.parts) or path.name.endswith(".d.ts"):
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        spans = list(re.finditer(r"^export (?:async )?function (\w+)", text, re.M))
        for match in spans:
            body = _ts_body(text, match.end())
            if TS_MODEL.search(body):
                effect, why = "pure", "model call"
            elif TS_IRREVERSIBLE.search(match.group(1)):
                effect, why = "irreversible", "its name says money or destruction"
            elif TS_EXTERNAL.search(body):
                effect, why = "external", "writes outside the process"
            elif TS_READ.search(body):
                effect, why = "read", "reads over the network"
            else:
                effect, why = "pure", "no I/O found"
            found.append({"name": match.group(1), "file": str(path.relative_to(root)), "effect": effect, "why": why,
                          "model": bool(TS_MODEL.search(body)),
                          "line": text[:match.start()].count("\n") + 1})
    return found


def agent_tools(root: Path, functions: list[dict]) -> list[dict]:
    """TypeScript functions listed in a tool dispatch table, which are what an agent loop may call."""
    tools = []
    names = {f["name"]: f for f in functions}
    for path in list(root.rglob("*.ts")) if root.is_dir() else []:
        if not root.is_file() and SKIP_DIRS & set(path.relative_to(root).parts):  # a named file is always read
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for table in re.finditer(r"(?:const|let) \w*(?:IMPLEMENTATIONS|TOOLS|HANDLERS)\w*[^=]*=\s*\{(.*?)\n\};",
            text, re.S):
            for key, fn in re.findall(r"^\s*(\w+):\s*(\w+),?\s*$", table.group(1), re.M):
                if fn in names:
                    tools.append({"tool": key, **names[fn]})
    return tools


@dataclass
class CodeMap:
    root: Path
    functions: dict[str, Func]
    entry: Func | None = None
    graph: Graph | None = None
    refusal: str | None = None
    typescript: list[dict] = field(default_factory=list)
    tools: list[dict] = field(default_factory=list)


def scan(path: str | Path, entry_name: str | None = None) -> CodeMap:
    root = Path(path).resolve()
    if not root.exists():
        raise ValueError(f"no such path: {root}")
    funcs = scan_python(root)
    ts = scan_typescript(root)
    result = CodeMap(root=root, functions=funcs, typescript=ts, tools=agent_tools(root, ts))
    try:
        if entry_name and entry_name not in funcs:
            raise Unmappable(f"no function named {entry_name} here")
        result.entry = funcs[entry_name] if entry_name else pick_entry(funcs)
        result.graph = build_graph(result.entry, funcs)
    except Unmappable as exc:
        result.refusal = str(exc)
    return result


def report(found: CodeMap) -> str:
    lines = [f"Mapped {found.root}", f"  Python functions {len(found.functions)}, TypeScript functions "
             f"{len(found.typescript)}. Every label below is inferred."]
    if found.tools:
        lines += ["", f"Agent tools found in a dispatch table: {len(found.tools)}"]
        lines += [f"  {t['tool']:<24}{t['effect']:<13}{t['why']}  ({t['file']}:{t['line']})" for t in found.tools]
        free = [t["tool"] for t in found.tools if t["effect"] in {"pure", "read"}]
        lines.append(f"  {len(free)} of {len(found.tools)} change nothing, so nothing about them stops them overlapping. "  # noqa: E501
                     "A loop that picks one tool per turn never lets them.")
    models = [f for f in found.typescript if f["model"]]
    if models:
        lines += ["", "Model calls: " + ", ".join(f"{m['name']} ({m['file']}:{m['line']})" for m in models)]
    if found.entry:
        lines += ["", f"Entry function: {found.entry.name} ({found.entry.file.name}:{found.entry.line})"]
    if found.refusal:
        lines += ["", f"Not drawn as a runnable graph: {found.refusal}"]
    if found.graph:
        lines += ["", "Steps:"]
        for n in found.graph.nodes:
            func = found.functions.get(n.target)
            if func:
                why = func.evidence[0] if func.evidence else "calls nothing outside the process"
                lines.append(f"  {n.id:<5}{n.target:<26}{n.effect!s:<13}{why}")
    return "\n".join(lines)


def explain_table(found: CodeMap) -> list[dict]:
    """Each step and why it got its label, for the screen."""
    rows = []
    for n in found.graph.nodes if found.graph else []:
        func = found.functions.get(n.target)
        if func:
            rows.append({"node": n.id, "target": n.target, "effect": str(n.effect),
                         "why": func.evidence[0] if func.evidence else "calls nothing outside the process"})
    return rows
