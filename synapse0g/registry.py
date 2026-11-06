"""The model registry: which models exist, where they live, and what a token costs.

Only `models.yaml` names a model. 0G prices come from 0G's live catalog unless the file sets one.
A model whose price is unknown has cost `None`, never an estimate.
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
USER_AGENT = "synapse0g/0.1"


def load_env(path: Path | None = None) -> None:
    """Read KEY=value lines from .env into os.environ, without overriding what is already set.

    Plain parsing on purpose: 0G's key is named `0G_API_KEY`, which a shell cannot export but a
    process environment can hold.
    """
    source = Path(path or os.environ.get("SYNAPSE0G_ENV", ROOT / ".env"))
    if not source.exists():
        return
    for line in source.read_text(encoding="utf-8").splitlines():
        m = re.match(r"\s*([A-Za-z0-9_\-]+)\s*=\s*(.*)$", line)
        if not m or line.lstrip().startswith("#"):
            continue
        value = m.group(2).strip().strip('"').strip("'")
        os.environ.setdefault(m.group(1), value)


@dataclass(frozen=True)
class Provider:
    name: str
    base_url: str
    key_env: tuple[str, ...]
    catalog_url: str | None = None
    # guarded: the agent's own account. Every call reserves its worst case against the account and the
    # job's cap before it is sent (synapse0g.spend), and nothing falls back to another provider's key.
    guarded: bool = False
    max_tokens: int | None = None  # the output cap a guarded call is held to when it names none

    def keys(self) -> list[str]:
        """Every key that is set, in the order models.yaml lists them. Calls rotate through them."""
        found = [os.environ[name] for name in self.key_env if os.environ.get(name)]
        if not found:
            raise KeyError(f"no API key for {self.name}: set one of {', '.join(self.key_env)} in .env")
        return found

    def key(self) -> str:
        return self.keys()[0]


@dataclass(frozen=True)
class Model:
    id: str
    provider: str
    tag: str
    price_in: float | None = None  # USD per token
    price_out: float | None = None
    price_source: str = "unknown"
    reasoning_off: dict = field(default_factory=dict)
    cheaper: tuple[str, ...] = ()
    supported: tuple[str, ...] = ()
    explicit_cache: bool = False  # accepts cache_control marks on messages (checked by a real call)
    cached_price_ratio: float | None = None  # billed price of a cached input token / normal price, measured
    min_cache_tokens: int = 1024
    neuron_in: int | None = None  # 0G's own price, in neuron (1e-18 0G) per token, from its catalog
    neuron_out: int | None = None

    def cost(self, tokens_in: int | None, tokens_out: int | None) -> float | None:
        if self.price_in is None or self.price_out is None or tokens_in is None or tokens_out is None:
            return None
        return tokens_in * self.price_in + tokens_out * self.price_out


@dataclass(frozen=True)
class Registry:
    providers: dict[str, Provider]
    models: dict[str, Model]
    catalog_fetched_at: str | None = None

    def model(self, model_id: str) -> Model:
        if model_id not in self.models:
            raise KeyError(f"unknown model id {model_id!r}; known: {', '.join(sorted(self.models))}")
        return self.models[model_id]

    def describe(self) -> dict:
        return {
            "catalog_fetched_at": self.catalog_fetched_at,
            "models": {
                m.id: {
                    "provider": m.provider,
                    "tag": m.tag,
                    "usd_per_mtok_in": None if m.price_in is None else round(m.price_in * 1e6, 6),
                    "usd_per_mtok_out": None if m.price_out is None else round(m.price_out * 1e6, 6),
                    "price_source": m.price_source,
                }
                for m in self.models.values()
            },
        }


CATALOG_CACHE = ROOT / "data" / "catalog_cache.json"
LAST_CATALOG: dict = {}  # url -> when the catalog in use was read, and whether it came from the cache


def fetch_catalog(url: str) -> dict[str, dict]:
    """0G's live model catalog. If it cannot be read now, the last one read is used, and says when it was
    read (`LAST_CATALOG`): prices from an older read of 0G's own catalog, never invented ones."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            doc = json.load(r)
    except (OSError, json.JSONDecodeError) as e:
        cache = json.loads(CATALOG_CACHE.read_text(encoding="utf-8")) if CATALOG_CACHE.exists() else {}
        if url not in cache:
            raise
        LAST_CATALOG[url] = {"read_at": cache[url]["read_at"], "from_cache": True, "why": f"{type(e).__name__}: {e}"[:200]}
        doc = cache[url]["doc"]
    else:
        LAST_CATALOG[url] = {"read_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "from_cache": False}
        try:
            cache = json.loads(CATALOG_CACHE.read_text(encoding="utf-8")) if CATALOG_CACHE.exists() else {}
            cache[url] = {"read_at": LAST_CATALOG[url]["read_at"], "doc": doc}
            CATALOG_CACHE.parent.mkdir(parents=True, exist_ok=True)
            CATALOG_CACHE.write_text(json.dumps(cache), encoding="utf-8")
        except OSError as e:  # the cache is a convenience: say so and go on
            print(f"registry: could not cache the catalog: {e}")
    return {m["id"]: m for m in doc.get("data", [])}


def load_registry(path: Path | None = None, *, catalog: dict[str, dict] | None = None) -> Registry:
    """Parse models.yaml. 0G prices are read from `catalog` (fetched live when not given)."""
    doc = yaml.safe_load(Path(path or ROOT / "models.yaml").read_text(encoding="utf-8")) or {}
    providers = {
        name: Provider(
            name=name,
            base_url=body["base_url"].rstrip("/"),
            key_env=tuple(body.get("key_env") or [f"{name.upper()}_API_KEY"]),
            catalog_url=body.get("catalog_url"),
            guarded=bool(body.get("guarded", False)),
            max_tokens=body.get("max_tokens"),
        )
        for name, body in (doc.get("providers") or {}).items()
    }
    catalogs: dict[str, dict[str, dict]] = {}
    fetched_at = None
    by_url: dict[str, dict[str, dict]] = {}
    for p in providers.values():
        if p.catalog_url:
            if p.catalog_url not in by_url:  # two providers on one Router share one read
                by_url[p.catalog_url] = catalog if catalog is not None else fetch_catalog(p.catalog_url)
            catalogs[p.name] = by_url[p.catalog_url]
            fetched_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    models: dict[str, Model] = {}
    for mid, body in (doc.get("models") or {}).items():
        provider = body["provider"]
        if provider not in providers:
            raise ValueError(f"models.yaml: {mid} uses unknown provider {provider!r}")
        price_in = price_out = None
        neuron_in = neuron_out = None
        source = "unknown"
        supported: tuple[str, ...] = ()
        listed = catalogs.get(provider, {}).get(body["tag"])
        if listed:
            supported = tuple(listed.get("supported_parameters") or ())
            usd = listed.get("pricing_usd") or {}
            if usd.get("prompt") is not None and usd.get("completion") is not None:
                price_in, price_out = float(usd["prompt"]), float(usd["completion"])
                source = f"{provider} catalog"
            neuron = listed.get("pricing") or {}
            try:
                neuron_in, neuron_out = int(neuron["prompt"]), int(neuron["completion"])
            except (KeyError, TypeError, ValueError):
                pass
        elif provider in catalogs:
            raise ValueError(f"models.yaml: {mid} names {body['tag']!r}, which {provider}'s catalog does not list")
        if body.get("price_per_mtok"):
            price_in = body["price_per_mtok"]["in"] / 1e6
            price_out = body["price_per_mtok"]["out"] / 1e6
            source = "models.yaml"
        models[mid] = Model(
            id=mid,
            provider=provider,
            tag=body["tag"],
            price_in=price_in,
            price_out=price_out,
            price_source=source,
            reasoning_off=dict(body.get("reasoning_off") or {}),
            cheaper=tuple(body.get("cheaper") or ()),
            supported=supported,
            explicit_cache=bool(body.get("explicit_cache", False)),
            cached_price_ratio=body.get("cached_price_ratio"),
            min_cache_tokens=int(body.get("min_cache_tokens", 1024)),
            neuron_in=neuron_in,
            neuron_out=neuron_out,
        )
    for m in models.values():
        for c in m.cheaper:
            if c not in models:
                raise ValueError(f"models.yaml: {m.id} lists cheaper model {c!r}, which is not defined")
            if models[c].provider != m.provider:
                raise ValueError(f"models.yaml: {m.id} -> {c} crosses providers; routing never does that")
    return Registry(providers, models, fetched_at)
