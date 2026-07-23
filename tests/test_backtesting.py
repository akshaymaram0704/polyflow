"""Tests for the backtesting engine."""

from __future__ import annotations

from app.backtesting.engine import run_backtest
from app.backtesting.scenario import build_scenario


def test_scenario_split_is_disjoint():
    sc = build_scenario(n_markets=40, n_traders=100, seed=1)
    hist_cids = {p["condition_id"] for ps in sc.history_positions.values() for p in ps}
    test_cids = {p["condition_id"] for ps in sc.test_positions.values() for p in ps}
    # History and test markets must not overlap (no look-ahead).
    assert hist_cids.isdisjoint(test_cids)
    # Every test market has a resolution answer key.
    assert test_cids.issubset(set(sc.resolution))


def test_backtest_beats_crowd_baseline():
    sc = build_scenario(n_markets=80, n_traders=200, seed=42)
    r = run_backtest(sc, min_confidence=0.6, top_n=50)

    assert r.n_recommendations > 0
    assert 0.0 <= r.hit_rate <= 1.0
    # A ranking that selects skilled traders should beat a coin flip and the crowd.
    assert r.hit_rate > 0.5
    assert r.edge_vs_crowd > 0.05
    assert r.confidence_buckets  # calibration buckets populated
    assert 0.0 <= r.brier_score <= 1.0


def test_backtest_no_recs_when_confidence_floor_too_high():
    sc = build_scenario(n_markets=30, n_traders=80, seed=7)
    r = run_backtest(sc, min_confidence=0.999, top_n=20)
    assert r.n_recommendations == 0
    assert r.hit_rate == 0
