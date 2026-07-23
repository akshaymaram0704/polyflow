"""Live order client — thin wrapper over ``py-clob-client``.

Only used when trading is enabled AND mode is ``live`` AND credentials are set.
``py-clob-client`` is an optional dependency (``pip install '.[trading]'``); we
import it lazily so the rest of PolyFlow never depends on it. All calls run the
synchronous SDK in a threadpool.
"""

from __future__ import annotations

import asyncio

from app.config import Settings, settings
from app.logging import get_logger

log = get_logger(__name__)

POLYGON_CHAIN_ID = 137


class TradingUnavailable(RuntimeError):
    """Raised when live trading is requested but cannot be performed safely."""


class LiveTradingClient:
    def __init__(self, cfg: Settings | None = None) -> None:
        self.cfg = cfg or settings
        self._client = None

    def _ensure_client(self):
        if self._client is not None:
            return self._client
        if not self.cfg.polygon_private_key:
            raise TradingUnavailable("POLYFLOW_POLYGON_PRIVATE_KEY is not set")
        try:
            from py_clob_client.client import ClobClient
        except ImportError as exc:  # pragma: no cover - optional dep
            raise TradingUnavailable(
                "py-clob-client not installed; run: pip install '.[trading]'"
            ) from exc

        client = ClobClient(
            host=self.cfg.clob_url,
            key=self.cfg.polygon_private_key,
            chain_id=POLYGON_CHAIN_ID,
        )
        # Prefer explicit L2 API creds if provided; otherwise derive them.
        if self.cfg.clob_api_key:
            from py_clob_client.clob_types import ApiCreds

            client.set_api_creds(
                ApiCreds(
                    api_key=self.cfg.clob_api_key,
                    api_secret=self.cfg.clob_api_secret,
                    api_passphrase=self.cfg.clob_api_passphrase,
                )
            )
        else:  # pragma: no cover - network dependent
            client.set_api_creds(client.create_or_derive_api_creds())
        self._client = client
        return client

    async def place_order(
        self, asset: str, price: float, size_shares: float, side: str = "BUY"
    ) -> tuple[str | None, str]:
        """Place a limit order. Returns (external_id, status)."""

        def _submit() -> tuple[str | None, str]:  # pragma: no cover - live only
            client = self._ensure_client()
            from py_clob_client.clob_types import OrderArgs
            from py_clob_client.order_builder.constants import BUY, SELL

            order = client.create_order(
                OrderArgs(
                    token_id=asset,
                    price=round(price, 3),
                    size=round(size_shares, 2),
                    side=BUY if side.upper() == "BUY" else SELL,
                )
            )
            resp = client.post_order(order)
            ext_id = resp.get("orderID") or resp.get("orderId") if isinstance(resp, dict) else None
            status = resp.get("status", "submitted") if isinstance(resp, dict) else "submitted"
            return ext_id, status

        return await asyncio.to_thread(_submit)
