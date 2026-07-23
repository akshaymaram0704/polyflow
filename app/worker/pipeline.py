"""Pipeline job functions shared by the scheduler, the CLI and tests.

Each job opens its own session so it can run standalone or on a timer. The
functions are deliberately small orchestrators over the ingestion + scoring
modules so they can be composed (``run_full_cycle``) or triggered individually.
"""

from __future__ import annotations

from app.db.session import SessionLocal
from app.ingestion.markets import sync_markets
from app.ingestion.traders import sync_traders
from app.ingestion.trades import sync_trades
from app.logging import get_logger
from app.scoring.ranking import run_scoring

log = get_logger(__name__)

# The most recently tracked outcome-token ids (refreshed on each market sync).
_tracked_tokens: list[str] = []


def tracked_tokens() -> list[str]:
    return list(_tracked_tokens)


async def job_sync_markets() -> list[str]:
    global _tracked_tokens
    async with SessionLocal() as session:
        _tracked_tokens = await sync_markets(session)
    return _tracked_tokens


async def job_sync_trades() -> int:
    async with SessionLocal() as session:
        return await sync_trades(session)


async def job_sync_traders() -> int:
    async with SessionLocal() as session:
        return await sync_traders(session)


async def job_scoring() -> dict[str, int]:
    async with SessionLocal() as session:
        return await run_scoring(session)


async def run_full_cycle() -> dict[str, object]:
    """Run the entire pipeline once, in dependency order."""
    tokens = await job_sync_markets()
    trades = await job_sync_trades()
    traders = await job_sync_traders()
    scoring = await job_scoring()
    result = {
        "markets_tokens": len(tokens),
        "trades_inserted": trades,
        "traders_synced": traders,
        **scoring,
    }
    log.info("run_full_cycle complete: %s", result)
    return result
