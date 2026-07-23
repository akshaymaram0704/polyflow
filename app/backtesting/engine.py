"""Backtest evaluation engine.

Reuses the production scoring code (``score_cohort`` + ``build_recommendations``)
so the backtest measures the *real* algorithm, not a reimplementation:

    1. Rank traders on their resolved history book.
    2. Build consensus recommendations from the top traders' test-market picks.
    3. Grade each recommendation against the true resolution:
         payoff = 1 if the recommended token won else 0
         roi    = (payoff - entry_price) / entry_price
    4. Compare against a "follow the crowd favorite" baseline on the same markets.
    5. Report hit-rate, ROI, Brier score and a per-confidence-bucket calibration.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

from app.backtesting.scenario import Scenario, build_scenario
from app.config import settings
from app.logging import get_logger
from app.scoring.ranking import build_recommendations, score_cohort

log = get_logger(__name__)


@dataclass
class Bucket:
    label: str
    count: int
    hit_rate: float
    mean_roi: float


@dataclass
class BacktestResult:
    n_recommendations: int
    hit_rate: float
    mean_roi: float
    total_pnl_per_dollar: float
    brier_score: float
    baseline_hit_rate: float
    baseline_mean_roi: float
    edge_vs_crowd: float  # hit_rate - baseline_hit_rate
    confidence_buckets: list[Bucket] = field(default_factory=list)
    params: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        d = asdict(self)
        return d


def _bucketize(graded: list[dict]) -> list[Bucket]:
    edges = [(0.0, 0.6), (0.6, 0.7), (0.7, 0.8), (0.8, 0.9), (0.9, 1.01)]
    buckets: list[Bucket] = []
    for lo, hi in edges:
        rows = [g for g in graded if lo <= g["confidence"] < hi]
        if not rows:
            continue
        buckets.append(
            Bucket(
                label=f"{lo:.2f}-{min(hi, 1.0):.2f}",
                count=len(rows),
                hit_rate=round(sum(g["correct"] for g in rows) / len(rows), 4),
                mean_roi=round(sum(g["roi"] for g in rows) / len(rows), 4),
            )
        )
    return buckets


def run_backtest(
    scenario: Scenario | None = None,
    *,
    min_confidence: float | None = None,
    top_n: int | None = None,
) -> BacktestResult:
    scenario = scenario or build_scenario()
    min_confidence = settings.min_confidence if min_confidence is None else min_confidence
    top_n = top_n or settings.top_traders_for_recs

    scores = score_cohort(scenario.history_positions)
    recs = build_recommendations(
        scores, scenario.test_positions, top_n=top_n, min_confidence=min_confidence
    )

    graded: list[dict] = []
    for r in recs:
        winner = scenario.resolution.get(r.condition_id)
        if winner is None:
            continue
        correct = 1 if r.asset == winner else 0
        entry = r.avg_entry_price or 0.0
        payoff = 1.0 if correct else 0.0
        roi = (payoff - entry) / entry if entry else 0.0
        graded.append({"confidence": r.confidence, "correct": correct, "roi": roi})

    n = len(graded)
    if n == 0:
        log.warning("Backtest produced no gradable recommendations")
        return BacktestResult(0, 0, 0, 0, 0, 0, 0, 0, [], {"note": "no recommendations"})

    hit_rate = sum(g["correct"] for g in graded) / n
    mean_roi = sum(g["roi"] for g in graded) / n
    brier = sum((g["confidence"] - g["correct"]) ** 2 for g in graded) / n

    # Crowd baseline: on the same recommended markets, buy the pre-resolution
    # favorite (higher-priced) outcome and grade it identically.
    rec_cids = {r.condition_id for r in recs}
    b_correct, b_roi, b_n = 0, 0.0, 0
    for cid in rec_cids:
        prices = scenario.market_prices.get(cid)
        winner = scenario.resolution.get(cid)
        if not prices or winner is None:
            continue
        fav_idx = 0 if prices[0] >= prices[1] else 1
        # Winner token id ends in 'y' (idx 0) or 'n' (idx 1) in the scenario.
        won = 1 if winner.endswith("y" if fav_idx == 0 else "n") else 0
        entry = prices[fav_idx]
        b_correct += won
        b_roi += ((1.0 if won else 0.0) - entry) / entry if entry else 0.0
        b_n += 1

    baseline_hit = b_correct / b_n if b_n else 0.0
    baseline_roi = b_roi / b_n if b_n else 0.0

    result = BacktestResult(
        n_recommendations=n,
        hit_rate=round(hit_rate, 4),
        mean_roi=round(mean_roi, 4),
        total_pnl_per_dollar=round(mean_roi * n, 4),
        brier_score=round(brier, 4),
        baseline_hit_rate=round(baseline_hit, 4),
        baseline_mean_roi=round(baseline_roi, 4),
        edge_vs_crowd=round(hit_rate - baseline_hit, 4),
        confidence_buckets=_bucketize(graded),
        params={
            "min_confidence": min_confidence,
            "top_n": top_n,
            "history_traders": len(scenario.history_positions),
            "test_markets": len(scenario.resolution),
        },
    )
    log.info(
        "Backtest: n=%d hit=%.3f roi=%.3f vs crowd hit=%.3f (edge %+.3f)",
        n,
        hit_rate,
        mean_roi,
        baseline_hit,
        result.edge_vs_crowd,
    )
    return result
