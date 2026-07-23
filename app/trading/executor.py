"""Trade execution orchestrator.

Safety model (defense in depth):
  * Nothing runs unless ``POLYFLOW_TRADING_ENABLED=true``.
  * Live orders require mode=live AND a configured private key; otherwise we
    transparently fall back to **paper** (record intent, send nothing).
  * Risk limits (per-order, per-day, max open positions) are enforced by the
    pure ``plan_orders`` logic before anything is recorded or sent.

Paper mode persists ``Order`` rows with ``mode='paper'`` so the strategy can be
tracked forward without capital. Live mode additionally submits to the CLOB.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.base import utcnow
from app.db.models import Order, Recommendation
from app.logging import get_logger
from app.trading.risk import RiskParams, plan_orders

log = get_logger(__name__)


def _effective_mode() -> tuple[str | None, str | None]:
    """Return (mode, note). mode is None when trading is disabled."""
    if not settings.trading_enabled:
        return None, "trading disabled (POLYFLOW_TRADING_ENABLED=false)"
    if settings.trading_mode.lower() == "live":
        if settings.polygon_private_key:
            return "live", None
        return "paper", "live requested but no private key set; running paper"
    return "paper", None


async def _current_exposure(session: AsyncSession, mode: str) -> tuple[float, int, set[str]]:
    start_of_day = utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    active_states = ("submitted", "filled")

    spent = (
        await session.execute(
            select(func.coalesce(func.sum(Order.size_usd), 0.0)).where(
                Order.mode == mode,
                Order.status.in_(active_states),
                Order.created_at >= start_of_day,
            )
        )
    ).scalar_one()

    open_rows = (
        await session.execute(
            select(Order.asset).where(Order.mode == mode, Order.status.in_(active_states))
        )
    ).scalars()
    held = set(open_rows)
    return float(spent), len(held), held


async def execute_recommendations(session: AsyncSession) -> dict:
    mode, note = _effective_mode()
    if mode is None:
        return {
            "enabled": False,
            "mode": "off",
            "submitted": 0,
            "skipped": 0,
            "orders": [],
            "note": note,
        }

    # Candidate recommendations, strongest first.
    recs = list(
        (
            await session.execute(
                select(Recommendation)
                .where(Recommendation.status == "active", Recommendation.direction == "BUY")
                .order_by(Recommendation.confidence.desc())
            )
        ).scalars()
    )
    candidates = [
        {
            "id": r.id,
            "condition_id": r.condition_id,
            "asset": r.asset,
            "outcome": r.outcome,
            "direction": r.direction,
            "confidence": r.confidence,
            "price": r.current_price or r.avg_entry_price,
        }
        for r in recs
    ]

    spent_today, open_count, held = await _current_exposure(session, mode)
    params = RiskParams(
        min_confidence=settings.trading_min_confidence,
        max_order_usd=settings.trading_max_order_usd,
        max_daily_usd=settings.trading_max_daily_usd,
        max_open_positions=settings.trading_max_open_positions,
    )
    plans = plan_orders(
        candidates, params, spent_today=spent_today, open_positions=open_count, held_assets=held
    )

    live_client = None
    submitted: list[Order] = []
    skipped = 0
    for p in plans:
        if p.action != "submit":
            skipped += 1
            continue

        price = p.price
        size_shares = round(p.size_usd / price, 2) if price else 0.0
        status, external_id, detail = "submitted", None, {"reason": p.reason, "confidence": None}

        if mode == "live":  # pragma: no cover - exercised only with real creds
            try:
                if live_client is None:
                    from app.trading.client import LiveTradingClient

                    live_client = LiveTradingClient()
                external_id, status = await live_client.place_order(
                    p.asset, price, size_shares, side="BUY"
                )
            except Exception as exc:  # noqa: BLE001 - never crash the batch
                status = "rejected"
                detail["error"] = str(exc)
                log.error("Live order rejected for %s: %s", p.asset, exc)

        order = Order(
            condition_id=p.condition_id,
            recommendation_id=p.recommendation_id,
            asset=p.asset,
            outcome=p.outcome,
            side="BUY",
            mode=mode,
            price=round(price, 4),
            size_usd=p.size_usd,
            size_shares=size_shares,
            status=status,
            external_id=external_id,
            detail=detail,
        )
        session.add(order)
        submitted.append(order)

    await session.commit()
    log.info(
        "execute_recommendations: mode=%s submitted=%d skipped=%d", mode, len(submitted), skipped
    )
    return {
        "enabled": True,
        "mode": mode,
        "submitted": len(submitted),
        "skipped": skipped,
        "orders": submitted,
        "note": note,
    }
