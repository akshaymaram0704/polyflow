"""Read services for traders, scores and the leaderboard."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models import Trader, TraderPosition, TraderScore


async def leaderboard(
    session: AsyncSession, *, window: str = "all", limit: int = 50, offset: int = 0
) -> list[dict]:
    """Top traders by composite score, joined with their aggregate PnL."""
    stmt = (
        select(TraderScore, Trader)
        .join(Trader, Trader.proxy_wallet == TraderScore.proxy_wallet)
        .where(TraderScore.window == window)
        .order_by(TraderScore.composite.desc())
        .limit(limit)
        .offset(offset)
    )
    rows = (await session.execute(stmt)).all()
    return [
        {
            "rank": score.rank,
            "proxy_wallet": score.proxy_wallet,
            "username": trader.username,
            "composite": score.composite,
            "profitability": score.profitability,
            "consistency": score.consistency,
            "sizing": score.sizing,
            "risk_adjusted": score.risk_adjusted,
            "total_pnl": trader.total_pnl,
        }
        for score, trader in rows
    ]


async def get_trader(session: AsyncSession, wallet: str) -> Trader | None:
    stmt = (
        select(Trader)
        .where(Trader.proxy_wallet == wallet)
        .options(selectinload(Trader.positions), selectinload(Trader.scores))
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def top_positions(
    session: AsyncSession, wallet: str, limit: int = 50
) -> list[TraderPosition]:
    stmt = (
        select(TraderPosition)
        .where(TraderPosition.proxy_wallet == wallet)
        .order_by(TraderPosition.current_value.desc())
        .limit(limit)
    )
    return list((await session.execute(stmt)).scalars())
