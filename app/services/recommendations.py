"""Read service for recommendations."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Market, Recommendation


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
