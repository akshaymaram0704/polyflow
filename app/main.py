"""FastAPI application factory + lifespan wiring."""

from __future__ import annotations

import asyncio
import contextlib
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.api.routes import (
    backtest,
    health,
    markets,
    recommendations,
    stream,
    traders,
    trading,
)
from app.cache.redis import close_cache, get_cache
from app.clients.polymarket import close_client
from app.config import settings
from app.db.session import init_db
from app.logging import configure_logging, get_logger

log = get_logger(__name__)

STATIC_DIR = Path(__file__).parent / "web" / "static"

DESCRIPTION = """
**PolyFlow** turns raw Polymarket data into ranked trading intelligence.

* Ingests markets, trades and trader positions across 500+ prediction markets.
* Ranks traders on profitability, consistency, sizing and risk-adjusted return.
* Surfaces high-confidence, consensus-driven trade recommendations.
* Streams sub-second price updates over WebSocket (`/ws/prices`).
"""


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    log.info("Starting PolyFlow API (mode=%s)", "fixtures" if settings.use_fixtures else "live")
    # On SQLite / offline runs, create tables directly. Postgres uses Alembic.
    if settings.is_sqlite:
        await init_db()
    await get_cache()  # warm the cache connection (or memory fallback)

    stop = asyncio.Event()
    bg_tasks: list[asyncio.Task] = []
    if settings.run_worker_in_api:
        bg_tasks = await _start_in_process_worker(stop)

    yield

    stop.set()
    for task in bg_tasks:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
    await close_cache()
    await close_client()


async def _start_in_process_worker(stop: asyncio.Event) -> list[asyncio.Task]:
    """Run the pipeline + price streamer inside the API (offline demo mode)."""
    from app.clients.websocket import PriceStreamer
    from app.worker import pipeline
    from app.worker.scheduler import build_scheduler

    log.info("run_worker_in_api=true -> seeding pipeline in-process")
    await pipeline.run_full_cycle()

    streamer = PriceStreamer()
    await streamer.set_assets(pipeline.tracked_tokens())
    streamer_task = asyncio.create_task(streamer.run(stop))

    scheduler = build_scheduler()
    scheduler.start()
    log.info("In-process worker active (scheduler + price streamer)")
    return [streamer_task]


def create_app() -> FastAPI:
    app = FastAPI(
        title="PolyFlow",
        version="0.1.0",
        description=DESCRIPTION,
        lifespan=lifespan,
    )
    app.include_router(health.router)
    app.include_router(markets.router)
    app.include_router(traders.router)
    app.include_router(recommendations.router)
    app.include_router(backtest.router)
    app.include_router(trading.router)
    app.include_router(stream.router)

    # Dashboard (static single-page UI).
    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

        @app.get("/dashboard", include_in_schema=False)
        async def dashboard() -> FileResponse:
            return FileResponse(STATIC_DIR / "index.html")

    @app.get("/", include_in_schema=False)
    async def root() -> RedirectResponse:
        return RedirectResponse(url="/dashboard")

    @app.get("/info", tags=["health"])
    async def info() -> dict:
        return {
            "name": "PolyFlow",
            "version": "0.1.0",
            "mode": "fixtures" if settings.use_fixtures else "live",
            "docs": "/docs",
            "dashboard": "/dashboard",
        }

    return app


app = create_app()
