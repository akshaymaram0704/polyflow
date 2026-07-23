"""Polymarket read-only API client.

Wraps the three public, key-less Polymarket data surfaces:

* Gamma      (``gamma-api.polymarket.com``)  — market / event catalog + metadata
* Data API   (``data-api.polymarket.com``)   — trades, positions, holders, value
* CLOB reads (``clob.polymarket.com``)        — spot price, order book, price history

Every method returns **normalized dicts** with stable snake_case keys so the rest
of PolyFlow is insulated from Polymarket's raw field naming (which mixes camelCase
and JSON-encoded string arrays). Parsing is defensive: missing/renamed fields
degrade to sane defaults rather than raising.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from typing import Any

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.config import Settings, settings
from app.logging import get_logger

log = get_logger(__name__)


# --------------------------------------------------------------------------- #
# Parsing helpers
# --------------------------------------------------------------------------- #
def _as_list(value: Any) -> list:
    """Gamma encodes arrays as JSON strings (e.g. '["Yes","No"]'). Normalize."""
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, list) else [parsed]
        except (json.JSONDecodeError, ValueError):
            return [value] if value else []
    return [value]


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_dt(value: Any) -> datetime | None:
    """Parse either a unix timestamp (Data API) or ISO string (Gamma)."""
    if value in (None, "", 0):
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value), tz=UTC)
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            try:
                return datetime.fromtimestamp(float(value), tz=UTC)
            except ValueError:
                return None
    return None


def normalize_market(raw: dict) -> dict:
    return {
        "condition_id": raw.get("conditionId") or raw.get("condition_id") or raw.get("id"),
        "question": raw.get("question") or raw.get("title") or "",
        "slug": raw.get("slug"),
        "category": raw.get("category") or (raw.get("tags") or [None])[0],
        "outcomes": _as_list(raw.get("outcomes")),
        "clob_token_ids": _as_list(raw.get("clobTokenIds") or raw.get("clob_token_ids")),
        "outcome_prices": [_as_float(p) for p in _as_list(raw.get("outcomePrices"))],
        "volume": _as_float(raw.get("volumeNum") or raw.get("volume")),
        "liquidity": _as_float(raw.get("liquidityNum") or raw.get("liquidity")),
        "active": bool(raw.get("active", True)),
        "closed": bool(raw.get("closed", False)),
        "start_date": _as_dt(raw.get("startDate") or raw.get("start_date")),
        "end_date": _as_dt(raw.get("endDate") or raw.get("end_date")),
    }


def normalize_position(raw: dict) -> dict:
    return {
        "proxy_wallet": raw.get("proxyWallet") or raw.get("user") or raw.get("wallet"),
        "username": raw.get("name") or raw.get("pseudonym"),
        "asset": str(raw.get("asset") or raw.get("tokenId") or ""),
        "condition_id": raw.get("conditionId") or raw.get("condition_id"),
        "outcome": raw.get("outcome"),
        "size": _as_float(raw.get("size")),
        "avg_price": _as_float(raw.get("avgPrice")),
        "cur_price": _as_float(raw.get("curPrice")),
        "initial_value": _as_float(raw.get("initialValue")),
        "current_value": _as_float(raw.get("currentValue")),
        "cash_pnl": _as_float(raw.get("cashPnl")),
        "percent_pnl": _as_float(raw.get("percentPnl")),
        "realized_pnl": _as_float(raw.get("realizedPnl")),
        "redeemable": bool(raw.get("redeemable", False)),
    }


def normalize_trade(raw: dict) -> dict:
    return {
        "tx_hash": raw.get("transactionHash") or raw.get("txHash"),
        "wallet": raw.get("proxyWallet") or raw.get("user") or raw.get("wallet"),
        "asset": str(raw.get("asset") or raw.get("tokenId") or ""),
        "condition_id": raw.get("conditionId") or raw.get("condition_id"),
        "outcome": raw.get("outcome"),
        "side": (raw.get("side") or "").upper() or None,
        "price": _as_float(raw.get("price")),
        "size": _as_float(raw.get("size")),
        "usd_value": _as_float(raw.get("usdcSize") or raw.get("size", 0) * raw.get("price", 0)),
        "timestamp": _as_dt(raw.get("timestamp")),
    }


def normalize_holder(raw: dict) -> dict:
    return {
        "wallet": raw.get("proxyWallet") or raw.get("user") or raw.get("wallet"),
        "amount": _as_float(raw.get("amount") or raw.get("shares") or raw.get("balance")),
    }


def _flatten_holders(data: Any) -> list[dict]:
    """Normalize the several shapes the Data API /holders endpoint returns.

    Seen in the wild:
      * ``[{"token": "...", "holders": [ {proxyWallet, amount}, ... ]}, ...]``  (per-token groups)
      * ``{"holders": [ ... ]}``
      * a bare ``[ {proxyWallet, amount}, ... ]``
    Returns a flat list of holder dicts.
    """
    if data is None:
        return []
    if isinstance(data, dict):
        data = data.get("holders", [])
    out: list[dict] = []
    for item in data or []:
        if isinstance(item, dict) and isinstance(item.get("holders"), list):
            out.extend(item["holders"])
        elif isinstance(item, dict):
            out.append(item)
    return out


# --------------------------------------------------------------------------- #
# Client interface + live implementation
# --------------------------------------------------------------------------- #
class BasePolymarketClient:
    """Interface shared by the live and fixture clients."""

    async def get_markets(
        self, limit: int = 500, active: bool = True, min_volume: float = 0.0
    ) -> list[dict]:
        raise NotImplementedError

    async def get_market(self, condition_id: str) -> dict | None:
        raise NotImplementedError

    async def get_trades(
        self, condition_id: str | None = None, user: str | None = None, limit: int = 100
    ) -> list[dict]:
        raise NotImplementedError

    async def get_positions(self, user: str, limit: int = 100) -> list[dict]:
        raise NotImplementedError

    async def get_holders(self, condition_id: str, limit: int = 50) -> list[dict]:
        raise NotImplementedError

    async def get_price(self, token_id: str, side: str = "buy") -> float | None:
        raise NotImplementedError

    async def aclose(self) -> None:  # pragma: no cover - trivial
        pass


class LivePolymarketClient(BasePolymarketClient):
    """Live client hitting the public Polymarket HTTP APIs."""

    def __init__(self, cfg: Settings | None = None) -> None:
        self.cfg = cfg or settings
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(20.0),
            headers={"User-Agent": "PolyFlow/0.1 (+analytics)"},
            limits=httpx.Limits(max_connections=self.cfg.http_concurrency * 2),
        )
        self._sem = asyncio.Semaphore(self.cfg.http_concurrency)

    @retry(
        retry=retry_if_exception_type((httpx.TransportError, httpx.HTTPStatusError)),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=0.5, max=8),
        reraise=True,
    )
    async def _get(self, url: str, params: dict | None = None) -> Any:
        async with self._sem:
            resp = await self._client.get(url, params=params)
            resp.raise_for_status()
            return resp.json()

    async def get_markets(
        self, limit: int = 500, active: bool = True, min_volume: float = 0.0
    ) -> list[dict]:
        """Page through Gamma /markets until `limit` active markets are collected."""
        collected: list[dict] = []
        offset, page = 0, min(limit, 100)
        while len(collected) < limit:
            params = {
                "limit": page,
                "offset": offset,
                "active": str(active).lower(),
                "closed": "false",
                "order": "volumeNum",
                "ascending": "false",
            }
            if min_volume:
                params["volume_num_min"] = min_volume
            batch = await self._get(f"{self.cfg.gamma_url}/markets", params)
            if not batch:
                break
            collected.extend(normalize_market(m) for m in batch)
            offset += page
            if len(batch) < page:
                break
        return collected[:limit]

    async def get_market(self, condition_id: str) -> dict | None:
        params = {"condition_ids": condition_id}
        batch = await self._get(f"{self.cfg.gamma_url}/markets", params)
        return normalize_market(batch[0]) if batch else None

    async def get_trades(
        self, condition_id: str | None = None, user: str | None = None, limit: int = 100
    ) -> list[dict]:
        params: dict[str, Any] = {"limit": limit}
        if condition_id:
            params["market"] = condition_id
        if user:
            params["user"] = user
        data = await self._get(f"{self.cfg.data_api_url}/trades", params)
        return [normalize_trade(t) for t in (data or [])]

    async def get_positions(self, user: str, limit: int = 100) -> list[dict]:
        params = {"user": user, "limit": limit, "sortBy": "CURRENT", "sortDirection": "DESC"}
        data = await self._get(f"{self.cfg.data_api_url}/positions", params)
        return [normalize_position(p) for p in (data or [])]

    async def get_holders(self, condition_id: str, limit: int = 50) -> list[dict]:
        params = {"market": condition_id, "limit": limit}
        data = await self._get(f"{self.cfg.data_api_url}/holders", params)
        return [normalize_holder(h) for h in _flatten_holders(data)]

    async def get_price(self, token_id: str, side: str = "buy") -> float | None:
        params = {"token_id": token_id, "side": side}
        data = await self._get(f"{self.cfg.clob_url}/price", params)
        return _as_float(data.get("price")) if isinstance(data, dict) else None

    async def aclose(self) -> None:
        await self._client.aclose()


_client: BasePolymarketClient | None = None


def get_client() -> BasePolymarketClient:
    """Return the process-wide client, honoring fixtures mode."""
    global _client
    if _client is None:
        if settings.use_fixtures:
            from app.clients.fixtures import FixturePolymarketClient

            _client = FixturePolymarketClient()
            log.info("Polymarket client: FIXTURES mode")
        else:
            _client = LivePolymarketClient()
            log.info("Polymarket client: LIVE mode (%s)", settings.gamma_url)
    return _client


async def close_client() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None
