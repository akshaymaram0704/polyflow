"""Cache + pub/sub abstraction.

Provides a single ``Cache`` interface with two backends:

* ``RedisCache``  — backed by redis.asyncio (used in docker / production).
* ``MemoryCache`` — in-process dict + asyncio fan-out, used when Redis is
  unavailable (offline local runs and tests). Pub/sub only spans the current
  process, which is enough for a single-process ``uvicorn`` demo.

The factory ``get_cache()`` tries Redis first and transparently falls back to
memory, so the rest of the app never has to care which backend is live.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

from app.config import settings
from app.logging import get_logger

log = get_logger(__name__)


class Cache:
    """Abstract async cache + pub/sub interface."""

    async def get(self, key: str) -> Any | None: ...
    async def set(self, key: str, value: Any, ttl: int | None = None) -> None: ...
    async def get_many(self, keys: list[str]) -> dict[str, Any]: ...
    async def publish(self, channel: str, message: Any) -> None: ...
    async def subscribe(self, channel: str) -> AsyncIterator[Any]: ...
    async def ping(self) -> bool: ...
    async def close(self) -> None: ...


class MemoryCache(Cache):
    """In-process cache with a simple asyncio pub/sub broker."""

    def __init__(self) -> None:
        self._store: dict[str, Any] = {}
        self._subscribers: dict[str, set[asyncio.Queue]] = {}

    async def get(self, key: str) -> Any | None:
        return self._store.get(key)

    async def set(self, key: str, value: Any, ttl: int | None = None) -> None:
        # TTL is a no-op for the in-memory backend (short-lived demo process).
        self._store[key] = value

    async def get_many(self, keys: list[str]) -> dict[str, Any]:
        return {k: self._store[k] for k in keys if k in self._store}

    async def publish(self, channel: str, message: Any) -> None:
        for queue in list(self._subscribers.get(channel, ())):
            queue.put_nowait(message)

    async def subscribe(self, channel: str) -> AsyncIterator[Any]:
        queue: asyncio.Queue = asyncio.Queue(maxsize=1000)
        self._subscribers.setdefault(channel, set()).add(queue)
        try:
            while True:
                yield await queue.get()
        finally:
            self._subscribers.get(channel, set()).discard(queue)

    async def ping(self) -> bool:
        return True

    async def close(self) -> None:
        self._store.clear()
        self._subscribers.clear()


class RedisCache(Cache):
    """Redis-backed cache. Values are JSON-encoded."""

    def __init__(self, client: Any) -> None:
        self._redis = client

    async def get(self, key: str) -> Any | None:
        raw = await self._redis.get(key)
        return json.loads(raw) if raw is not None else None

    async def set(self, key: str, value: Any, ttl: int | None = None) -> None:
        await self._redis.set(key, json.dumps(value), ex=ttl)

    async def get_many(self, keys: list[str]) -> dict[str, Any]:
        if not keys:
            return {}
        values = await self._redis.mget(keys)
        return {k: json.loads(v) for k, v in zip(keys, values, strict=True) if v is not None}

    async def publish(self, channel: str, message: Any) -> None:
        await self._redis.publish(channel, json.dumps(message))

    async def subscribe(self, channel: str) -> AsyncIterator[Any]:
        pubsub = self._redis.pubsub()
        await pubsub.subscribe(channel)
        try:
            async for msg in pubsub.listen():
                if msg.get("type") == "message":
                    yield json.loads(msg["data"])
        finally:
            await pubsub.unsubscribe(channel)
            await pubsub.aclose()

    async def ping(self) -> bool:
        try:
            return bool(await self._redis.ping())
        except Exception:
            return False

    async def close(self) -> None:
        await self._redis.aclose()


# Channel / key naming helpers.
PRICE_CHANNEL = "polyflow:prices"


def price_key(token_id: str) -> str:
    return f"price:{token_id}"


_cache: Cache | None = None


async def get_cache() -> Cache:
    """Return the process-wide cache, connecting to Redis if possible."""
    global _cache
    if _cache is not None:
        return _cache

    try:
        import redis.asyncio as aioredis

        client = aioredis.from_url(settings.redis_url, decode_responses=True)
        cache: Cache = RedisCache(client)
        if await cache.ping():
            log.info("Connected to Redis at %s", settings.redis_url)
            _cache = cache
            return _cache
        raise ConnectionError("ping failed")
    except Exception as exc:  # noqa: BLE001 - any failure -> graceful fallback
        log.warning("Redis unavailable (%s); using in-memory cache", exc)
        _cache = MemoryCache()
        return _cache


async def close_cache() -> None:
    global _cache
    if _cache is not None:
        await _cache.close()
        _cache = None
