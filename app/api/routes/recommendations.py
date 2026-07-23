"""Recommendation endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.schemas.models import RecommendationOut
from app.services import recommendations as svc

router = APIRouter(prefix="/recommendations", tags=["recommendations"])


@router.get("", response_model=list[RecommendationOut])
async def list_recommendations(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    min_confidence: float | None = Query(None, ge=0.0, le=1.0),
    condition_id: str | None = None,
    direction: str | None = Query(None, pattern="^(?i)(buy|avoid)$"),
    session: AsyncSession = Depends(get_session),
) -> list[RecommendationOut]:
    rows = await svc.list_recommendations(
        session,
        limit=limit,
        offset=offset,
        min_confidence=min_confidence,
        condition_id=condition_id,
        direction=direction,
    )
    return [RecommendationOut(**r) for r in rows]
