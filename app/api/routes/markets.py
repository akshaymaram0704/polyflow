"""Market endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.schemas.models import MarketOut, PriceOut
from app.services import markets as svc

router = APIRouter(prefix="/markets", tags=["markets"])


@router.get("", response_model=list[MarketOut])
async def list_markets(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    category: str | None = None,
    active: bool = True,
    sort: str = Query("volume", pattern="^(volume|liquidity)$"),
    session: AsyncSession = Depends(get_session),
) -> list[MarketOut]:
    rows = await svc.list_markets(
        session, limit=limit, offset=offset, category=category, active=active, sort=sort
    )
    return [MarketOut.model_validate(m) for m in rows]


@router.get("/live", response_model=list[MarketOut])
async def live_markets(
    limit: int = Query(60, ge=1, le=500),
    session: AsyncSession = Depends(get_session),
) -> list[MarketOut]:
    """Markets for games in progress / about to finish (in-play window)."""
    rows = await svc.live_markets(session, limit=limit)
    return [MarketOut.model_validate(m) for m in rows]


@router.get("/{condition_id}", response_model=MarketOut)
async def get_market(condition_id: str, session: AsyncSession = Depends(get_session)) -> MarketOut:
    market = await svc.get_market(session, condition_id)
    if market is None:
        raise HTTPException(status_code=404, detail="Market not found")
    return MarketOut.model_validate(market)


@router.get("/{condition_id}/price", response_model=list[PriceOut])
async def market_price(
    condition_id: str, session: AsyncSession = Depends(get_session)
) -> list[PriceOut]:
    """Latest price for each outcome token of a market (cache first)."""
    market = await svc.get_market(session, condition_id)
    if market is None:
        raise HTTPException(status_code=404, detail="Market not found")
    prices: list[PriceOut] = []
    for token in market.clob_token_ids:
        data = await svc.get_live_price(str(token))
        if data:
            prices.append(PriceOut(**data))
    return prices
