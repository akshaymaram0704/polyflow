"""Tests for Polymarket payload parsing and the offline fixture client."""

from __future__ import annotations

import pytest

from app.clients.fixtures import FixturePolymarketClient
from app.clients.polymarket import (
    normalize_holder,
    normalize_market,
    normalize_position,
    normalize_trade,
)


def test_normalize_market_parses_json_string_arrays(raw_samples):
    m = normalize_market(raw_samples["gamma_market"])
    assert m["condition_id"] == "0xabc123"
    assert m["outcomes"] == ["Yes", "No"]
    assert m["clob_token_ids"] == ["111", "222"]
    assert m["outcome_prices"] == [0.62, 0.38]
    assert m["volume"] == 123456.78
    assert m["active"] is True
    assert m["start_date"] is not None


def test_normalize_position(raw_samples):
    p = normalize_position(raw_samples["data_position"])
    assert p["proxy_wallet"] == "0xdeadbeef"
    assert p["asset"] == "111"
    assert p["cash_pnl"] == 120.0
    assert p["percent_pnl"] == 0.24


def test_normalize_trade(raw_samples):
    t = normalize_trade(raw_samples["data_trade"])
    assert t["side"] == "BUY"
    assert t["usd_value"] == 500.0
    assert t["timestamp"] is not None


def test_normalize_holder(raw_samples):
    h = normalize_holder(raw_samples["data_holder"])
    assert h["wallet"] == "0xdeadbeef"
    assert h["amount"] == 1000.0


def test_flatten_holders_shapes():
    from app.clients.polymarket import _flatten_holders

    # per-token groups with nested "holders" (the live Data API shape)
    grouped = [{"token": "1", "holders": [{"proxyWallet": "0xa", "amount": 5}]},
               {"token": "2", "holders": [{"proxyWallet": "0xb", "amount": 3}]}]
    assert [h["proxyWallet"] for h in _flatten_holders(grouped)] == ["0xa", "0xb"]
    # dict with holders
    assert _flatten_holders({"holders": [{"proxyWallet": "0xc"}]})[0]["proxyWallet"] == "0xc"
    # bare list
    assert _flatten_holders([{"proxyWallet": "0xd"}])[0]["proxyWallet"] == "0xd"
    assert _flatten_holders(None) == []


def test_normalize_market_defensive_on_missing_fields():
    m = normalize_market({})
    assert m["outcomes"] == [] and m["volume"] == 0.0


@pytest.mark.asyncio
async def test_fixture_client_universe():
    client = FixturePolymarketClient()

    markets = await client.get_markets(limit=500)
    assert len(markets) >= 500
    assert all("condition_id" in m for m in markets)

    holders = await client.get_holders(markets[0]["condition_id"], limit=10)
    assert isinstance(holders, list)

    wallet = holders[0]["wallet"]
    positions = await client.get_positions(wallet)
    assert len(positions) >= 1
    assert positions[0]["proxy_wallet"] == wallet

    token = markets[0]["clob_token_ids"][0]
    price = await client.get_price(token)
    assert price is not None and 0.0 <= price <= 1.0
