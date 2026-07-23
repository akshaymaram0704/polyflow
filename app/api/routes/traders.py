"""Trader + leaderboard endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.schemas.models import (
    LeaderboardEntry,
    PositionOut,
    TraderDetailOut,
    TraderScoreOut,
)
from app.services import traders as svc

router = APIRouter(prefix="/traders", tags=["traders"])


@router.get("/leaderboard", response_model=list[LeaderboardEntry])
async def leaderboard(
    window: str = "all",
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_session),
) -> list[LeaderboardEntry]:
    rows = await svc.leaderboard(session, window=window, limit=limit, offset=offset)
    return [LeaderboardEntry(**r) for r in rows]


@router.get("/{wallet}", response_model=TraderDetailOut)
async def get_trader(
    wallet: str,
    session: AsyncSession = Depends(get_session),
) -> TraderDetailOut:
    trader = await svc.get_trader(session, wallet)
    if trader is None:
        raise HTTPException(status_code=404, detail="Trader not found")

    positions = await svc.top_positions(session, wallet)
    score = next((s for s in trader.scores if s.window == "all"), None)
    return TraderDetailOut(
        proxy_wallet=trader.proxy_wallet,
        username=trader.username,
        total_pnl=trader.total_pnl,
        total_volume=trader.total_volume,
        position_count=trader.position_count,
        score=TraderScoreOut.model_validate(score) if score else None,
        positions=[PositionOut.model_validate(p) for p in positions],
    )
