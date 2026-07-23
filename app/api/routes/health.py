"""Liveness / readiness endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.cache.redis import get_cache
from app.config import settings
from app.db.session import get_session
from app.schemas.models import HealthOut

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthOut)
async def health() -> HealthOut:
    """Cheap liveness check."""
    return HealthOut(status="ok", mode="fixtures" if settings.use_fixtures else "live")


@router.get("/health/ready", response_model=HealthOut)
async def ready(session: AsyncSession = Depends(get_session)) -> HealthOut:
    """Readiness check: verifies DB and cache connectivity."""
    db_ok = True
    try:
        await session.execute(text("SELECT 1"))
    except Exception:
        db_ok = False

    cache_ok = await (await get_cache()).ping()
    status = "ok" if (db_ok and cache_ok) else "degraded"
    return HealthOut(
        status=status,
        mode="fixtures" if settings.use_fixtures else "live",
        database=db_ok,
        cache=cache_ok,
    )
