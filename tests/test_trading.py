"""Tests for trading risk logic (pure) and paper execution (via the API)."""

from __future__ import annotations

import pytest

from app.trading.risk import RiskParams, plan_orders

PARAMS = RiskParams(
    min_confidence=0.7, max_order_usd=50.0, max_daily_usd=120.0, max_open_positions=5
)


def _cand(i, conf=0.8, price=0.5, asset=None, direction="BUY"):
    return {
        "id": i,
        "condition_id": f"0xm{i}",
        "asset": asset or f"T{i}",
        "outcome": "Yes",
        "direction": direction,
        "confidence": conf,
        "price": price,
    }


def test_plan_sizes_and_caps_daily_budget():
    cands = [_cand(i) for i in range(5)]
    plans = plan_orders(cands, PARAMS, spent_today=0.0, open_positions=0)
    submits = [p for p in plans if p.action == "submit"]
    # 120 daily / 50 per order -> two full orders (50+50) then 20 remaining.
    assert [p.size_usd for p in submits] == [50.0, 50.0, 20.0]
    assert sum(p.size_usd for p in submits) == 120.0


def test_plan_skips_low_confidence():
    plans = plan_orders([_cand(1, conf=0.5)], PARAMS, spent_today=0.0, open_positions=0)
    assert plans[0].action == "skip"
    assert "confidence" in plans[0].reason


def test_plan_skips_held_and_nonbuy_and_bad_price():
    cands = [
        _cand(1, asset="HELD"),
        _cand(2, direction="AVOID"),
        _cand(3, price=1.0),
    ]
    plans = plan_orders(cands, PARAMS, spent_today=0.0, open_positions=0, held_assets={"HELD"})
    reasons = {p.asset: p.reason for p in plans}
    assert plans[0].action == "skip" and "holding" in reasons["HELD"]
    assert plans[1].action == "skip" and "non-buy" in reasons["T2"]
    assert plans[2].action == "skip" and "price" in reasons["T3"]


def test_plan_respects_max_open_positions():
    cands = [_cand(i) for i in range(4)]
    # Already at the cap (5) -> every further candidate must be skipped.
    plans = plan_orders(cands, PARAMS, spent_today=0.0, open_positions=5)
    assert all(p.action == "skip" for p in plans)
    assert all("max open positions" in p.reason for p in plans)


def test_plan_respects_prior_daily_spend():
    plans = plan_orders([_cand(1)], PARAMS, spent_today=120.0, open_positions=0)
    assert plans[0].action == "skip"
    assert "daily budget" in plans[0].reason


# --- API-level paper execution ------------------------------------------------
@pytest.mark.asyncio
async def test_execute_disabled_by_default(client):
    r = await client.post("/trading/execute")
    assert r.status_code == 200
    body = r.json()
    assert body["enabled"] is False
    assert body["submitted"] == 0


@pytest.mark.asyncio
async def test_execute_paper_mode(client):
    from app.config import settings

    # Temporarily enable paper trading with a permissive confidence floor.
    prev = (settings.trading_enabled, settings.trading_mode, settings.trading_min_confidence)
    settings.trading_enabled = True
    settings.trading_mode = "paper"
    settings.trading_min_confidence = 0.0
    try:
        r = await client.post("/trading/execute")
        assert r.status_code == 200
        body = r.json()
        assert body["enabled"] is True
        assert body["mode"] == "paper"
        assert body["submitted"] >= 1
        assert all(o["mode"] == "paper" for o in body["orders"])

        orders = (await client.get("/trading/orders", params={"mode": "paper"})).json()
        assert len(orders) >= 1
    finally:
        (
            settings.trading_enabled,
            settings.trading_mode,
            settings.trading_min_confidence,
        ) = prev


@pytest.mark.asyncio
async def test_trading_status_no_secrets(client):
    r = await client.get("/trading/status")
    assert r.status_code == 200
    body = r.json()
    assert "limits" in body
    # Ensure we never leak key material.
    assert "private_key" not in str(body).lower()


@pytest.mark.asyncio
async def test_backtest_endpoint(client):
    r = await client.get("/backtest", params={"min_confidence": 0.6, "top_n": 50})
    assert r.status_code == 200
    body = r.json()
    assert body["n_recommendations"] >= 0
    assert 0.0 <= body["hit_rate"] <= 1.0


# --- PnL tracker --------------------------------------------------------------
def test_settlement_pnl_win_and_loss():
    from app.trading.pnl import settlement_pnl

    # 100 shares bought for $50; wins -> pays $100 -> +$50 realized.
    assert settlement_pnl("T1", "T1", size_shares=100, size_usd=50.0) == 50.0
    # Loses -> pays $0 -> -$50.
    assert settlement_pnl("T1", "T2", size_shares=100, size_usd=50.0) == -50.0


def test_mark_to_market():
    from app.trading.pnl import mark

    assert mark(size_shares=100, size_usd=50.0, cur_price=0.6) == 10.0
    assert mark(size_shares=100, size_usd=50.0, cur_price=0.4) == -10.0


def test_winning_asset_resolution():
    from app.trading.pnl import winning_asset

    assert winning_asset(["T1", "T2"], [1.0, 0.0]) == "T1"
    assert winning_asset(["T1", "T2"], [0.0, 1.0]) == "T2"
    # Unresolved market -> no winner.
    assert winning_asset(["T1", "T2"], [0.55, 0.45]) is None


@pytest.mark.asyncio
async def test_pnl_summary_after_paper_execution(client):
    from app.config import settings

    prev = (settings.trading_enabled, settings.trading_mode, settings.trading_min_confidence)
    settings.trading_enabled = True
    settings.trading_mode = "paper"
    settings.trading_min_confidence = 0.0
    try:
        await client.post("/trading/execute")
        summary = (await client.get("/trading/pnl", params={"mode": "paper"})).json()
        assert summary["mode"] == "paper"
        assert summary["invested_usd"] > 0
        assert "total_pnl" in summary
        # Fixture markets are active (unresolved), so nothing settles yet.
        settled = (await client.post("/trading/settle", params={"mode": "paper"})).json()
        assert settled["settled"] == 0
    finally:
        (
            settings.trading_enabled,
            settings.trading_mode,
            settings.trading_min_confidence,
        ) = prev
