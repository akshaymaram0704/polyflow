"""Unit tests for the pure ranking + recommendation math."""

from __future__ import annotations

from app.scoring.ranking import (
    TraderScoreResult,
    build_recommendations,
    compute_metrics,
    percentile_rank,
    score_cohort,
)


def test_percentile_rank_orders_and_bounds():
    ranks = percentile_rank([10.0, 20.0, 30.0, 40.0])
    assert ranks == sorted(ranks)  # monotonic with input order here
    assert all(0.0 <= r <= 1.0 for r in ranks)
    assert percentile_rank([5.0]) == [0.5]
    assert percentile_rank([]) == []


def test_percentile_rank_handles_ties():
    ranks = percentile_rank([1.0, 1.0, 1.0])
    assert ranks == [0.5, 0.5, 0.5]


def test_compute_metrics_basic():
    positions = [
        {"percent_pnl": 0.2, "cash_pnl": 100.0, "realized_pnl": 0.0, "initial_value": 500.0},
        {"percent_pnl": -0.1, "cash_pnl": -20.0, "realized_pnl": 0.0, "initial_value": 200.0},
    ]
    m = compute_metrics("0xwallet", positions)
    assert m.position_count == 2
    assert m.total_pnl == 80.0
    assert 0.0 < m.win_rate < 1.0  # one winner of two
    assert 0.0 <= m.diversification <= 1.0
    assert m.max_drawdown == 0.1  # worst single-position return is -0.1


def test_compute_metrics_empty():
    m = compute_metrics("0xempty", [])
    assert m.total_pnl == 0.0 and m.position_count == 0


def _winning_positions(n: int, ret: float, size: float):
    return [
        {
            "condition_id": f"0xm{i}",
            "asset": f"{i}01",
            "outcome": "Yes",
            "size": size,
            "avg_price": 0.4,
            "cur_price": 0.4 * (1 + ret),
            "percent_pnl": ret,
            "cash_pnl": size * 0.4 * ret,
            "realized_pnl": 0.0,
            "initial_value": size * 0.4,
            "current_value": size * 0.4 * (1 + ret),
        }
        for i in range(n)
    ]


def test_score_cohort_ranks_skilled_trader_first():
    good = _winning_positions(6, ret=0.35, size=1000)  # consistent big winners
    mediocre = _winning_positions(4, ret=0.02, size=300)  # small edge
    bad = [
        {**p, "percent_pnl": -0.3, "cash_pnl": -p["initial_value"] * 0.3}
        for p in _winning_positions(5, ret=-0.3, size=500)
    ]

    results = score_cohort(
        {"0xgood": good, "0xmed": mediocre, "0xbad": bad},
        weights={"profitability": 0.4, "consistency": 0.3, "sizing": 0.1, "risk_adjusted": 0.2},
    )
    by_wallet = {r.wallet: r for r in results}
    assert by_wallet["0xgood"].rank == 1
    assert by_wallet["0xbad"].rank == 3
    assert all(0.0 <= r.composite <= 1.0 for r in results)
    # Ranks are a permutation of 1..N.
    assert sorted(r.rank for r in results) == [1, 2, 3]


def test_build_recommendations_consensus():
    # Two high-scoring traders both long the same outcome in market 0xM.
    positions = {
        "0xA": [
            {
                "condition_id": "0xM",
                "asset": "T1",
                "outcome": "Yes",
                "size": 1000,
                "avg_price": 0.5,
                "cur_price": 0.6,
                "current_value": 600,
                "initial_value": 500,
            }
        ],
        "0xB": [
            {
                "condition_id": "0xM",
                "asset": "T1",
                "outcome": "Yes",
                "size": 800,
                "avg_price": 0.55,
                "cur_price": 0.6,
                "current_value": 480,
                "initial_value": 440,
            }
        ],
    }
    scores = [
        TraderScoreResult("0xA", 0.9, 0.9, 0.9, 0.9, 0.9, rank=1),
        TraderScoreResult("0xB", 0.8, 0.8, 0.8, 0.8, 0.8, rank=2),
    ]
    recs = build_recommendations(scores, positions, top_n=2, min_confidence=0.0)
    assert len(recs) == 1
    rec = recs[0]
    assert rec.condition_id == "0xM"
    assert rec.outcome == "Yes"
    assert rec.direction == "BUY"
    assert rec.supporter_count == 2
    assert 0.0 <= rec.confidence <= 1.0
    assert rec.consensus_size_usd == 1080.0
    assert rec.rationale["agreement"] == 1.0  # only one outcome backed


def test_build_recommendations_confidence_filter():
    positions = {
        "0xA": [
            {
                "condition_id": "0xM",
                "asset": "T1",
                "outcome": "Yes",
                "size": 1,
                "avg_price": 0.5,
                "cur_price": 0.5,
                "current_value": 0.5,
                "initial_value": 0.5,
            }
        ]
    }
    scores = [TraderScoreResult("0xA", 0.1, 0.1, 0.1, 0.1, 0.1, rank=1)]
    # A tiny, low-score position should not clear a high confidence floor.
    assert build_recommendations(scores, positions, top_n=1, min_confidence=0.9) == []
