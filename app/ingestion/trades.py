"""Trade ingestion: pull recent fills for the highest-volume active markets."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.polymarket import get_client
from app.db.models import Market, Trade
from app.logging import get_logger

log = get_logger(__name__)


async def sync_trades(
    session: AsyncSession, markets_limit: int = 100, per_market: int = 100
) -> int:
    """Ingest recent trades for the top markets by volume. Returns rows inserted."""
    client = get_client()
    result = await session.execute(
        select(Market.condition_id)
        .where(Market.active.is_(True))
        .order_by(Market.volume.desc())
        .limit(markets_limit)
    )
    condition_ids = [row[0] for row in result.all()]

    inserted = 0
    for cid in condition_ids:
        trades = await client.get_trades(condition_id=cid, limit=per_market)
        if not trades:
            continue
        # Dedupe against what we already stored for this market.
        existing = await session.execute(
            select(Trade.tx_hash, Trade.wallet, Trade.asset).where(Trade.condition_id == cid)
        )
        seen = {(r[0], r[1], r[2]) for r in existing.all()}

        for t in trades:
            if not t.get("wallet"):
                continue
            key = (t.get("tx_hash"), t["wallet"], t.get("asset"))
            if key in seen:
                continue
            seen.add(key)
            session.add(
                Trade(
                    condition_id=cid,
                    tx_hash=t.get("tx_hash"),
                    wallet=t["wallet"],
                    asset=t.get("asset"),
                    outcome=t.get("outcome"),
                    side=t.get("side"),
                    price=t.get("price", 0.0),
                    size=t.get("size", 0.0),
                    usd_value=t.get("usd_value", 0.0),
                    timestamp=t.get("timestamp"),
                )
            )
            inserted += 1
        await session.commit()

    log.info("sync_trades: inserted %d new trades across %d markets", inserted, len(condition_ids))
    return inserted
