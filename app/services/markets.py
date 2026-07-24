"""Read services for markets + live prices (cache-aware)."""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.cache.redis import get_cache, price_key
from app.clients.polymarket import get_client
from app.db.base import utcnow
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


async def live_markets(
    session: AsyncSession, *, limit: int = 400, window_hours: int = 36
) -> list[Market]:
    """Markets for games happening *right now* / imminent — an in-play window.

    A live sports market resolves right after the game ends, so we treat markets
    whose ``end_date`` falls between ~2h ago (just finishing) and a few hours out
    as "in progress / about to finish". Ordered by soonest to resolve.

    Falls back to top active markets when no end-dated markets exist (fixtures),
    so the page/bot still have something to work with.
    """
    from app.logging import get_logger

    log = get_logger(__name__)
    now = utcnow()
    stmt = (
        select(Market)
        .where(
            Market.active.is_(True),
            Market.closed.is_(False),
            Market.end_date.is_not(None),
            Market.end_date >= now - timedelta(hours=3),
            Market.end_date <= now + timedelta(hours=window_hours),
        )
        .order_by(Market.end_date.asc())
        .limit(limit)
    )
    rows = list((await session.execute(stmt)).scalars())
    log.info("live_markets: %d markets in the next %dh in-play window", len(rows), window_hours)
    if not rows:
        # No in-play window hits — surface the soonest-resolving active markets so
        # the page shows the nearest games rather than long-dated futures.
        stmt = (
            select(Market)
            .where(Market.active.is_(True), Market.end_date.is_not(None), Market.end_date >= now)
            .order_by(Market.end_date.asc())
            .limit(limit)
        )
        rows = list((await session.execute(stmt)).scalars())
        if not rows:  # last resort (e.g. fixtures with no dates)
            rows = list(
                (
                    await session.execute(
                        select(Market)
                        .where(
                            Market.active.is_(True),
                            or_(Market.closed.is_(False), Market.closed.is_(None)),
                        )
                        .order_by(Market.volume.desc())
                        .limit(limit)
                    )
                ).scalars()
            )
    return rows


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
