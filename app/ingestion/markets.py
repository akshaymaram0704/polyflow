"""Market ingestion: pull the active market catalog from Gamma and upsert it."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.polymarket import get_client
from app.config import settings
from app.db.models import Market
from app.logging import get_logger

log = get_logger(__name__)


async def sync_markets(session: AsyncSession, limit: int | None = None) -> list[str]:
    """Fetch active markets and upsert them. Returns the tracked CLOB token ids."""
    client = get_client()
    limit = limit or settings.market_limit
    markets = await client.get_markets(limit=limit, active=True)

    token_ids: list[str] = []
    for m in markets:
        if not m.get("condition_id"):
            continue
        await session.merge(
            Market(
                condition_id=m["condition_id"],
                question=m["question"][:512],
                slug=m.get("slug"),
                category=m.get("category"),
                outcomes=m.get("outcomes", []),
                clob_token_ids=m.get("clob_token_ids", []),
                outcome_prices=m.get("outcome_prices", []),
                volume=m.get("volume", 0.0),
                liquidity=m.get("liquidity", 0.0),
                active=m.get("active", True),
                closed=m.get("closed", False),
                start_date=m.get("start_date"),
                end_date=m.get("end_date"),
            )
        )
        token_ids.extend(str(t) for t in m.get("clob_token_ids", []) if t)

    await session.commit()
    log.info("sync_markets: upserted %d markets (%d tokens)", len(markets), len(token_ids))
    return token_ids
