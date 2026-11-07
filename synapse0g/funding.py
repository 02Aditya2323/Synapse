"""Where the paid agent's money comes from, read from its sources. Nothing here signs or sends.

The loop, for an agent funded through comfy (docs/COMFY.md):

    someone trades the agent's token  ->  the trade pays fees in 0G     (on chain: ChargeTaken)
    the fees are split per the token   ->  creators' part, compute part (on chain: splitOf)
    the compute part becomes credit    ->  the Router account balance   (Router: /account/balance)
    the agent's model calls spend it   ->  billed calls, with request ids (our own records)

Each stage is read where it lives and kept separate: fees charged on chain are not credit
delivered, and credit delivered is not credit spendable. A stage that cannot be read yet (no
token, no management key) is reported as not known, never filled in.

Trades from `team_wallets` are shown, but never counted as earnings.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from synapse0g.registry import ROOT, USER_AGENT

CONFIG = ROOT / "comfy.yaml"
CACHE = ROOT / "data" / "funding_cache.json"
ROUTER = "https://router-api.0g.ai/v1"
NEURON = 10**18  # 1 0G

# event topics and function selectors: keccak of the signatures, checked against the documented
# topics and live calls on 6 Nov 2026
LAUNCHED = "0x20c8f644fcaa2f5b4960a99c98327b3d5bd9838194dae4a67dd914ec9c8249fe"
CHARGE_TAKEN = "0x787cb3fc0a69467064364b0ecaef40dee4a3f7e29279d74b70e8ded6e4758a1f"
SPLIT_OF = "0x175d69cf"  # splitOf(bytes32)
IS_COMFY_TOKEN = "0xa0fc893a"  # isComfyToken(address)
NAME, SYMBOL = "0x06fdde03", "0x95d89b41"
CHUNK = 100_000  # blocks per log query, as the integration docs ask


def load_config(path: Path = CONFIG) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


# ---- decoding ----------------------------------------------------------------------------------
def _words(data: str) -> list[str]:
    h = data[2:] if data.startswith("0x") else data
    return [h[i:i + 64] for i in range(0, len(h), 64)]


def _addr(word: str) -> str:
    return "0x" + word[-40:]


def decode_launched(log: dict) -> dict:
    w = _words(log["data"])
    return {"pool_id": log["topics"][1], "token": _addr(log["topics"][2]), "creator": _addr(log["topics"][3]),
            "ladder_supply": int(w[1], 16) / NEURON, "block": int(log["blockNumber"], 16), "tx": log["transactionHash"]}


def decode_charge(log: dict) -> dict:
    """ChargeTaken(bytes32 indexed poolId, address indexed router, bool isBuy, uint256 quoteAmount,
    uint256 baseFee, uint256 openingFee, uint256 creatorTax, address currency). Amounts in 0G."""
    w = _words(log["data"])
    base, opening, tax = (int(x, 16) / NEURON for x in w[2:5])
    return {"tx": log["transactionHash"], "block": int(log["blockNumber"], 16), "router": _addr(log["topics"][2]),
            "is_buy": bool(int(w[0], 16)), "quote_0g": int(w[1], 16) / NEURON, "base_fee_0g": base,
            "opening_fee_0g": opening, "creator_tax_0g": tax, "native": int(w[5], 16) == 0}


def decode_split(data: str) -> dict:
    """splitOf(poolId): (set, protocol bps, creator bps, compute bps of the creators' part, destination)."""
    w = _words(data)
    return {"set": bool(int(w[0], 16)), "protocol_bps": int(w[1], 16), "creator_bps": int(w[2], 16),
            "compute_bps": int(w[3], 16), "compute_destination": "0x" + w[4]}


def decode_string(data: str) -> str:
    w = _words(data)
    n = int(w[1], 16)
    return bytes.fromhex("".join(w[2:]))[:n].decode("utf-8", "replace")


def shares(charge: dict, split: dict) -> dict:
    """What one charge sends to the creators and to compute, per the token's on-chain split. The opening
    fee is split like the base fee; the creator tax goes to the creators whole (comfy.fun/docs/trading/fees)."""
    creators = (charge["base_fee_0g"] + charge["opening_fee_0g"]) * split["creator_bps"] / 10_000 + charge["creator_tax_0g"]
    compute = creators * split["compute_bps"] / 10_000
    return {"creators_0g": creators, "compute_0g": compute, "claimable_0g": creators - compute}


# ---- sources -----------------------------------------------------------------------------------
FALLBACK_RPCS = ["https://0g-mainnet.drpc.org"]  # 0G's docs advise a third-party RPC beside its rate-limited public one


class Chain:
    def __init__(self, rpc: str, fallbacks: list[str] | None = None):
        self.rpc = rpc
        self.rpcs = [rpc] + [u for u in (FALLBACK_RPCS if fallbacks is None else fallbacks) if u != rpc]

    def _call(self, method: str, params: list, attempts: int = 4):
        body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
        for i in range(attempts):  # an RPC drops connections now and then: a read is safe to repeat, on the next RPC
            url = self.rpcs[i % len(self.rpcs)]
            req = urllib.request.Request(url, body, {"Content-Type": "application/json", "User-Agent": USER_AGENT})
            try:
                with urllib.request.urlopen(req, timeout=30) as r:
                    doc = json.load(r)
                if "error" in doc and i < attempts - 1 and "rate" in str(doc["error"]).lower():
                    continue
                break
            except (OSError, json.JSONDecodeError):
                if i == attempts - 1:
                    raise
                time.sleep(1.0 * (i + 1))
        if "error" in doc:
            raise RuntimeError(f"{method}: {doc['error']}")
        return doc["result"]

    def head(self) -> int:
        return int(self._call("eth_blockNumber", []), 16)

    def eth_call(self, to: str, data: str) -> str:
        return self._call("eth_call", [{"to": to, "data": data}, "latest"])

    def logs(self, topics: list, start: int, end: int, address: str | None = None) -> list[dict]:
        found, b = [], start
        while b <= end:
            q = {"topics": topics, "fromBlock": hex(b), "toBlock": hex(min(b + CHUNK - 1, end))}
            if address:
                q["address"] = address
            found += self._call("eth_getLogs", [q])
            b += CHUNK
        return found

    def sender(self, tx: str) -> str:
        return self._call("eth_getTransactionByHash", [tx])["from"]


class RouterAccount:
    """The 0G Router account the agent's key spends from. Reading it needs a management key (mk-...)
    with account:read; inference keys (sk-...) are refused."""

    def __init__(self, key: str):
        self.key = key

    def _get(self, path: str, attempts: int = 3) -> dict:
        req = urllib.request.Request(ROUTER + path, headers={"Authorization": f"Bearer {self.key}", "User-Agent": USER_AGENT})
        for i in range(attempts):  # a dropped connection or a DNS hiccup is retried; a refusal (4xx) is not
            try:
                with urllib.request.urlopen(req, timeout=30) as r:
                    return json.load(r)
            except urllib.error.HTTPError:
                raise
            except (OSError, json.JSONDecodeError):
                if i == attempts - 1:
                    raise
                time.sleep(1.0 * (i + 1))
        raise RuntimeError("unreachable")

    def balance(self) -> dict:
        doc = self._get("/account/balance")
        return {"address": doc.get("address"), "spendable_0g": int(doc["total_balance"]) / NEURON,
                "deposited_0g": int(doc["deposit_balance"]) / NEURON}

    def usage(self, start_date: str) -> dict:
        return self._get(f"/account/usage/stats?start_date={start_date}")

    def account(self) -> dict:
        """The account itself: its address and the currency it bills in (account:read)."""
        return self._get("/account")

    def funds(self) -> dict:
        """What a request can be admitted against, in neuron (0G-billed accounts report wei strings).

        0G admits a request when deposit + credit - pending charge, plus a share (vault_ratio) of the
        Payment Layer vault, covers its minimum cost; /account/balance leaves the vault out, so it is
        read here (docs: 0gfoundation.github.io/0g-router, "Admission")."""
        doc = self._get("/account/funds")
        router, vault = doc.get("router") or {}, doc.get("payment_layer") or {}
        n = _neuron
        return {"currency": doc.get("currency"), "deposit": n(router.get("deposit")), "credit": n(router.get("credit")),
                "pending_charge": n(router.get("pending_charge")), "subtotal": n(router.get("subtotal")),
                "payment_layer": n(vault.get("balance")), "payment_layer_read_at": vault.get("queried_at"),
                "total": n(doc.get("total")), "raw": doc}

    def api_keys(self) -> list[dict]:
        """The account's inference keys and their caps, as 0G echoes them (keys:read). Never the key."""
        doc = self._get("/api-keys?status=all")
        return doc.get("data", doc.get("keys", [])) if isinstance(doc, dict) else doc

    def usage_history(self, limit: int = 100) -> list[dict]:
        doc = self._get(f"/account/usage/history?limit={limit}")
        return doc.get("data", doc.get("records", [])) if isinstance(doc, dict) else doc


def _neuron(value) -> int:
    """An amount from the Router in neuron. 0G-billed accounts send wei as an integer string."""
    if value in (None, ""):
        return 0
    return int(str(value).split(".")[0]) if "." not in str(value) else int(float(value) * NEURON)


def usd_per_0g(catalog_model: dict) -> float | None:
    """The rate implied by the 0G catalog, which prices each model in both neuron and USD."""
    try:
        neuron = int(catalog_model["pricing"]["prompt"])
        usd = float(catalog_model["pricing_usd"]["prompt"])
    except (KeyError, TypeError, ValueError):
        return None
    return usd / (neuron / NEURON) if neuron else None


# ---- the snapshot the dashboard reads ----------------------------------------------------------
@dataclass
class Stage:
    key: str
    title: str
    state: str  # done | waiting | unknown
    detail: str
    evidence: dict = field(default_factory=dict)


def _cache() -> dict:
    return json.loads(CACHE.read_text(encoding="utf-8")) if CACHE.exists() else {}


def _save(doc: dict) -> None:
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps(doc, indent=1), encoding="utf-8")


def find_pool(chain: Chain, cfg: dict, token: str) -> dict | None:
    """The token's Launched event: its pool id, creator and launch tx."""
    c = cfg["contracts"]
    topic = "0x" + "0" * 24 + token.lower()[2:]
    logs = chain.logs([LAUNCHED, None, topic], c["first_block"], chain.head(), address=c["launcher"])
    return decode_launched(logs[0]) if logs else None


def charges(chain: Chain, cfg: dict, pool_id: str, start: int) -> list[dict]:
    """Every charge on the pool since `start`, scanned once and kept: a later read scans only new blocks."""
    cache = _cache()
    seen = cache.get("charges", {}).get(pool_id, {"to_block": start - 1, "rows": []})
    head = chain.head()
    if head > seen["to_block"]:
        new = [decode_charge(log) for log in chain.logs([CHARGE_TAKEN, pool_id], seen["to_block"] + 1, head, address=cfg["contracts"]["hook"])]
        for row in new:
            row["trader"] = chain.sender(row["tx"])
        seen = {"to_block": head, "rows": seen["rows"] + new}
        cache.setdefault("charges", {})[pool_id] = seen
        _save(cache)
    return seen["rows"]


def snapshot(cfg: dict | None = None, *, chain: Chain | None = None, account: RouterAccount | None = None,
             spent: dict | None = None) -> dict:
    """Every stage of the loop, each read from its own source. `spent` is the agent's own measured
    inference (calls, billed cost, request ids) from service.inference_receipts()."""
    cfg = cfg or load_config()
    chain = chain or Chain(cfg["network"]["rpc"])
    if account is None and os.environ.get("ZG_MANAGEMENT_KEY"):
        account = RouterAccount(os.environ["ZG_MANAGEMENT_KEY"])
    explorer = cfg["network"]["explorer"]
    team = {w.lower() for w in cfg.get("team_wallets") or []}
    stages: list[Stage] = []
    errors: list[str] = []

    stages.append(Stage("identity", "Agent registered on 0G", "done" if cfg.get("agent_id") else "waiting",
                        f"agent {cfg['agent_id']}, owner {cfg.get('owner')}" if cfg.get("agent_id")
                        else "not registered yet: `comfy-cli agent register --owner <wallet>`, then claim within 15 minutes",
                        {"agent_id": cfg.get("agent_id"), "owner": cfg.get("owner")}))

    token, launch, split, rows = cfg.get("token"), None, None, []
    if token:
        try:
            ok = int(chain.eth_call(cfg["contracts"]["launcher"], IS_COMFY_TOKEN + "0" * 24 + token.lower()[2:]), 16) == 1
            name, symbol = (decode_string(chain.eth_call(token, s)) for s in (NAME, SYMBOL))
            launch = {"pool_id": cfg["pool_id"]} if cfg.get("pool_id") else find_pool(chain, cfg, token)
            if launch:
                split = decode_split(chain.eth_call(cfg["contracts"]["splitter"], SPLIT_OF + launch["pool_id"][2:]))
                rows = charges(chain, cfg, launch["pool_id"], launch.get("block") or cfg["contracts"]["first_block"])
            stages.append(Stage("token", "Token launched on comfy", "done" if ok and launch else "waiting",
                                f"{name} ({symbol}){'' if ok else ': the launcher does not know this token'}",
                                {"token": token, "name": name, "symbol": symbol, "is_comfy_token": ok, "launch": launch,
                                 "split": split, "url": cfg.get("token_page"), "explorer": f"{explorer}/address/{token}"}))
        except Exception as e:  # shown as an error on the stage, never as an empty success
            errors.append(f"chain: {type(e).__name__}: {e}")
            stages.append(Stage("token", "Token launched on comfy", "unknown", f"could not read the chain: {e}"))
    else:
        stages.append(Stage("token", "Token launched on comfy", "waiting",
                            "not launched yet: launch from the agent's page on comfy.fun with the compute share on"))

    outside = [r for r in rows if r.get("trader", "").lower() not in team]
    totals = {"trades": len(rows), "outside_trades": len(outside), "team_trades": len(rows) - len(outside)}
    if split:
        for k in ("creators_0g", "compute_0g", "claimable_0g"):
            totals[k] = round(sum(shares(r, split)[k] for r in outside), 9)
    totals["volume_0g"] = round(sum(r["quote_0g"] for r in outside), 9)
    recent = [dict(r, team=r.get("trader", "").lower() in team, explorer=f"{explorer}/tx/{r['tx']}",
                   **(shares(r, split) if split else {})) for r in rows[-20:]]
    stages.append(Stage("fees", "Trades paid fees", "done" if outside else "waiting",
                        (f"{len(outside)} trades by others" + (f", {totals['team_trades']} by the team (not counted)" if totals["team_trades"] else "")
                         + (f": {totals.get('compute_0g', 0):.6f} 0G to compute per the on-chain split" if split else ""))
                        if rows else "no trades yet", {"totals": totals, "recent": list(reversed(recent))}))

    balance = None
    if account:
        try:
            balance = account.balance()
        except Exception as e:
            errors.append(f"router: {type(e).__name__}: {e}")
    history = _cache().get("balances", [])
    if balance:
        history = (history + [{"at": time.time(), **balance}])[-500:]
        cache = _cache()
        cache["balances"] = history
        _save(cache)
    rises = [{"at": b["at"], "by_0g": round(b["deposited_0g"] - a["deposited_0g"], 9)}
             for a, b in zip(history, history[1:], strict=False) if b["deposited_0g"] > a["deposited_0g"]]
    stages.append(Stage("credit", "Compute credit arrived", "done" if rises else ("waiting" if balance else "unknown"),
                        (f"credit rose {len(rises)} time(s), {sum(r['by_0g'] for r in rises):.6f} 0G in all, while we watched"
                         if rises else "no rise in the account's deposits seen yet") if balance
                        else "the Router account cannot be read: set ZG_MANAGEMENT_KEY (an mk- key with account:read)",
                        {"rises": rises[-20:]}))
    stages.append(Stage("balance", "Credit the agent can spend", "done" if balance else "unknown",
                        f"{balance['spendable_0g']:.6f} 0G spendable now" if balance else "not known",
                        {"balance": balance}))

    spent = spent or {}
    calls = f"{spent['calls']} billed calls, ${spent['billed_usd']:.4f} in all" if spent.get("calls") else "no billed calls yet"
    # calls count as paid by earnings only once earned credit has arrived; before that a person paid for them
    stages.append(Stage("inference", "Inference paid from it", "done" if spent.get("calls") and rises else "waiting",
                        calls if rises else f"{calls}, all paid from the team's own deposit: none from earned credit yet",
                        dict(spent, paid_from="earned credit and deposits" if rises else "team deposit")))
    return {"read_at": time.time(), "network": cfg["network"], "stages": [s.__dict__ for s in stages], "errors": errors}


# ---- the read-only check before the agent spends anything ---------------------------------------
AGENT_MODELS = ("qwen3.8-flash", "qwen3.7-flash")


def account_check(owner: str | None = None, key_name: str | None = None) -> dict:
    """Everything the agent's account must be before it spends, read from 0G, with what blocks it.

    Needs ZG_MANAGEMENT_KEY (account:read, keys:read). Reads only; prints no key. Field names of each
    answer are kept (`shapes`) so units can be checked against the Router reference."""
    from synapse0g import spend

    key_name = key_name or os.environ.get("ZG_AGENT_KEY_NAME", "synapse-agent")
    out: dict = {"env": {k: bool(os.environ.get(k)) for k in ("ZG_MANAGEMENT_KEY", "ZG_AGENT_KEY")}, "blockers": [], "shapes": {}}
    if not os.environ.get("ZG_MANAGEMENT_KEY"):
        out["blockers"].append("ZG_MANAGEMENT_KEY is not set (a read-only mk- key with account:read and keys:read)")
        return out
    acct = RouterAccount(os.environ["ZG_MANAGEMENT_KEY"])

    def read(name, fn):
        try:
            value = fn()
            out["shapes"][name] = sorted(value) if isinstance(value, dict) else (sorted(value[0]) if value else [])
            return value
        except Exception as e:  # a read that fails is reported, never skipped
            out["blockers"].append(f"{name}: {type(e).__name__}: {str(e)[:200]}")
            return None

    a = read("account", acct.account)
    if a is not None:
        out["account"] = {k: a.get(k) for k in ("address", "wallet_address", "billing_currency", "currency", "settlement_currency")
                          if a.get(k) is not None}
        currency = str(out["account"].get("billing_currency") or out["account"].get("currency")
                       or out["account"].get("settlement_currency") or "").lower()
        if currency and currency != "0g":
            out["blockers"].append(f"the account bills in {currency}: comfy credit is spendable only by a 0G-billed account")
        address = str(out["account"].get("address") or out["account"].get("wallet_address") or "").lower()
        if owner and address and address != owner.lower():
            out["blockers"].append(f"the account is {address}, not the owner wallet {owner.lower()}")
    f = read("funds", acct.funds)
    if f is not None:
        out["funds_0g"] = {k: f[k] / NEURON for k in ("deposit", "credit", "pending_charge", "payment_layer", "total")}
        out["funds_0g"]["admissible"] = spend.admission(f) / NEURON
        out["funds_currency"] = f.get("currency")
        out["shapes"]["funds.router"] = sorted(f["raw"].get("router") or {})
        out["shapes"]["funds.payment_layer"] = sorted(f["raw"].get("payment_layer") or {})
        if out["funds_0g"]["admissible"] < spend.MIN_COST / NEURON:
            out["blockers"].append(f"admissible funds {out['funds_0g']['admissible']:.6f} 0G are under 0G's 0.01 minimum per request")
    keys = read("api_keys", acct.api_keys)
    if keys is not None:
        mine = [k for k in keys if k.get("name") == key_name]
        out["key"] = [{f: k.get(f) for f in ("name", "status", "credit_limit", "reset_period", "expires_at", "allowed_models",
                                              "allowed_providers", "used", "currency")} for k in mine]
        active = [k for k in mine if k.get("status") in (None, "active")]
        if len(active) != 1:
            out["blockers"].append(f"expected one active key named {key_name!r}, found {len(active)}")
        else:
            k = active[0]
            if k.get("credit_limit") in (None, ""):
                out["blockers"].append("the agent key has no credit limit")
            extra = set(k.get("allowed_models") or []) - set(AGENT_MODELS)
            if not k.get("allowed_models") or extra:
                out["blockers"].append(f"the agent key should allow only {', '.join(AGENT_MODELS)}; it allows {k.get('allowed_models') or 'every model'}")
    try:  # needed only to settle uncertain charges later: a failed read is a warning, not a blocker
        out["usage_records"] = len(acct.usage_history(5))
    except Exception as e:
        out.setdefault("warnings", []).append(f"usage_history: {type(e).__name__}: {str(e)[:200]}")
    try:
        catalog = {m["id"]: m for m in json.load(urllib.request.urlopen(urllib.request.Request(
            ROUTER + "/models", headers={"User-Agent": USER_AGENT}), timeout=30))["data"]}
        out["models"] = {m: m in catalog for m in AGENT_MODELS}
        for m in AGENT_MODELS:
            if m not in catalog:
                out["blockers"].append(f"{m} is not in 0G's catalog now")
    except Exception as e:
        out["blockers"].append(f"catalog: {type(e).__name__}: {e}")
    out["ready"] = not out["blockers"]
    return out
