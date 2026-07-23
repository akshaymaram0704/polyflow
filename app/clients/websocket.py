"""Real-time price streamer.

Live mode connects to Polymarket's public CLOB **market** WebSocket
(``wss://ws-subscriptions-clob.polymarket.com/ws/market``), subscribes to a set
of outcome token ids, and on each ``price_change`` / ``book`` message writes the
latest price to the cache (``price:{token_id}``) and publishes it on the
``polyflow:prices`` channel for the API's ``/ws/prices`` fan-out.

Fixtures mode has no live socket, so it simulates a seeded random-walk tick loop
over the fixture universe — same cache/publish side effects, so the streaming API
behaves identically offline.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import random
import time

from app.cache.redis import PRICE_CHANNEL, Cache, get_cache, price_key
from app.config import Settings, settings
from app.logging import get_logger

log = get_logger(__name__)


class PriceStreamer:
    def __init__(self, cfg: Settings | None = None) -> None:
        self.cfg = cfg or settings
        self._assets: list[str] = []
        self._cache: Cache | None = None
        self._lock = asyncio.Lock()

    async def set_assets(self, token_ids: list[str]) -> None:
        async with self._lock:
            self._assets = [str(t) for t in token_ids if t]

    async def _emit(self, token_id: str, price: float) -> None:
        cache = self._cache or await get_cache()
        payload = {"token_id": token_id, "price": round(float(price), 4), "ts": time.time()}
        await cache.set(price_key(token_id), payload, ttl=300)
        await cache.publish(PRICE_CHANNEL, payload)

    async def run(self, stop: asyncio.Event) -> None:
        self._cache = await get_cache()
        if self.cfg.use_fixtures:
            await self._run_fixtures(stop)
        else:
            await self._run_live(stop)

    # ------------------------------------------------------------------ #
    async def _run_fixtures(self, stop: asyncio.Event) -> None:
        from app.clients.fixtures import _universe

        prices = dict(_universe().token_to_price)
        if not self._assets:
            self._assets = list(prices)[:200]
        rng = random.Random(7)
        log.info("PriceStreamer: fixtures tick loop over %d tokens", len(self._assets))
        while not stop.is_set():
            for tok in self._assets:
                base = prices.get(tok, 0.5)
                drift = rng.gauss(0, 0.01)
                base = min(0.99, max(0.01, base + drift))
                prices[tok] = base
                await self._emit(tok, base)
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=1.0)

    # ------------------------------------------------------------------ #
    async def _run_live(self, stop: asyncio.Event) -> None:
        import websockets

        while not stop.is_set():
            try:
                async with websockets.connect(self.cfg.ws_url, ping_interval=20) as ws:
                    await ws.send(json.dumps({"assets_ids": self._assets, "type": "market"}))
                    log.info("PriceStreamer: subscribed to %d assets (live)", len(self._assets))
                    async for raw in ws:
                        if stop.is_set():
                            break
                        await self._handle_message(raw)
            except Exception as exc:  # noqa: BLE001 - reconnect on any drop
                if stop.is_set():
                    break
                log.warning("WS disconnected (%s); reconnecting in 3s", exc)
                with contextlib.suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(stop.wait(), timeout=3.0)

    async def _handle_message(self, raw: str | bytes) -> None:
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return
        events = data if isinstance(data, list) else [data]
        for ev in events:
            if not isinstance(ev, dict):
                continue
            token = ev.get("asset_id") or ev.get("asset")
            price = ev.get("price")
            # `book` messages carry bids/asks — derive a midpoint.
            if price is None and (ev.get("bids") or ev.get("asks")):
                price = self._midpoint(ev)
            if token and price is not None:
                with contextlib.suppress(ValueError, TypeError):
                    await self._emit(str(token), float(price))

    @staticmethod
    def _midpoint(ev: dict) -> float | None:
        def best(levels, key):
            vals = [float(lvl["price"]) for lvl in levels if "price" in lvl]
            return key(vals) if vals else None

        bid = best(ev.get("bids", []), max)
        ask = best(ev.get("asks", []), min)
        if bid is not None and ask is not None:
            return (bid + ask) / 2
        return bid or ask
