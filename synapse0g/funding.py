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
class Chain:
    def __init__(self, rpc: str):
        self.rpc = rpc

    def _call(self, method: str, params: list):
        body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
        req = urllib.request.Request(self.rpc, body, {"Content-Type": "application/json", "User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=30) as r:
            doc = json.load(r)
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

    def _get(self, path: str) -> dict:
        req = urllib.request.Request(ROUTER + path, headers={"Authorization": f"Bearer {self.key}", "User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.load(r)

    def balance(self) -> dict:
        doc = self._get("/account/balance")
        return {"address": doc.get("address"), "spendable_0g": int(doc["total_balance"]) / NEURON,
                "deposited_0g": int(doc["deposit_balance"]) / NEURON}

    def usage(self, start_date: str) -> dict:
        return self._get(f"/account/usage/stats?start_date={start_date}")


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
