"""Read service for recommendations."""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import utcnow
from app.db.models import Market, Recommendation, Trade


async def list_recommendations(
    session: AsyncSession,
    *,
    limit: int = 50,
    offset: int = 0,
    min_confidence: float | None = None,
    condition_id: str | None = None,
    direction: str | None = None,
) -> list[dict]:
    """Active recommendations, newest/strongest first, with market question joined."""
    stmt = (
        select(Recommendation, Market.question)
        .join(Market, Market.condition_id == Recommendation.condition_id)
        .where(Recommendation.status == "active")
    )
    if min_confidence is not None:
        stmt = stmt.where(Recommendation.confidence >= min_confidence)
    if condition_id:
        stmt = stmt.where(Recommendation.condition_id == condition_id)
    if direction:
        stmt = stmt.where(Recommendation.direction == direction.upper())
    stmt = stmt.order_by(Recommendation.confidence.desc()).limit(limit).offset(offset)

    out: list[dict] = []
    for rec, question in (await session.execute(stmt)).all():
        data = {c.name: getattr(rec, c.name) for c in rec.__table__.columns}
        data["question"] = question
        out.append(data)
    return out


async def live_recommendations(
    session: AsyncSession, *, limit: int = 50, window_minutes: int = 240
) -> list[dict]:
    """Recommendations on markets trading *right now* (recent trade activity first).

    A market's recent trade count is a proxy for a live/in-play game. Falls back
    to the strongest active recs when no recent trades are present (e.g. fixtures
    whose synthetic trade timestamps are static).
    """
    cutoff = utcnow() - timedelta(minutes=window_minutes)
    rows = (
        await session.execute(
            select(Trade.condition_id, func.count(Trade.id))
            .where(Trade.timestamp.is_not(None), Trade.timestamp >= cutoff)
            .group_by(Trade.condition_id)
        )
    ).all()
    counts = {cid: n for cid, n in rows}

    recs = await list_recommendations(session, limit=500)
    for r in recs:
        r["recent_trades"] = counts.get(r["condition_id"], 0)

    live = [r for r in recs if r["recent_trades"] > 0]
    src = live if live else recs  # fallback keeps the page useful offline
    src.sort(key=lambda r: (r["recent_trades"], r["consensus_size_usd"]), reverse=True)
    return src[:limit]
