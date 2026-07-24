"""Quantitative trader-ranking and recommendation engine.

Pipeline:
    1. Per trader, compute raw performance metrics from their position snapshot.
    2. Normalize each metric across the cohort with a robust fractional
       percentile rank (outlier-insensitive, deterministic).
    3. Blend into four interpretable sub-scores in [0, 1]:
         profitability | consistency | sizing | risk_adjusted
    4. Combine into a weighted ``composite`` score and rank traders.
    5. Aggregate the top-ranked traders' positions per market into a
       score-weighted directional consensus -> high-confidence recommendations.

The pure functions (``compute_metrics``, ``score_cohort``,
``build_recommendations``) contain no I/O and are unit-tested directly.
"""

from __future__ import annotations

import statistics as stats
from dataclasses import dataclass, field

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.base import utcnow
from app.db.models import Market, Recommendation, TraderPosition, TraderScore
from app.logging import get_logger

log = get_logger(__name__)

_EPS = 1e-9
_SIZE_REF_USD = 10_000.0  # saturation reference for consensus-size weighting


# --------------------------------------------------------------------------- #
# Normalization
# --------------------------------------------------------------------------- #
def percentile_rank(values: list[float]) -> list[float]:
    """Fractional percentile rank in [0, 1] for each value (ties averaged).

    A single value maps to 0.5. Robust to outliers because only ordering
    matters, not magnitude.
    """
    n = len(values)
    if n == 0:
        return []
    if n == 1:
        return [0.5]
    out: list[float] = []
    for v in values:
        less = sum(1 for x in values if x < v)
        equal = sum(1 for x in values if x == v)
        out.append((less + 0.5 * equal) / n)
    return out


# --------------------------------------------------------------------------- #
# Per-trader raw metrics
# --------------------------------------------------------------------------- #
@dataclass
class TraderMetrics:
    wallet: str
    total_pnl: float = 0.0
    roi: float = 0.0
    win_rate: float = 0.0
    sharpe: float = 0.0
    sortino: float = 0.0
    max_drawdown: float = 0.0
    conviction_edge: float = 0.0
    diversification: float = 0.0
    position_count: int = 0


def compute_metrics(wallet: str, positions: list[dict]) -> TraderMetrics:
    """Compute raw performance metrics for one trader's position snapshot.

    Each position dict needs: ``percent_pnl``, ``cash_pnl``, ``realized_pnl``,
    ``initial_value``.
    """
    if not positions:
        return TraderMetrics(wallet=wallet)

    returns = [float(p.get("percent_pnl", 0.0)) for p in positions]
    sizes = [max(0.0, float(p.get("initial_value", 0.0))) for p in positions]
    pnls = [float(p.get("cash_pnl", 0.0)) + float(p.get("realized_pnl", 0.0)) for p in positions]

    total_pnl = sum(pnls)
    total_invested = sum(sizes) or _EPS
    roi = total_pnl / total_invested
    win_rate = sum(1 for r in returns if r > 0) / len(returns)

    mean_r = stats.fmean(returns)
    std_r = stats.pstdev(returns) if len(returns) > 1 else 0.0
    sharpe = mean_r / (std_r + _EPS)

    downside = [r for r in returns if r < 0]
    downside_std = stats.pstdev(downside) if len(downside) > 1 else abs(min(returns, default=0.0))
    sortino = mean_r / (downside_std + _EPS)

    max_drawdown = max(0.0, -min(returns))

    # Sizing: did bigger bets earn more than an equal-weight book would?
    weights = [s / total_invested for s in sizes]
    size_weighted_return = sum(w * r for w, r in zip(weights, returns, strict=True))
    conviction_edge = size_weighted_return - mean_r

    # Concentration (Herfindahl); diversification is its complement in [0, 1].
    herfindahl = sum(w * w for w in weights)
    diversification = 1.0 - herfindahl

    return TraderMetrics(
        wallet=wallet,
        total_pnl=total_pnl,
        roi=roi,
        win_rate=win_rate,
        sharpe=sharpe,
        sortino=sortino,
        max_drawdown=max_drawdown,
        conviction_edge=conviction_edge,
        diversification=diversification,
        position_count=len(positions),
    )


# --------------------------------------------------------------------------- #
# Cohort scoring
# --------------------------------------------------------------------------- #
@dataclass
class TraderScoreResult:
    wallet: str
    profitability: float
    consistency: float
    sizing: float
    risk_adjusted: float
    composite: float
    rank: int = 0
    metrics: TraderMetrics = field(default=None)  # type: ignore[assignment]


def score_cohort(
    positions_by_trader: dict[str, list[dict]],
    weights: dict[str, float] | None = None,
) -> list[TraderScoreResult]:
    """Score and rank every trader relative to the cohort.

    Returns results sorted by composite descending, with ``rank`` assigned
    (1 = best).
    """
    weights = weights or settings.ranking_weights
    metrics = [compute_metrics(w, p) for w, p in positions_by_trader.items()]
    if not metrics:
        return []

    # Cohort-normalized components.
    pct_roi = percentile_rank([m.roi for m in metrics])
    pct_pnl = percentile_rank([m.total_pnl for m in metrics])
    pct_sharpe = percentile_rank([m.sharpe for m in metrics])
    pct_count = percentile_rank([float(m.position_count) for m in metrics])
    pct_conv = percentile_rank([m.conviction_edge for m in metrics])
    pct_sortino = percentile_rank([m.sortino for m in metrics])
    pct_dd = percentile_rank([m.max_drawdown for m in metrics])

    results: list[TraderScoreResult] = []
    for i, m in enumerate(metrics):
        profitability = 0.6 * pct_roi[i] + 0.4 * pct_pnl[i]
        consistency = 0.5 * m.win_rate + 0.3 * pct_sharpe[i] + 0.2 * pct_count[i]
        sizing = 0.6 * pct_conv[i] + 0.4 * m.diversification
        risk_adjusted = 0.6 * pct_sortino[i] + 0.4 * (1.0 - pct_dd[i])

        composite = (
            weights["profitability"] * profitability
            + weights["consistency"] * consistency
            + weights["sizing"] * sizing
            + weights["risk_adjusted"] * risk_adjusted
        )
        results.append(
            TraderScoreResult(
                wallet=m.wallet,
                profitability=round(profitability, 6),
                consistency=round(consistency, 6),
                sizing=round(sizing, 6),
                risk_adjusted=round(risk_adjusted, 6),
                composite=round(composite, 6),
                metrics=m,
            )
        )

    results.sort(key=lambda r: r.composite, reverse=True)
    for rank, r in enumerate(results, start=1):
        r.rank = rank
    return results


# --------------------------------------------------------------------------- #
# Recommendation engine
# --------------------------------------------------------------------------- #
@dataclass
class RecCandidate:
    condition_id: str
    outcome: str
    asset: str
    direction: str
    confidence: float
    consensus_size_usd: float
    supporter_count: int
    avg_entry_price: float
    current_price: float
    rationale: dict


def build_recommendations(
    scores: list[TraderScoreResult],
    positions_by_trader: dict[str, list[dict]],
    top_n: int,
    min_confidence: float,
    min_price: float = 0.0,
    max_price: float = 1.0,
) -> list[RecCandidate]:
    """Turn the top-ranked traders' books into per-market consensus calls.

    For each market, the outcome backed by the highest score-weighted capital
    wins. Confidence blends directional *agreement*, the supporters' average
    *skill score*, and the *size* of the consensus.

    ``min_price``/``max_price`` bound the recommended outcome's market price so we
    surface *actionable, higher-upside* positions (e.g. 20–65¢) rather than
    near-certain 95–100¢ favorites that offer almost no reward.
    """
    score_map = {s.wallet: s.composite for s in scores}
    top_wallets = {s.wallet for s in scores[:top_n]}

    # market -> asset -> aggregate
    markets: dict[str, dict[str, dict]] = {}
    for wallet in top_wallets:
        score = score_map.get(wallet, 0.0)
        for p in positions_by_trader.get(wallet, []):
            cid, asset = p.get("condition_id"), p.get("asset")
            if not cid or not asset or p.get("size", 0) <= 0:
                continue
            agg = markets.setdefault(cid, {}).setdefault(
                asset,
                {
                    "outcome": p.get("outcome") or "",
                    "weighted_score": 0.0,
                    "size_usd": 0.0,
                    "entry_num": 0.0,
                    "supporters": [],
                    "cur_price": float(p.get("cur_price", 0.0)),
                },
            )
            size_usd = float(p.get("current_value", 0.0))
            agg["weighted_score"] += score * max(size_usd, 1.0)
            agg["size_usd"] += size_usd
            agg["entry_num"] += float(p.get("avg_price", 0.0)) * size_usd
            agg["cur_price"] = float(p.get("cur_price", agg["cur_price"]))
            agg["supporters"].append(
                {"wallet": wallet, "score": round(score, 4), "size_usd": round(size_usd, 2)}
            )

    candidates: list[RecCandidate] = []
    for cid, by_asset in markets.items():
        total_weight = sum(a["weighted_score"] for a in by_asset.values()) or _EPS
        # Winning outcome = most score-weighted capital behind it.
        best_asset, best = max(by_asset.items(), key=lambda kv: kv[1]["weighted_score"])
        agreement = best["weighted_score"] / total_weight  # 0.5..1 for binary

        supporters = sorted(best["supporters"], key=lambda s: s["size_usd"], reverse=True)
        avg_score = stats.fmean([s["score"] for s in supporters]) if supporters else 0.0
        size_factor = best["size_usd"] / (best["size_usd"] + _SIZE_REF_USD)
        confidence = 0.5 * agreement + 0.3 * avg_score + 0.2 * size_factor

        if confidence < min_confidence:
            continue
        # Risk band: skip near-certain favorites (tiny upside) and extreme longshots.
        price = best["cur_price"] or (best["entry_num"] / (best["size_usd"] or _EPS))
        if not (min_price <= price <= max_price):
            continue
        avg_entry = best["entry_num"] / (best["size_usd"] or _EPS)
        candidates.append(
            RecCandidate(
                condition_id=cid,
                outcome=best["outcome"],
                asset=best_asset,
                direction="BUY",
                confidence=round(confidence, 4),
                consensus_size_usd=round(best["size_usd"], 2),
                supporter_count=len(supporters),
                avg_entry_price=round(avg_entry, 4),
                current_price=round(best["cur_price"], 4),
                rationale={
                    "agreement": round(agreement, 4),
                    "avg_trader_score": round(avg_score, 4),
                    "size_factor": round(size_factor, 4),
                    "top_traders": supporters[:10],
                },
            )
        )

    candidates.sort(key=lambda c: c.confidence, reverse=True)
    return candidates


# --------------------------------------------------------------------------- #
# DB orchestration
# --------------------------------------------------------------------------- #
async def _load_positions(session: AsyncSession) -> dict[str, list[dict]]:
    result = await session.execute(select(TraderPosition))
    grouped: dict[str, list[dict]] = {}
    for pos in result.scalars():
        grouped.setdefault(pos.proxy_wallet, []).append(
            {
                "condition_id": pos.condition_id,
                "asset": pos.asset,
                "outcome": pos.outcome,
                "size": pos.size,
                "avg_price": pos.avg_price,
                "cur_price": pos.cur_price,
                "initial_value": pos.initial_value,
                "current_value": pos.current_value,
                "percent_pnl": pos.percent_pnl,
                "cash_pnl": pos.cash_pnl,
                "realized_pnl": pos.realized_pnl,
            }
        )
    return grouped


async def run_scoring(session: AsyncSession, window: str = "all") -> dict[str, int]:
    """Recompute all trader scores and regenerate recommendations."""
    positions_by_trader = await _load_positions(session)
    scores = score_cohort(positions_by_trader)

    now = utcnow()
    for s in scores:
        existing = await session.execute(
            select(TraderScore).where(
                TraderScore.proxy_wallet == s.wallet, TraderScore.window == window
            )
        )
        row = existing.scalar_one_or_none()
        if row is None:
            row = TraderScore(proxy_wallet=s.wallet, window=window)
            session.add(row)
        row.profitability = s.profitability
        row.consistency = s.consistency
        row.sizing = s.sizing
        row.risk_adjusted = s.risk_adjusted
        row.composite = s.composite
        row.rank = s.rank
        row.computed_at = now
    await session.commit()

    recs = build_recommendations(
        scores,
        positions_by_trader,
        top_n=settings.top_traders_for_recs,
        min_confidence=settings.min_confidence,
        min_price=settings.rec_min_price,
        max_price=settings.rec_max_price,
    )
    # Keep only markets we actually track (FK safety), then replace active recs.
    valid = {row[0] for row in (await session.execute(select(Market.condition_id))).all()}
    await session.execute(delete(Recommendation).where(Recommendation.status == "active"))
    kept = 0
    for c in recs:
        if c.condition_id not in valid:
            continue
        session.add(
            Recommendation(
                condition_id=c.condition_id,
                outcome=c.outcome,
                asset=c.asset,
                direction=c.direction,
                confidence=c.confidence,
                consensus_size_usd=c.consensus_size_usd,
                supporter_count=c.supporter_count,
                avg_entry_price=c.avg_entry_price,
                current_price=c.current_price,
                rationale=c.rationale,
                status="active",
                generated_at=now,
            )
        )
        kept += 1
    await session.commit()

    log.info("run_scoring: scored %d traders, generated %d recommendations", len(scores), kept)
    return {"scored": len(scores), "recommendations": kept}
