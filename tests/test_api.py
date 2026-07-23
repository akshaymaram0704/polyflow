"""End-to-end API smoke tests against the seeded fixtures DB."""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_root_and_health(client):
    r = await client.get("/info")
    assert r.status_code == 200
    assert r.json()["name"] == "PolyFlow"

    # Root redirects to the dashboard.
    r = await client.get("/")
    assert r.status_code in (307, 308)

    r = await client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


@pytest.mark.asyncio
async def test_list_markets(client):
    r = await client.get("/markets", params={"limit": 5, "sort": "volume"})
    assert r.status_code == 200
    markets = r.json()
    assert len(markets) == 5
    # Sorted by volume descending.
    vols = [m["volume"] for m in markets]
    assert vols == sorted(vols, reverse=True)


@pytest.mark.asyncio
async def test_get_market_detail_and_price(client):
    listing = (await client.get("/markets", params={"limit": 1})).json()
    cid = listing[0]["condition_id"]

    r = await client.get(f"/markets/{cid}")
    assert r.status_code == 200
    assert r.json()["condition_id"] == cid

    r = await client.get(f"/markets/{cid}/price")
    assert r.status_code == 200
    prices = r.json()
    assert isinstance(prices, list) and len(prices) >= 1
    assert 0.0 <= prices[0]["price"] <= 1.0


@pytest.mark.asyncio
async def test_market_not_found(client):
    r = await client.get("/markets/0xdoesnotexist")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_leaderboard(client):
    r = await client.get("/traders/leaderboard", params={"limit": 20})
    assert r.status_code == 200
    board = r.json()
    assert len(board) >= 1
    composites = [e["composite"] for e in board]
    assert composites == sorted(composites, reverse=True)
    assert all(0.0 <= c <= 1.0 for c in composites)
    # Ranks should be present and start at 1.
    assert board[0]["rank"] == 1


@pytest.mark.asyncio
async def test_trader_detail(client):
    board = (await client.get("/traders/leaderboard", params={"limit": 1})).json()
    wallet = board[0]["proxy_wallet"]

    r = await client.get(f"/traders/{wallet}")
    assert r.status_code == 200
    body = r.json()
    assert body["proxy_wallet"] == wallet
    assert body["score"] is not None
    assert isinstance(body["positions"], list)


@pytest.mark.asyncio
async def test_recommendations(client):
    r = await client.get("/recommendations", params={"limit": 50})
    assert r.status_code == 200
    recs = r.json()
    assert isinstance(recs, list)
    if recs:
        rec = recs[0]
        assert 0.0 <= rec["confidence"] <= 1.0
        assert rec["direction"] in {"BUY", "AVOID"}
        assert "top_traders" in rec["rationale"]
        assert rec["question"]  # market question joined in


@pytest.mark.asyncio
async def test_recommendations_confidence_filter(client):
    r = await client.get("/recommendations", params={"min_confidence": 0.99})
    assert r.status_code == 200
    assert all(rec["confidence"] >= 0.99 for rec in r.json())
