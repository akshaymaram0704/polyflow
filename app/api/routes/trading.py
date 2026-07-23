"""Trading endpoints (status, orders, execute).

Execution is opt-in and gated by configuration; by default trading is disabled
and, when enabled, runs in paper mode (records intent without sending orders).
No secrets are ever returned by these endpoints.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.models import Order
from app.db.session import get_session
from app.schemas.models import ExecuteResponse, OrderOut, PnlSummaryOut
from app.trading.executor import execute_recommendations
from app.trading.pnl import pnl_summary, settle_orders

router = APIRouter(prefix="/trading", tags=["trading"])


@router.get("/status")
async def status() -> dict:
    """Current trading configuration (no secrets)."""
    return {
        "enabled": settings.trading_enabled,
        "mode": settings.trading_mode,
        "has_credentials": bool(settings.polygon_private_key),
        "limits": {
            "min_confidence": settings.trading_min_confidence,
            "max_order_usd": settings.trading_max_order_usd,
            "max_daily_usd": settings.trading_max_daily_usd,
            "max_open_positions": settings.trading_max_open_positions,
        },
    }


@router.get("/orders", response_model=list[OrderOut])
async def orders(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    mode: str | None = Query(None, pattern="^(paper|live)$"),
    order_status: str | None = None,
    session: AsyncSession = Depends(get_session),
) -> list[OrderOut]:
    stmt = select(Order)
    if mode:
        stmt = stmt.where(Order.mode == mode)
    if order_status:
        stmt = stmt.where(Order.status == order_status)
    stmt = stmt.order_by(Order.created_at.desc()).limit(limit).offset(offset)
    rows = (await session.execute(stmt)).scalars()
    return [OrderOut.model_validate(o) for o in rows]


@router.post("/execute", response_model=ExecuteResponse)
async def execute(session: AsyncSession = Depends(get_session)) -> ExecuteResponse:
    """Evaluate active recommendations and place orders (paper unless configured live).

    Returns immediately with no orders if trading is disabled.
    """
    result = await execute_recommendations(session)
    return ExecuteResponse(
        enabled=result["enabled"],
        mode=result["mode"],
        submitted=result["submitted"],
        skipped=result["skipped"],
        orders=[OrderOut.model_validate(o) for o in result["orders"]],
        note=result.get("note"),
    )


@router.get("/pnl", response_model=PnlSummaryOut)
async def pnl(
    mode: str = Query("paper", pattern="^(paper|live)$"),
    session: AsyncSession = Depends(get_session),
) -> PnlSummaryOut:
    """Portfolio PnL: realized (settled) + unrealized (mark-to-market)."""
    return PnlSummaryOut(**await pnl_summary(session, mode))


@router.post("/settle")
async def settle(
    mode: str | None = Query(None, pattern="^(paper|live)$"),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Grade open orders against any markets that have since resolved."""
    return await settle_orders(session, mode)
