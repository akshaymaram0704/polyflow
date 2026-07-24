"""Pydantic response models for the public API."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class MarketOut(ORMModel):
    condition_id: str
    question: str
    slug: str | None = None
    category: str | None = None
    outcomes: list = []
    clob_token_ids: list = []
    outcome_prices: list = []
    volume: float
    liquidity: float
    active: bool
    closed: bool
    end_date: datetime | None = None
    updated_at: datetime


class PriceOut(BaseModel):
    token_id: str
    price: float
    ts: float | None = None
    source: str = "cache"


class TraderScoreOut(ORMModel):
    window: str
    profitability: float
    consistency: float
    sizing: float
    risk_adjusted: float
    composite: float
    rank: int | None = None
    computed_at: datetime


class PositionOut(ORMModel):
    condition_id: str | None = None
    asset: str
    outcome: str | None = None
    size: float
    avg_price: float
    cur_price: float
    current_value: float
    cash_pnl: float
    percent_pnl: float


class TraderOut(ORMModel):
    proxy_wallet: str
    username: str | None = None
    total_pnl: float
    total_volume: float
    position_count: int


class TraderDetailOut(TraderOut):
    score: TraderScoreOut | None = None
    positions: list[PositionOut] = []


class LeaderboardEntry(BaseModel):
    rank: int | None = None
    proxy_wallet: str
    username: str | None = None
    composite: float
    profitability: float
    consistency: float
    sizing: float
    risk_adjusted: float
    total_pnl: float


class RecommendationOut(ORMModel):
    id: int
    condition_id: str
    outcome: str
    asset: str | None = None
    direction: str
    confidence: float
    consensus_size_usd: float
    supporter_count: int
    avg_entry_price: float
    current_price: float
    rationale: dict
    status: str
    generated_at: datetime
    question: str | None = None
    recent_trades: int = 0


class HealthOut(BaseModel):
    status: str
    mode: str
    database: bool | None = None
    cache: bool | None = None


class BucketOut(BaseModel):
    label: str
    count: int
    hit_rate: float
    mean_roi: float


class BacktestOut(BaseModel):
    n_recommendations: int
    hit_rate: float
    mean_roi: float
    total_pnl_per_dollar: float
    brier_score: float
    baseline_hit_rate: float
    baseline_mean_roi: float
    edge_vs_crowd: float
    confidence_buckets: list[BucketOut] = []
    params: dict = {}


class OrderOut(ORMModel):
    id: int
    condition_id: str | None = None
    recommendation_id: int | None = None
    asset: str
    outcome: str | None = None
    side: str
    mode: str
    price: float
    size_usd: float
    size_shares: float
    status: str
    external_id: str | None = None
    pnl: float = 0.0
    detail: dict
    created_at: datetime


class ExecuteResponse(BaseModel):
    enabled: bool
    mode: str
    submitted: int
    skipped: int
    orders: list[OrderOut] = []
    note: str | None = None


class PnlSummaryOut(BaseModel):
    mode: str
    orders: int
    open: int
    settled: int
    invested_usd: float
    open_current_value_usd: float
    realized_pnl: float
    unrealized_pnl: float
    total_pnl: float
    roi: float
    settled_win_rate: float | None = None
