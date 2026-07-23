"""Trader discovery + position ingestion.

We discover candidate traders from the *holders* of the highest-volume markets
(robust and key-less), then snapshot each trader's positions + PnL from the Data
API. Positions are stored as a fresh snapshot per sync (old rows for the trader
are replaced), since Polymarket's ``/positions`` already returns current state.
"""

from __future__ import annotations

import asyncio

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.polymarket import get_client
from app.config import settings
from app.db.base import utcnow
from app.db.models import Market, Trade, Trader, TraderPosition
from app.logging import get_logger

log = get_logger(__name__)


async def _discover_wallets(session: AsyncSession, top_markets: int) -> list[str]:
    """Aggregate holders across the top markets, ranked by total holding size."""
    client = get_client()
    result = await session.execute(
        select(Market.condition_id)
        .where(Market.active.is_(True))
        .order_by(Market.volume.desc())
        .limit(top_markets)
    )
    condition_ids = [row[0] for row in result.all()]

    totals: dict[str, float] = {}

    async def _collect(cid: str) -> None:
        for h in await client.get_holders(cid, limit=settings.holders_per_market):
            w = h.get("wallet")
            if w:
                totals[w] = totals.get(w, 0.0) + h.get("amount", 0.0)

    # Bounded concurrency across markets.
    sem = asyncio.Semaphore(settings.http_concurrency)

    async def _guarded(cid: str) -> None:
        async with sem:
            await _collect(cid)

    await asyncio.gather(*(_guarded(cid) for cid in condition_ids))

    # Fallback / augmentation: also seed candidates from wallets seen in recent
    # trades. Robust when /holders is empty or its shape has drifted.
    trade_wallets = await session.execute(
        select(Trade.wallet).where(Trade.wallet.is_not(None)).distinct()
    )
    for (w,) in trade_wallets.all():
        totals.setdefault(w, 0.0)

    ranked = sorted(totals, key=lambda w: totals[w], reverse=True)
    if ranked:
        log.info("_discover_wallets: %d candidate wallets", len(ranked))
    return ranked[: settings.top_traders]


async def sync_traders(session: AsyncSession, top_markets: int = 150) -> int:
    """Discover top traders and snapshot their positions. Returns traders synced."""
    client = get_client()
    wallets = await _discover_wallets(session, top_markets)

    sem = asyncio.Semaphore(settings.http_concurrency)

    async def _fetch(wallet: str) -> tuple[str, list[dict]]:
        async with sem:
            return wallet, await client.get_positions(wallet, limit=200)

    results = await asyncio.gather(*(_fetch(w) for w in wallets))

    now = utcnow()
    synced = 0
    for wallet, positions in results:
        if not positions:
            continue
        trader = await session.get(Trader, wallet)
        if trader is None:
            trader = Trader(proxy_wallet=wallet, first_seen=now)
            session.add(trader)
        trader.last_seen = now
        trader.username = positions[0].get("username") or trader.username
        trader.total_pnl = sum(p.get("cash_pnl", 0.0) for p in positions)
        trader.total_volume = sum(p.get("initial_value", 0.0) for p in positions)
        trader.position_count = len(positions)

        # Replace the position snapshot for this trader.
        await session.execute(delete(TraderPosition).where(TraderPosition.proxy_wallet == wallet))
        for p in positions:
            if not p.get("asset"):
                continue
            session.add(
                TraderPosition(
                    proxy_wallet=wallet,
                    condition_id=p.get("condition_id"),
                    asset=p["asset"],
                    outcome=p.get("outcome"),
                    size=p.get("size", 0.0),
                    avg_price=p.get("avg_price", 0.0),
                    cur_price=p.get("cur_price", 0.0),
                    initial_value=p.get("initial_value", 0.0),
                    current_value=p.get("current_value", 0.0),
                    cash_pnl=p.get("cash_pnl", 0.0),
                    percent_pnl=p.get("percent_pnl", 0.0),
                    realized_pnl=p.get("realized_pnl", 0.0),
                    redeemable=p.get("redeemable", False),
                    fetched_at=now,
                )
            )
        synced += 1
        # Commit in batches to keep transactions bounded.
        if synced % 100 == 0:
            await session.commit()

    await session.commit()
    log.info("sync_traders: synced %d traders (from %d candidates)", synced, len(wallets))
    return synced
