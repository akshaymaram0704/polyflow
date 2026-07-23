"""Real-time price streaming over WebSocket.

Clients connect to ``/ws/prices`` and receive live price ticks published by the
worker's ``PriceStreamer`` on the ``polyflow:prices`` cache channel. A client may
send ``{"assets": ["<token_id>", ...]}`` at any time to filter the stream to
specific outcome tokens; sending an empty list (or nothing) streams everything.
"""

from __future__ import annotations

import asyncio
import contextlib

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.cache.redis import PRICE_CHANNEL, get_cache
from app.logging import get_logger

router = APIRouter(tags=["stream"])
log = get_logger(__name__)


@router.websocket("/ws/prices")
async def ws_prices(ws: WebSocket) -> None:
    await ws.accept()
    cache = await get_cache()
    filter_assets: set[str] | None = None

    async def _receive_filters() -> None:
        nonlocal filter_assets
        try:
            while True:
                msg = await ws.receive_json()
                assets = msg.get("assets") if isinstance(msg, dict) else None
                filter_assets = {str(a) for a in assets} if assets else None
        except (WebSocketDisconnect, RuntimeError):
            pass

    receiver = asyncio.create_task(_receive_filters())
    await ws.send_json({"type": "connected", "channel": "prices"})
    try:
        async for tick in cache.subscribe(PRICE_CHANNEL):
            if receiver.done():
                break
            if filter_assets and str(tick.get("token_id")) not in filter_assets:
                continue
            await ws.send_json({"type": "price", **tick})
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        receiver.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await receiver
