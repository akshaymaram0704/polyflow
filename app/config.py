"""Application configuration, loaded from environment / `.env`."""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Central settings object. Every field maps to a `POLYFLOW_`-prefixed env var."""

    model_config = SettingsConfigDict(
        env_prefix="POLYFLOW_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Core
    env: str = "development"
    log_level: str = "INFO"

    # Offline mode: serve bundled fixtures instead of hitting the live API.
    use_fixtures: bool = True

    # When true, the API process also runs the pipeline + price streamer in-process
    # (handy for single-process offline demos where Redis pub/sub isn't bridging
    # separate api/worker containers). In docker, keep this false and run the worker.
    run_worker_in_api: bool = False

    # Infra
    database_url: str = "sqlite+aiosqlite:///./polyflow.db"
    redis_url: str = "redis://localhost:6379/0"

    # Polymarket endpoints
    gamma_url: str = "https://gamma-api.polymarket.com"
    data_api_url: str = "https://data-api.polymarket.com"
    clob_url: str = "https://clob.polymarket.com"
    ws_url: str = "wss://ws-subscriptions-clob.polymarket.com/ws/market"

    # Category focus: "" = all categories, "sports" = sports markets only.
    category: str = ""

    # Pipeline tuning
    market_limit: int = 500
    top_traders: int = 1000
    holders_per_market: int = 50
    http_concurrency: int = 8
    sync_markets_minutes: int = 5
    sync_trades_minutes: int = 2
    sync_traders_minutes: int = 15
    scoring_minutes: int = 15

    # Ranking weights
    w_profitability: float = 0.35
    w_consistency: float = 0.25
    w_sizing: float = 0.15
    w_risk_adjusted: float = 0.25

    # Recommendations
    min_confidence: float = 0.52
    top_traders_for_recs: int = 300
    # Risk band on the recommended outcome's price. Default targets higher-upside
    # positions (no near-certain favorites). Set both to 0/1 to disable.
    rec_min_price: float = 0.12
    rec_max_price: float = 0.65

    # --- Trading (analytics stays read-only; execution is strictly opt-in) ---
    # Master kill-switch: no order is ever placed or recorded unless this is true.
    trading_enabled: bool = False
    # "paper" records intended orders without sending; "live" sends real orders.
    trading_mode: str = "paper"
    # Only act on recommendations at/above this confidence.
    trading_min_confidence: float = 0.75
    # Risk limits (USDC).
    trading_max_order_usd: float = 50.0
    trading_max_daily_usd: float = 500.0
    trading_max_open_positions: int = 20
    # Live-only credentials (leave blank for paper mode). NEVER commit these.
    polygon_private_key: str = ""
    clob_api_key: str = ""
    clob_api_secret: str = ""
    clob_api_passphrase: str = ""

    # --- Backtesting ---
    backtest_markets: int = 300  # resolved markets in the offline scenario

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")

    @property
    def ranking_weights(self) -> dict[str, float]:
        return {
            "profitability": self.w_profitability,
            "consistency": self.w_consistency,
            "sizing": self.w_sizing,
            "risk_adjusted": self.w_risk_adjusted,
        }

    @property
    def is_live_trading(self) -> bool:
        """True only when execution is enabled AND explicitly in live mode."""
        return self.trading_enabled and self.trading_mode.lower() == "live"


@lru_cache
def get_settings() -> Settings:
    """Cached settings singleton."""
    return Settings()


settings = get_settings()
