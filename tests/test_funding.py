"""The funding loop, read from recorded 0G mainnet data: the COMFY token's launch, its fee split and a
real 10 0G buy (tx 0xa5b2...8115), captured on 6 Nov 2026. No network."""

from __future__ import annotations

import pytest

from synapse0g import funding

POOL = "0x87340a0c514f1f920b0073ead0dd0286e3d36e1978a1548011370ab49e794222"
TOKEN = "0xbd5ceb67a87695a36dfc61582a25d5b854fe7c13"
LAUNCH = {"topics": [funding.LAUNCHED, POOL, "0x" + "0" * 24 + TOKEN[2:], "0x000000000000000000000000b6c1de241c5d1ac9b3c0d0afb72a49bf1d1fa6a2"],
          "data": "0x0000000000000000000000000000000000000000000000000000000000017ebc0000000000000000000000000000000000000000033b2e3c9fd0803ce80000000000000000000000000000000000000000000000000000000000000000000000",
          "blockNumber": "0x2b77b48", "transactionHash": "0x9a9ddca7ba4a48d542fe6f7fcda91c79558f6e3bf3234a6ab7b853295acea84e"}
CHARGE = {"topics": [funding.CHARGE_TAKEN, POOL, "0x00000000000000000000000076526f4815447f693468ef44e0204a0689178d9e"],
          "data": "0x" + "".join(w.rjust(64, "0") for w in ["1", "8ac7230489e80000", "16345785d8a0000", "0", "0", "0"]),
          "blockNumber": "0x2b77c00", "transactionHash": "0xa5b2e784a175d4b078d30fc2e4177f04b44bfbab8bc8d5abda406bca3c198115"}
# a split with a 25% compute share, as seen on recent launches
SPLIT = "0x" + "".join(w.rjust(64, "0") for w in ["1", "bb8", "1b58", "9c4", "75f79262" + "0" * 56])


def _string(text: str) -> str:
    raw = text.encode().hex()
    return "0x" + "20".rjust(64, "0") + hex(len(text))[2:].rjust(64, "0") + raw.ljust(64, "0")


class FakeChain:
    def __init__(self, trader="0x1111111111111111111111111111111111111111"):
        self.trader = trader

    def head(self):
        return 45600000

    def eth_call(self, to, data):
        if data.startswith(funding.IS_COMFY_TOKEN):
            return "0x" + "1".rjust(64, "0")
        if data.startswith(funding.SPLIT_OF):
            return SPLIT
        return _string({funding.NAME: "comfy", funding.SYMBOL: "COMFY"}[data])

    def logs(self, topics, start, end, address=None):
        return [LAUNCH] if topics[0] == funding.LAUNCHED else [CHARGE]

    def sender(self, tx):
        return self.trader


class FakeAccount:
    def __init__(self, deposited):
        self.deposited = deposited

    def balance(self):
        return {"address": "0xagent", "spendable_0g": self.deposited - 0.01, "deposited_0g": self.deposited}


@pytest.fixture(autouse=True)
def _cache(tmp_path, monkeypatch):
    monkeypatch.setattr(funding, "CACHE", tmp_path / "funding_cache.json")


def _cfg(**over):
    cfg = funding.load_config()
    cfg.update(over)
    return cfg


def test_events_decode_as_the_chain_wrote_them():
    launch = funding.decode_launched(LAUNCH)
    assert launch["pool_id"] == POOL and launch["token"] == TOKEN and launch["ladder_supply"] == 1e9
    charge = funding.decode_charge(CHARGE)
    assert charge["is_buy"] and charge["quote_0g"] == 10 and charge["base_fee_0g"] == pytest.approx(0.1) and charge["native"]
    split = funding.decode_split(SPLIT)
    assert (split["protocol_bps"], split["creator_bps"], split["compute_bps"]) == (3000, 7000, 2500)


def test_a_charge_splits_into_creator_and_compute_shares():
    s = funding.shares(funding.decode_charge(CHARGE), funding.decode_split(SPLIT))
    assert s["creators_0g"] == pytest.approx(0.07) and s["compute_0g"] == pytest.approx(0.0175)
    assert s["claimable_0g"] == pytest.approx(0.0525)


def test_nothing_is_shown_before_it_happens():
    snap = funding.snapshot(_cfg(), chain=FakeChain(), spent={"calls": 5, "billed_usd": 0.01})
    states = {s["key"]: s["state"] for s in snap["stages"]}
    assert states == {"identity": "waiting", "token": "waiting", "fees": "waiting", "credit": "unknown",
                      "balance": "unknown", "inference": "waiting"}
    inference = snap["stages"][-1]
    assert "team's own deposit" in inference["detail"]  # calls paid by people are never shown as earned


def test_the_whole_loop_once_every_stage_has_happened():
    cfg = _cfg(agent_id="42", owner="0xowner", token=TOKEN)
    funding.snapshot(cfg, chain=FakeChain(), account=FakeAccount(1.0))
    snap = funding.snapshot(cfg, chain=FakeChain(), account=FakeAccount(1.0175), spent={"calls": 5, "billed_usd": 0.01})
    stages = {s["key"]: s for s in snap["stages"]}
    assert all(s["state"] == "done" for s in stages.values()), {k: s["detail"] for k, s in stages.items()}
    assert stages["fees"]["evidence"]["totals"]["compute_0g"] == pytest.approx(0.0175)
    assert stages["credit"]["evidence"]["rises"][0]["by_0g"] == pytest.approx(0.0175)
    assert stages["token"]["evidence"]["symbol"] == "COMFY"


def test_the_teams_own_trades_are_shown_but_never_counted():
    team = "0x2222222222222222222222222222222222222222"
    cfg = _cfg(agent_id="42", token=TOKEN, team_wallets=[team])
    fees = next(s for s in funding.snapshot(cfg, chain=FakeChain(trader=team))["stages"] if s["key"] == "fees")
    assert fees["state"] == "waiting" and fees["evidence"]["totals"]["team_trades"] == 1
    assert fees["evidence"]["totals"]["compute_0g"] == 0 and fees["evidence"]["recent"][0]["team"]


def test_the_catalog_rate_converts_0g_to_usd():
    model = {"pricing": {"prompt": "1304662813734"}, "pricing_usd": {"prompt": "0.00000019125"}}
    assert funding.usd_per_0g(model) == pytest.approx(0.14659, rel=1e-3)
    assert funding.usd_per_0g({}) is None
