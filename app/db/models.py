"""ORM models for PolyFlow.

Entity map:
    Market            one Polymarket market (a condition with N outcomes)
    Trade             a single on-chain fill against a market
    Trader            a Polymarket proxy wallet we track
    TraderPosition    a trader's current holding in one market outcome
    TraderScore       computed ranking scores for a trader (per window)
    Recommendation    a high-confidence directional call derived from consensus
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, timestamp_column


class Market(Base):
    __tablename__ = "markets"

    condition_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    question: Mapped[str] = mapped_column(String(512))
    slug: Mapped[str | None] = mapped_column(String(256), index=True)
    category: Mapped[str | None] = mapped_column(String(128), index=True)

    # JSON arrays: ["Yes", "No"] and the corresponding CLOB token ids.
    outcomes: Mapped[list] = mapped_column(default=list)
    clob_token_ids: Mapped[list] = mapped_column(default=list)
    outcome_prices: Mapped[list] = mapped_column(default=list)

    volume: Mapped[float] = mapped_column(Float, default=0.0, index=True)
    liquidity: Mapped[float] = mapped_column(Float, default=0.0)
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    closed: Mapped[bool] = mapped_column(Boolean, default=False)

    start_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    end_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = timestamp_column()

    trades: Mapped[list[Trade]] = relationship(
        back_populates="market", cascade="all, delete-orphan"
    )
    positions: Mapped[list[TraderPosition]] = relationship(
        back_populates="market", cascade="all, delete-orphan"
    )


class Trade(Base):
    __tablename__ = "trades"
    __table_args__ = (
        Index("ix_trades_market_ts", "condition_id", "timestamp"),
        Index("ix_trades_wallet_ts", "wallet", "timestamp"),
        UniqueConstraint("tx_hash", "wallet", "asset", name="uq_trade_dedupe"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    condition_id: Mapped[str] = mapped_column(
        ForeignKey("markets.condition_id", ondelete="CASCADE"), index=True
    )
    tx_hash: Mapped[str | None] = mapped_column(String(80))
    wallet: Mapped[str] = mapped_column(String(64), index=True)
    asset: Mapped[str | None] = mapped_column(String(80))  # CLOB token id
    outcome: Mapped[str | None] = mapped_column(String(64))
    side: Mapped[str | None] = mapped_column(String(8))  # BUY / SELL
    price: Mapped[float] = mapped_column(Float, default=0.0)
    size: Mapped[float] = mapped_column(Float, default=0.0)
    usd_value: Mapped[float] = mapped_column(Float, default=0.0)
    timestamp: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    market: Mapped[Market] = relationship(back_populates="trades")


class Trader(Base):
    __tablename__ = "traders"

    proxy_wallet: Mapped[str] = mapped_column(String(64), primary_key=True)
    username: Mapped[str | None] = mapped_column(String(128))
    first_seen: Mapped[datetime] = timestamp_column()
    last_seen: Mapped[datetime] = timestamp_column(onupdate=None)

    # Denormalized aggregates refreshed by the scoring job (for fast reads).
    total_pnl: Mapped[float] = mapped_column(Float, default=0.0)
    total_volume: Mapped[float] = mapped_column(Float, default=0.0)
    position_count: Mapped[int] = mapped_column(Integer, default=0)

    positions: Mapped[list[TraderPosition]] = relationship(
        back_populates="trader", cascade="all, delete-orphan"
    )
    scores: Mapped[list[TraderScore]] = relationship(
        back_populates="trader", cascade="all, delete-orphan"
    )


class TraderPosition(Base):
    __tablename__ = "trader_positions"
    __table_args__ = (
        UniqueConstraint("proxy_wallet", "asset", name="uq_position"),
        Index("ix_positions_market", "condition_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    proxy_wallet: Mapped[str] = mapped_column(
        ForeignKey("traders.proxy_wallet", ondelete="CASCADE"), index=True
    )
    condition_id: Mapped[str | None] = mapped_column(
        ForeignKey("markets.condition_id", ondelete="CASCADE")
    )
    asset: Mapped[str] = mapped_column(String(80))  # CLOB token id
    outcome: Mapped[str | None] = mapped_column(String(64))

    size: Mapped[float] = mapped_column(Float, default=0.0)
    avg_price: Mapped[float] = mapped_column(Float, default=0.0)
    cur_price: Mapped[float] = mapped_column(Float, default=0.0)
    initial_value: Mapped[float] = mapped_column(Float, default=0.0)
    current_value: Mapped[float] = mapped_column(Float, default=0.0)
    cash_pnl: Mapped[float] = mapped_column(Float, default=0.0)
    percent_pnl: Mapped[float] = mapped_column(Float, default=0.0)
    realized_pnl: Mapped[float] = mapped_column(Float, default=0.0)
    redeemable: Mapped[bool] = mapped_column(Boolean, default=False)
    fetched_at: Mapped[datetime] = timestamp_column()

    trader: Mapped[Trader] = relationship(back_populates="positions")
    market: Mapped[Market | None] = relationship(back_populates="positions")


class TraderScore(Base):
    __tablename__ = "trader_scores"
    __table_args__ = (
        UniqueConstraint("proxy_wallet", "window", name="uq_score_window"),
        Index("ix_scores_composite", "window", "composite"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    proxy_wallet: Mapped[str] = mapped_column(
        ForeignKey("traders.proxy_wallet", ondelete="CASCADE"), index=True
    )
    window: Mapped[str] = mapped_column(String(16), default="all")  # all/30d/7d

    # Sub-scores are normalized to roughly [0, 1].
    profitability: Mapped[float] = mapped_column(Float, default=0.0)
    consistency: Mapped[float] = mapped_column(Float, default=0.0)
    sizing: Mapped[float] = mapped_column(Float, default=0.0)
    risk_adjusted: Mapped[float] = mapped_column(Float, default=0.0)
    composite: Mapped[float] = mapped_column(Float, default=0.0, index=True)
    rank: Mapped[int | None] = mapped_column(Integer)

    computed_at: Mapped[datetime] = timestamp_column()

    trader: Mapped[Trader] = relationship(back_populates="scores")


class Recommendation(Base):
    __tablename__ = "recommendations"
    __table_args__ = (
        Index("ix_recs_confidence", "confidence"),
        Index("ix_recs_market", "condition_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    condition_id: Mapped[str] = mapped_column(
        ForeignKey("markets.condition_id", ondelete="CASCADE"), index=True
    )
    outcome: Mapped[str] = mapped_column(String(64))
    asset: Mapped[str | None] = mapped_column(String(80))
    direction: Mapped[str] = mapped_column(String(8), default="BUY")  # BUY / AVOID

    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    consensus_size_usd: Mapped[float] = mapped_column(Float, default=0.0)
    supporter_count: Mapped[int] = mapped_column(Integer, default=0)
    avg_entry_price: Mapped[float] = mapped_column(Float, default=0.0)
    current_price: Mapped[float] = mapped_column(Float, default=0.0)

    # rationale: {"traders": [{"wallet", "score", "size_usd"}], "notes": "..."}
    rationale: Mapped[dict] = mapped_column(default=dict)
    status: Mapped[str] = mapped_column(String(16), default="active", index=True)
    generated_at: Mapped[datetime] = timestamp_column()


class Order(Base):
    """A trade order produced by the execution module.

    ``mode`` distinguishes paper (recorded, never sent) from live (sent to the
    CLOB). Paper orders let the strategy be evaluated forward without capital.
    """

    __tablename__ = "orders"
    __table_args__ = (Index("ix_orders_created", "created_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    condition_id: Mapped[str | None] = mapped_column(
        ForeignKey("markets.condition_id", ondelete="SET NULL")
    )
    recommendation_id: Mapped[int | None] = mapped_column(Integer)
    asset: Mapped[str] = mapped_column(String(80))  # CLOB token id
    outcome: Mapped[str | None] = mapped_column(String(64))
    side: Mapped[str] = mapped_column(String(8), default="BUY")

    mode: Mapped[str] = mapped_column(String(8), default="paper", index=True)  # paper/live
    price: Mapped[float] = mapped_column(Float, default=0.0)
    size_usd: Mapped[float] = mapped_column(Float, default=0.0)
    size_shares: Mapped[float] = mapped_column(Float, default=0.0)

    # submitted / filled / rejected / skipped / settled
    status: Mapped[str] = mapped_column(String(16), default="submitted", index=True)
    external_id: Mapped[str | None] = mapped_column(String(120))  # CLOB order id (live)
    # Realized PnL once the market resolves (set by the settlement job).
    pnl: Mapped[float] = mapped_column(Float, default=0.0)
    detail: Mapped[dict] = mapped_column(default=dict)  # reason / rationale / error / mark
    created_at: Mapped[datetime] = timestamp_column()
