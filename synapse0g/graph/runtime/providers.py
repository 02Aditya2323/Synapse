"""One OpenAI-style client for Groq, OpenRouter and Ollama.

Provider configuration for Synapse workflows. The `models.yaml` is a nested document
(`providers:` / `models:` / `defaults:` / `limits:`), with prices in a
first-class `price_per_mtok`, and the key ring reads its env var name from the file
instead of a hard-coded table.

**Only `models.yaml` names a model** (docs/CORRECTNESS.md). Code passes registry ids such
as `agent-small`; this module turns them into provider + tag. Nothing else in Synapse
may contain a provider tag or a URL.

**Measured, never estimated** (rule 5). `tokens_in` / `tokens_out` come from the
provider's `usage` block and are `None` when it did not report them. `cost_usd` is
`None` unless `models.yaml` gives a price for that model -- there is no average, no
fallback figure and no guess.

    synapse models        # verify every models.yaml tag resolves at its provider
"""

from __future__ import annotations

import itertools
import json
import os
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

try:  # .env is optional; real env vars always win
    from dotenv import load_dotenv

    load_dotenv(override=False)
except ImportError:  # pragma: no cover
    pass

# The graph engine's registry ships with the package; SYNAPSE_MODELS points elsewhere.
REGISTRY_PATH = Path(os.environ.get("SYNAPSE_MODELS") or Path(__file__).resolve().parent.parent / "data" / "models.yaml")


from synapse0g.graph.runtime.rate_gate import gate_for  # noqa: E402


class ProviderError(RuntimeError):
    """A provider could not answer. Carries enough detail to record on a crashed run --
    never swallowed, never turned into a benchmark number (docs/CORRECTNESS.md)."""


@dataclass(frozen=True)
class ProviderSpec:
    name: str
    base_url: str
    key_env: str
    key_optional: bool = False


@dataclass(frozen=True)
class ModelConfig:
    id: str
    provider: str
    tag: str
    params: dict = field(default_factory=dict)
    price_per_mtok: dict | None = None  # {"in": usd, "out": usd}; absent = cost unknown
    fallback: str | None = None
    batch: bool = False  # the `batch` pass may fuse calls to this target

    @property
    def determinism(self) -> str:
        """What the IR should call a call to this model.

        A seed plus temperature 0 makes the call reproducible, which is what makes it
        CSE-able and memoizable. Without that it is `stochastic` and no pass may
        collapse two of its samples (docs/IR_SPEC.md section 4.2).
        """
        temperature = self.params.get("temperature")
        if "seed" in self.params and (temperature in (0, 0.0) or temperature is None):
            return "seeded"
        if temperature in (0, 0.0):
            return "deterministic"
        return "stochastic"

    def cost_usd(self, tokens_in: int | None, tokens_out: int | None) -> float | None:
        """Measured tokens x the price in models.yaml. `None` when either the price or
        the token count is unknown, because the alternative is a made-up number on a
        slide."""
        if not self.price_per_mtok or tokens_in is None or tokens_out is None:
            return None
        return (tokens_in * self.price_per_mtok["in"] + tokens_out * self.price_per_mtok["out"]) / 1e6


@dataclass
class LLMResult:
    text: str
    tool_calls: list[dict]  # [{"id", "name", "arguments": dict}]
    finish_reason: str | None
    tokens_in: int | None
    tokens_out: int | None
    provider: str
    model_tag: str
    model_id: str  # the registry id that actually answered -- may be a fallback
    cost_usd: float | None = None

    def as_output(self) -> dict:
        return {"text": self.text, "tool_calls": self.tool_calls, "finish_reason": self.finish_reason}


@dataclass(frozen=True)
class Registry:
    providers: dict[str, ProviderSpec]
    models: dict[str, ModelConfig]
    defaults: dict[str, str]
    limits: dict[str, Any]

    @property
    def max_concurrency(self) -> int:
        return int(self.limits.get("max_concurrency", 8))

    def provider_limit(self, provider: str) -> int:
        """Per-provider concurrency cap. The scheduler enforces it; the free tiers in
        this build are a real constraint, not a theoretical one."""
        return int((self.limits.get("per_provider") or {}).get(provider, self.max_concurrency))


_registry: Registry | None = None


def load_registry(path: Path | None = None) -> Registry:
    """Parse models.yaml. Cached when read from the default path."""
    global _registry
    if path is None and _registry is not None:
        return _registry
    source = Path(path or REGISTRY_PATH)
    if path is None and not source.exists():  # an installed copy, run from any folder
        source = Path(__file__).resolve().parent.parent / "data" / "models.yaml"
    if not source.exists():
        raise FileNotFoundError(f"no model registry at {source}. Copy models.yaml from the repo root.")
    doc = yaml.safe_load(source.read_text(encoding="utf-8")) or {}

    providers: dict[str, ProviderSpec] = {}
    for name, body in (doc.get("providers") or {}).items():
        providers[name] = ProviderSpec(
            name=name,
            base_url=os.environ.get(f"{name.upper()}_BASE_URL", body["base_url"]),
            key_env=body.get("api_key_env", f"{name.upper()}_API_KEY"),
            key_optional=(name == "ollama"),  # Ollama ignores the key; keep the client uniform
        )

    models: dict[str, ModelConfig] = {}
    for mid, body in (doc.get("models") or {}).items():
        provider = body["provider"]
        if provider not in providers and provider != "fake":
            raise ValueError(
                f"models.yaml: {mid} uses provider {provider!r}, which is not in the providers: block"
            )
        models[mid] = ModelConfig(
            id=mid,
            provider=provider,
            tag=body["tag"],
            params=body.get("params") or {},
            price_per_mtok=body.get("price_per_mtok"),
            fallback=body.get("fallback"),
            batch=bool(body.get("batch", False)),
        )
    for mid, cfg in models.items():
        if cfg.fallback and cfg.fallback not in models:
            raise ValueError(f"models.yaml: {mid} falls back to {cfg.fallback!r}, which is not a registry id")

    registry = Registry(providers, models, doc.get("defaults") or {}, doc.get("limits") or {})
    if path is None:
        _registry = registry
    return registry


def reset_registry() -> None:
    """Drop the cache. Tests that point SYNAPSE_MODELS elsewhere call this."""
    global _registry
    _registry = None
    _rings.clear()
    _clients.clear()
    _exhausted.clear()


def model_config(model_id: str) -> ModelConfig:
    reg = load_registry()
    if model_id not in reg.models:
        raise KeyError(f"no model id {model_id!r} in models.yaml. Known: {', '.join(sorted(reg.models))}")
    return reg.models[model_id]


def price_of(model_id: str) -> dict | None:
    try:
        return model_config(model_id).price_per_mtok
    except KeyError:
        return None


# ---- fakes (tests and offline demos only) -----------------------------------
FakeFn = Callable[[list[dict], list[dict] | None, dict], LLMResult]
_fakes: dict[str, FakeFn] = {}


def register_fake(model_id: str, fn: FakeFn) -> None:
    """Route a registry id to a Python function.

    For tests and for the hermetic benchmark path. Never used for a reported number:
    a fake model's latency is not a measurement of anything.
    """
    _fakes[model_id] = fn


def clear_fakes() -> None:
    _fakes.clear()


def is_faked(model_id: str) -> bool:
    return model_id in _fakes


# ---- clients ----------------------------------------------------------------
class _KeyRing:
    """Several comma-separated keys per provider, rotated on 429. This provider setup
    exhausted a free tier mid-session; rotation plus `fallback:` is what kept it alive."""

    def __init__(self, keys: list[str]):
        self.keys = keys
        self._cycle = itertools.cycle(range(len(keys)))
        self._lock = threading.Lock()
        self.current = next(self._cycle)

    def rotate(self) -> str:
        with self._lock:
            self.current = next(self._cycle)
            return self.keys[self.current]

    def key(self) -> str:
        return self.keys[self.current]


_rings: dict[str, _KeyRing] = {}
_exhausted: dict[str, set[int]] = {}  # provider -> key indexes out of daily quota in this process
_clients: dict[tuple[str, str], Any] = {}


def _keys_for(provider: str) -> _KeyRing:
    if provider not in _rings:
        spec = load_registry().providers[provider]
        raw = os.environ.get(spec.key_env, "")
        keys = [k.strip() for k in raw.split(",") if k.strip()]
        if not keys:
            if not spec.key_optional:
                raise ProviderError(f"{spec.key_env} is not set (see .env.example)")
            keys = ["local"]
        _rings[provider] = _KeyRing(keys)
    return _rings[provider]


def _client(provider: str, key: str):
    from openai import OpenAI

    spec = load_registry().providers[provider]
    cache_key = (provider, key)
    if cache_key not in _clients:
        _clients[cache_key] = OpenAI(
            base_url=spec.base_url,
            api_key=key,
            max_retries=0,  # we retry ourselves so we can rotate keys
            timeout=float(os.environ.get("SYNAPSE_LLM_TIMEOUT", "90")),
        )
    return _clients[cache_key]


def _parse_tool_calls(message) -> list[dict]:
    calls = []
    for c in getattr(message, "tool_calls", None) or []:
        raw = c.function.arguments or "{}"
        try:
            args = json.loads(raw)
        except json.JSONDecodeError:
            args = {"__unparsed__": raw}  # kept verbatim: a malformed call is evidence, not noise
        calls.append({"id": c.id, "name": c.function.name, "arguments": args})
    return calls


def chat(model_id: str, messages: list[dict], tools: list[dict] | None = None, **params) -> LLMResult:
    """Call a registry model.

    If it is out of quota or unreachable and models.yaml names a `fallback`, the
    fallback answers; `LLMResult.provider` / `model_tag` / `model_id` always say which
    model really answered, so a profile never attributes a fallback's latency to the
    model that was asked for.
    """
    from synapse0g.graph.runtime import meter

    # Recorded where the call is made, not here at the top: a fallback re-enters `chat`, and
    # counting at every level would charge one answer twice.
    if model_id in _fakes:
        result = _fakes[model_id](messages, tools, params)
        meter.record(result)
        return result
    cfg = model_config(model_id)
    try:
        result = _chat_one(cfg, messages, tools, params)
        meter.record(result)
        return result
    except ProviderError as exc:
        if not cfg.fallback or cfg.fallback == model_id:
            raise
        print(f"[providers] {model_id} unavailable ({exc}); using fallback {cfg.fallback}", file=sys.stderr)
        return chat(cfg.fallback, messages, tools, **params)


_waits = {"retries": 0, "slept_s": 0.0}
_waits_lock = threading.Lock()


def _back_off(seconds: float) -> None:
    with _waits_lock:
        _waits["retries"] += 1
        _waits["slept_s"] += seconds
    time.sleep(seconds)


def waits() -> dict[str, float]:
    """Retries and back-off sleep so far in this process. A sleep lands inside a run's
    wall-clock time, so a benchmark has to be able to see it: on a rate-limited key, the
    graph that makes more calls waits more, and that is not the optimization's doing."""
    with _waits_lock:
        return dict(_waits)


def _chat_one(cfg: ModelConfig, messages: list[dict], tools: list[dict] | None, params: dict) -> LLMResult:
    merged = {**cfg.params, **params}
    merged.pop("price_per_mtok", None)
    ring = _keys_for(cfg.provider)
    deadline = time.monotonic() + float(os.environ.get("SYNAPSE_LLM_DEADLINE", "45"))
    tried_keys: set[int] = set()
    last_exc: Exception | None = None
    attempt = 0
    while True:
        usable = [i for i in range(len(ring.keys)) if i not in _exhausted.get(cfg.provider, set())]
        if not usable:
            raise ProviderError(f"{cfg.id}: every {cfg.provider} key is out of its daily quota") from last_exc
        if ring.current not in usable:
            ring.current = usable[0]
        if time.monotonic() > deadline:
            raise ProviderError(
                f"{cfg.id}: no answer within the deadline ({len(tried_keys)} keys tried)"
            ) from last_exc
        attempt += 1
        # Spread calls across keys from the start. Rotating only after a 429 piles a burst onto one
        # key, and the back-off then lands inside the measured time.
        if len(usable) > 1:
            ring.rotate()
            if ring.current not in usable:
                ring.current = usable[0]
        client = _client(cfg.provider, ring.key())
        try:
            kwargs: dict[str, Any] = {"model": cfg.tag, "messages": messages, **merged}
            if tools:
                kwargs["tools"] = tools
            gate = gate_for(cfg.provider)
            if gate is not None:
                gate.before_call()
            resp = client.chat.completions.create(**kwargs)
            if gate is not None:
                gate.after_call(getattr(getattr(resp, "usage", None), "total_tokens", None))
        except Exception as exc:  # noqa: BLE001 - classified below and re-raised if not transient
            status = getattr(exc, "status_code", None)
            rate_limited = status == 429 or type(exc).__name__ == "RateLimitError"
            transient = status in (500, 502, 503, 504) or type(exc).__name__ in (
                "APITimeoutError",
                "APIConnectionError",
            )
            if not (rate_limited or transient):
                raise ProviderError(f"{cfg.id}: {type(exc).__name__}: {exc}") from exc
            last_exc = exc
            if rate_limited:
                if "per-day" in str(exc) or "per day" in str(exc).lower():
                    _exhausted.setdefault(cfg.provider, set()).add(ring.current)
                tried_keys.add(ring.current)
                ring.rotate()
                _back_off(1 if len(tried_keys) < len(usable) else min(2 ** min(attempt, 4), 15))
            else:
                _back_off(min(2 ** min(attempt, 4), 15))
            continue
        choice = resp.choices[0]
        usage = getattr(resp, "usage", None)
        tokens_in = getattr(usage, "prompt_tokens", None) if usage else None
        tokens_out = getattr(usage, "completion_tokens", None) if usage else None
        return LLMResult(
            text=choice.message.content or "",
            tool_calls=_parse_tool_calls(choice.message),
            finish_reason=choice.finish_reason,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            provider=cfg.provider,
            model_tag=cfg.tag,
            model_id=cfg.id,
            cost_usd=cfg.cost_usd(tokens_in, tokens_out),
        )


def verify() -> list[tuple[str, str, str]]:
    """Check every models.yaml tag exists at its provider. Returns (id, status, detail).

    `synapse models` runs this. A MISSING row at H0 is five minutes of `ollama pull`;
    the same row discovered at H18 is the demo.
    """
    report: list[tuple[str, str, str]] = []
    available: dict[str, set[str] | Exception] = {}
    for cfg in load_registry().models.values():
        if cfg.provider == "fake" or is_faked(cfg.id):
            report.append((cfg.id, "FAKE", "routed to a local function; never used for a reported number"))
            continue
        if cfg.provider not in available:
            try:
                client = _client(cfg.provider, _keys_for(cfg.provider).key())
                available[cfg.provider] = {m.id for m in client.models.list().data}
            except Exception as exc:  # noqa: BLE001 - reported as a row, not raised
                available[cfg.provider] = exc
        got = available[cfg.provider]
        if isinstance(got, Exception):
            report.append((cfg.id, "ERROR", f"{cfg.provider}: {type(got).__name__}: {got}"))
        elif cfg.tag in got or f"{cfg.tag}:latest" in got:
            price = "" if cfg.price_per_mtok else "  (no price: cost reported as None)"
            report.append((cfg.id, "OK", f"{cfg.provider}/{cfg.tag}{price}"))
        else:
            report.append((cfg.id, "MISSING", f"{cfg.provider} has no {cfg.tag!r}"))
    return report
