"""Worker entrypoint: runs the pipeline scheduler + live price streamer.

Usage:
    python -m app.worker.run            # run scheduler + streamer forever
    python -m app.worker.run --once     # run one full pipeline cycle and exit
"""

from __future__ import annotations

import asyncio
import signal
import sys

from app.clients.websocket import PriceStreamer
from app.config import settings
from app.db.session import init_db
from app.logging import configure_logging, get_logger
from app.worker import pipeline
from app.worker.scheduler import build_scheduler

log = get_logger(__name__)


async def _run_once() -> None:
    if settings.is_sqlite:
        await init_db()
    result = await pipeline.run_full_cycle()
    log.info("Single cycle finished: %s", result)


async def _run_forever() -> None:
    if settings.is_sqlite:
        await init_db()

    stop = asyncio.Event()

    # Seed data + tracked tokens before starting timers/stream.
    await pipeline.run_full_cycle()

    streamer = PriceStreamer()
    await streamer.set_assets(pipeline.tracked_tokens())
    streamer_task = asyncio.create_task(streamer.run(stop))

    scheduler = build_scheduler()
    # Refresh the streamer's asset set whenever markets are re-synced.
    scheduler.add_job(
        lambda: asyncio.create_task(streamer.set_assets(pipeline.tracked_tokens())),
        "interval",
        minutes=settings.sync_markets_minutes,
        id="refresh_stream_assets",
    )
    scheduler.start()
    log.info("Worker running: scheduler + price streamer active")

    # Graceful shutdown on SIGINT/SIGTERM (POSIX); on Windows rely on KeyboardInterrupt.
    loop = asyncio.get_running_loop()
    for sig in (getattr(signal, "SIGINT", None), getattr(signal, "SIGTERM", None)):
        if sig is None:
            continue
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:  # Windows
            pass

    try:
        await stop.wait()
    finally:
        log.info("Shutting down worker...")
        scheduler.shutdown(wait=False)
        stop.set()
        streamer_task.cancel()
        try:
            await streamer_task
        except asyncio.CancelledError:
            pass


def main() -> None:
    configure_logging()
    once = "--once" in sys.argv
    try:
        asyncio.run(_run_once() if once else _run_forever())
    except KeyboardInterrupt:
        log.info("Interrupted; exiting.")


if __name__ == "__main__":
    main()
