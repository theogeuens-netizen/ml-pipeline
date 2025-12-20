"""
SQLAlchemy 2.0 models for the Polymarket ML data collector.

Tables:
- markets: Market metadata and tracking state
- snapshots: Time-series snapshots with ~50 feature columns
- trades: Individual trades from WebSocket
- orderbook_snapshots: Full orderbook storage
- whale_events: Whale trade tracking with impact
- task_runs: Celery task execution logging
- tier_transitions: Market tier change tracking for monitoring
"""

from datetime import datetime
from typing import Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """Base class for all models."""
    pass


class Market(Base):
    """
    Market metadata and collection tracking.

    Primary source: Gamma API /markets endpoint
    """
    __tablename__ = "markets"

    id: Mapped[int] = mapped_column(primary_key=True)
    condition_id: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    slug: Mapped[str] = mapped_column(String(255))
    question: Mapped[str] = mapped_column(Text)
    description: Mapped[Optional[str]] = mapped_column(Text)

    # Event grouping
    event_id: Mapped[Optional[str]] = mapped_column(String(100), index=True)
    event_slug: Mapped[Optional[str]] = mapped_column(String(255))
    event_title: Mapped[Optional[str]] = mapped_column(String(500))

    # Token IDs for CLOB API
    yes_token_id: Mapped[Optional[str]] = mapped_column(String(100))
    no_token_id: Mapped[Optional[str]] = mapped_column(String(100))

    # Timing
    start_date: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    end_date: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    # Initial state (at discovery)
    initial_price: Mapped[Optional[float]] = mapped_column(Numeric(10, 6))
    initial_spread: Mapped[Optional[float]] = mapped_column(Numeric(10, 6))
    initial_volume: Mapped[Optional[float]] = mapped_column(Numeric(20, 2))
    initial_liquidity: Mapped[Optional[float]] = mapped_column(Numeric(20, 2))

    # Resolution
    resolved: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    outcome: Mapped[Optional[str]] = mapped_column(String(20))  # YES/NO/INVALID
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    # Collection tracking
    tier: Mapped[int] = mapped_column(SmallInteger, default=0, index=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    tracking_started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    last_snapshot_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    snapshot_count: Mapped[int] = mapped_column(Integer, default=0)

    # Metadata
    category: Mapped[Optional[str]] = mapped_column(String(100))
    tags: Mapped[Optional[dict]] = mapped_column(JSONB)
    neg_risk: Mapped[bool] = mapped_column(Boolean, default=False)
    competitive: Mapped[Optional[float]] = mapped_column(Numeric(5, 4))
    enable_order_book: Mapped[bool] = mapped_column(Boolean, default=True)

    # Category taxonomy (assigned via rules or Claude)
    category_l1: Mapped[Optional[str]] = mapped_column(String(50), index=True)  # Domain
    category_l2: Mapped[Optional[str]] = mapped_column(String(50), index=True)  # Sub-domain
    category_l3: Mapped[Optional[str]] = mapped_column(String(50), index=True)  # Market structure
    categorized_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    categorization_method: Mapped[Optional[str]] = mapped_column(String(20), index=True)  # 'rule', 'claude', 'event'
    matched_rule_id: Mapped[Optional[int]] = mapped_column(ForeignKey("categorization_rules.id"))

    # Timestamps
    first_seen: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    # Relationships
    snapshots: Mapped[list["Snapshot"]] = relationship(back_populates="market")
    trades: Mapped[list["Trade"]] = relationship(back_populates="market")
    orderbook_snapshots: Mapped[list["OrderbookSnapshot"]] = relationship(back_populates="market")
    whale_events: Mapped[list["WhaleEvent"]] = relationship(back_populates="market")
    tier_transitions: Mapped[list["TierTransition"]] = relationship(back_populates="market")


class Snapshot(Base):
    """
    Time-series snapshot with ~50 feature columns.

    Sources:
    - Price/momentum/volume: Gamma API
    - Orderbook depth: CLOB API /book
    - Trade flow/whale metrics: WebSocket via Redis
    - Context: Computed at snapshot time
    """
    __tablename__ = "snapshots"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    market_id: Mapped[int] = mapped_column(ForeignKey("markets.id"), index=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    tier: Mapped[int] = mapped_column(SmallInteger)

    # === PRICE FIELDS (5) ===
    price: Mapped[float] = mapped_column(Numeric(10, 6))
    best_bid: Mapped[Optional[float]] = mapped_column(Numeric(10, 6))
    best_ask: Mapped[Optional[float]] = mapped_column(Numeric(10, 6))
    spread: Mapped[Optional[float]] = mapped_column(Numeric(10, 6))
    last_trade_price: Mapped[Optional[float]] = mapped_column(Numeric(10, 6))

    # === MOMENTUM FIELDS - FREE FROM GAMMA (3) ===
    price_change_1d: Mapped[Optional[float]] = mapped_column(Numeric(10, 6))
    price_change_1w: Mapped[Optional[float]] = mapped_column(Numeric(10, 6))
    price_change_1m: Mapped[Optional[float]] = mapped_column(Numeric(10, 6))

    # === VOLUME FIELDS (4) ===
    volume_total: Mapped[Optional[float]] = mapped_column(Numeric(20, 2))
    volume_24h: Mapped[Optional[float]] = mapped_column(Numeric(20, 2))
    volume_1w: Mapped[Optional[float]] = mapped_column(Numeric(20, 2))
    liquidity: Mapped[Optional[float]] = mapped_column(Numeric(20, 2))

    # === ORDERBOOK DEPTH - FROM CLOB (8) ===
    bid_depth_5: Mapped[Optional[float]] = mapped_column(Numeric(20, 2))
    bid_depth_10: Mapped[Optional[float]] = mapped_column(Numeric(20, 2))
    bid_depth_20: Mapped[Optional[float]] = mapped_column(Numeric(20, 2))
    bid_depth_50: Mapped[Optional[float]] = mapped_column(Numeric(20, 2))
    ask_depth_5: Mapped[Optional[float]] = mapped_column(Numeric(20, 2))
    ask_depth_10: Mapped[Optional[float]] = mapped_column(Numeric(20, 2))
    ask_depth_20: Mapped[Optional[float]] = mapped_column(Numeric(20, 2))
    ask_depth_50: Mapped[Optional[float]] = mapped_column(Numeric(20, 2))

    # === ORDERBOOK DERIVED (7) ===
    bid_levels: Mapped[Optional[int]] = mapped_column(SmallInteger)
    ask_levels: Mapped[Optional[int]] = mapped_column(SmallInteger)
    book_imbalance: Mapped[Optional[float]] = mapped_column(Numeric(10, 6))
    bid_wall_price: Mapped[Optional[float]] = mapped_column(Numeric(10, 6))
    bid_wall_size: Mapped[Optional[float]] = mapped_column(Numeric(20, 2))
    ask_wall_price: Mapped[Optional[float]] = mapped_column(Numeric(10, 6))
    ask_wall_size: Mapped[Optional[float]] = mapped_column(Numeric(20, 2))

    # === TRADE FLOW - FROM WEBSOCKET VIA REDIS (9) ===
    trade_count_1h: Mapped[Optional[int]] = mapped_column(Integer)
    buy_count_1h: Mapped[Optional[int]] = mapped_column(Integer)
    sell_count_1h: Mapped[Optional[int]] = mapped_column(Integer)
    volume_1h: Mapped[Optional[float]] = mapped_column(Numeric(20, 2))
    buy_volume_1h: Mapped[Optional[float]] = mapped_column(Numeric(20, 2))
    sell_volume_1h: Mapped[Optional[float]] = mapped_column(Numeric(20, 2))
    avg_trade_size_1h: Mapped[Optional[float]] = mapped_column(Numeric(20, 2))
    max_trade_size_1h: Mapped[Optional[float]] = mapped_column(Numeric(20, 2))
    vwap_1h: Mapped[Optional[float]] = mapped_column(Numeric(10, 6))

    # === WHALE METRICS - FROM WEBSOCKET VIA REDIS (8) ===
    whale_count_1h: Mapped[Optional[int]] = mapped_column(Integer)
    whale_volume_1h: Mapped[Optional[float]] = mapped_column(Numeric(20, 2))
    whale_buy_volume_1h: Mapped[Optional[float]] = mapped_column(Numeric(20, 2))
    whale_sell_volume_1h: Mapped[Optional[float]] = mapped_column(Numeric(20, 2))
    whale_net_flow_1h: Mapped[Optional[float]] = mapped_column(Numeric(20, 2))
    whale_buy_ratio_1h: Mapped[Optional[float]] = mapped_column(Numeric(10, 6))
    time_since_whale: Mapped[Optional[int]] = mapped_column(Integer)  # seconds
    pct_volume_from_whales: Mapped[Optional[float]] = mapped_column(Numeric(10, 6))

    # === CONTEXT FIELDS (3) ===
    hours_to_close: Mapped[Optional[float]] = mapped_column(Numeric(10, 4))
    day_of_week: Mapped[Optional[int]] = mapped_column(SmallInteger)
    hour_of_day: Mapped[Optional[int]] = mapped_column(SmallInteger)

    # Relationship
    market: Mapped["Market"] = relationship(back_populates="snapshots")


class Trade(Base):
    """
    Individual trades from WebSocket.

    Source: WebSocket last_trade_price events
    """
    __tablename__ = "trades"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    market_id: Mapped[int] = mapped_column(ForeignKey("markets.id"), index=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)

    price: Mapped[float] = mapped_column(Numeric(10, 6))
    size: Mapped[float] = mapped_column(Numeric(20, 2))
    side: Mapped[str] = mapped_column(String(4))  # BUY/SELL

    whale_tier: Mapped[int] = mapped_column(SmallInteger, default=0)  # 0/1/2/3

    # Market state at trade time
    best_bid: Mapped[Optional[float]] = mapped_column(Numeric(10, 6))
    best_ask: Mapped[Optional[float]] = mapped_column(Numeric(10, 6))
    mid_price: Mapped[Optional[float]] = mapped_column(Numeric(10, 6))

    # Relationship
    market: Mapped["Market"] = relationship(back_populates="trades")


class OrderbookSnapshot(Base):
    """
    Full orderbook storage for detailed analysis.

    Source: CLOB API /book endpoint
    """
    __tablename__ = "orderbook_snapshots"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    market_id: Mapped[int] = mapped_column(ForeignKey("markets.id"), index=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)

    bids: Mapped[dict] = mapped_column(JSONB)  # [[price, size], ...]
    asks: Mapped[dict] = mapped_column(JSONB)

    # Summary stats
    total_bid_depth: Mapped[float] = mapped_column(Numeric(20, 2))
    total_ask_depth: Mapped[float] = mapped_column(Numeric(20, 2))
    num_bid_levels: Mapped[int] = mapped_column(SmallInteger)
    num_ask_levels: Mapped[int] = mapped_column(SmallInteger)

    # Largest orders (walls)
    largest_bid_price: Mapped[Optional[float]] = mapped_column(Numeric(10, 6))
    largest_bid_size: Mapped[Optional[float]] = mapped_column(Numeric(20, 2))
    largest_ask_price: Mapped[Optional[float]] = mapped_column(Numeric(10, 6))
    largest_ask_size: Mapped[Optional[float]] = mapped_column(Numeric(20, 2))

    # Relationship
    market: Mapped["Market"] = relationship(back_populates="orderbook_snapshots")


class WhaleEvent(Base):
    """
    Whale trade tracking with impact measurement.

    Created when trade size >= whale_tier_2_threshold ($2000)
    """
    __tablename__ = "whale_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    market_id: Mapped[int] = mapped_column(ForeignKey("markets.id"), index=True)
    trade_id: Mapped[Optional[int]] = mapped_column(ForeignKey("trades.id"))
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)

    price: Mapped[float] = mapped_column(Numeric(10, 6))
    size: Mapped[float] = mapped_column(Numeric(20, 2))
    side: Mapped[str] = mapped_column(String(4))
    whale_tier: Mapped[int] = mapped_column(SmallInteger)

    # Impact tracking (filled by background task)
    price_before: Mapped[Optional[float]] = mapped_column(Numeric(10, 6))
    price_after_1m: Mapped[Optional[float]] = mapped_column(Numeric(10, 6))
    price_after_5m: Mapped[Optional[float]] = mapped_column(Numeric(10, 6))
    impact_1m: Mapped[Optional[float]] = mapped_column(Numeric(10, 6))
    impact_5m: Mapped[Optional[float]] = mapped_column(Numeric(10, 6))

    # Relationship
    market: Mapped["Market"] = relationship(back_populates="whale_events")


class TaskRun(Base):
    """
    Celery task execution logging for monitoring.
    """
    __tablename__ = "task_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    task_name: Mapped[str] = mapped_column(String(100), index=True)
    task_id: Mapped[str] = mapped_column(String(100))
    tier: Mapped[Optional[int]] = mapped_column(SmallInteger)

    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[Optional[int]] = mapped_column(Integer)

    status: Mapped[str] = mapped_column(String(20), index=True)  # running/success/failed
    markets_processed: Mapped[Optional[int]] = mapped_column(Integer)
    rows_inserted: Mapped[Optional[int]] = mapped_column(Integer)

    error_message: Mapped[Optional[str]] = mapped_column(Text)
    error_traceback: Mapped[Optional[str]] = mapped_column(Text)


class TierTransition(Base):
    """
    Track market tier changes for monitoring dashboard.

    Records every tier transition (T0→T1, T1→T2, etc.) for visibility
    into market lifecycle and system activity. Retained for 7 days.
    """
    __tablename__ = "tier_transitions"

    id: Mapped[int] = mapped_column(primary_key=True)
    market_id: Mapped[int] = mapped_column(ForeignKey("markets.id"), index=True)
    condition_id: Mapped[str] = mapped_column(String(100))
    market_slug: Mapped[Optional[str]] = mapped_column(String(255))
    from_tier: Mapped[int] = mapped_column(SmallInteger)
    to_tier: Mapped[int] = mapped_column(SmallInteger)  # -1 indicates deactivated
    transitioned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    hours_to_close: Mapped[Optional[float]] = mapped_column(Numeric(10, 4))
    reason: Mapped[Optional[str]] = mapped_column(String(50))  # "time", "deactivated", "resolved", etc.

    # Relationship
    market: Mapped["Market"] = relationship(back_populates="tier_transitions")


class CategorizationRule(Base):
    """
    Database-stored categorization rules for pattern matching.

    Rules are loaded at runtime and applied to uncategorized markets.
    Stats track accuracy for validation and improvement.
    """
    __tablename__ = "categorization_rules"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    l1: Mapped[str] = mapped_column(String(50))
    l2: Mapped[str] = mapped_column(String(50))

    # Matching criteria
    keywords: Mapped[dict] = mapped_column(JSONB)  # ["bitcoin", "btc"]
    negative_keywords: Mapped[Optional[dict]] = mapped_column(JSONB)  # Exclusions
    l3_patterns: Mapped[Optional[dict]] = mapped_column(JSONB)  # {"SPREAD": ["pattern"]}
    l3_default: Mapped[Optional[str]] = mapped_column(String(50))

    # Stats (updated by validation)
    times_matched: Mapped[int] = mapped_column(Integer, default=0)
    times_validated: Mapped[int] = mapped_column(Integer, default=0)
    times_correct: Mapped[int] = mapped_column(Integer, default=0)

    # Meta
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    notes: Mapped[Optional[str]] = mapped_column(Text)

    @property
    def accuracy(self) -> Optional[float]:
        """Calculate accuracy from validation stats."""
        if self.times_validated > 0:
            return self.times_correct / self.times_validated
        return None


class RuleValidation(Base):
    """
    Track validation results for categorization rules.

    Used to measure and improve rule accuracy over time.
    """
    __tablename__ = "rule_validations"

    id: Mapped[int] = mapped_column(primary_key=True)
    market_id: Mapped[int] = mapped_column(ForeignKey("markets.id"), index=True)
    rule_id: Mapped[Optional[int]] = mapped_column(ForeignKey("categorization_rules.id"), index=True)

    # What rule predicted
    rule_l1: Mapped[Optional[str]] = mapped_column(String(50))
    rule_l2: Mapped[Optional[str]] = mapped_column(String(50))
    rule_l3: Mapped[Optional[str]] = mapped_column(String(50))

    # Ground truth (from Claude or human)
    correct_l1: Mapped[Optional[str]] = mapped_column(String(50))
    correct_l2: Mapped[Optional[str]] = mapped_column(String(50))
    correct_l3: Mapped[Optional[str]] = mapped_column(String(50))

    # Result
    is_correct: Mapped[Optional[bool]] = mapped_column(Boolean)
    validated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    validated_by: Mapped[Optional[str]] = mapped_column(String(50))  # 'claude', 'human'
