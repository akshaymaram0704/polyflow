"""Read services for markets + live prices (cache-aware)."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.cache.redis import get_cache, price_key
from app.clients.polymarket import get_client
from app.db.models import Market


async def list_markets(
    session: AsyncSession,
    *,
    limit: int = 50,
    offset: int = 0,
    category: str | None = None,
    active: bool | None = True,
    sort: str = "volume",
) -> list[Market]:
    stmt = select(Market)
    if category:
        stmt = stmt.where(Market.category == category)
    if active is not None:
        stmt = stmt.where(Market.active.is_(active))
    order_col = {"volume": Market.volume, "liquidity": Market.liquidity}.get(sort, Market.volume)
    stmt = stmt.order_by(order_col.desc()).limit(limit).offset(offset)
    return list((await session.execute(stmt)).scalars())


async def get_market(session: AsyncSession, condition_id: str) -> Market | None:
    return await session.get(Market, condition_id)


async def get_live_price(token_id: str) -> dict | None:
    """Latest price for a token: cache first, then a live spot fallback."""
    cache = await get_cache()
    cached = await cache.get(price_key(token_id))
    if cached:
        return {**cached, "source": "cache"}
    price = await get_client().get_price(token_id)
    if price is None:
        return None
    return {"token_id": token_id, "price": price, "ts": None, "source": "api"}
