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
from decimal import Decimal
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
    gamma_id: Mapped[Optional[int]] = mapped_column(Integer, index=True)  # Gamma API numeric ID
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

    # Trading Status (from Gamma API)
    # These track whether the market is open for trading, separate from resolution
    closed: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    closed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    accepting_orders: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    accepting_orders_updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    # UMA Resolution Status (from Gamma API)
    # Values: null (not started), "proposed" (in challenge window), "disputed", "resolved", "flagged"
    uma_resolution_status: Mapped[Optional[str]] = mapped_column(String(20), index=True)
    uma_status_updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

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
    token_type: Mapped[str] = mapped_column(String(3), default="YES", index=True)  # YES/NO

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


# ============================================================================
# NEWS DATA MODELS
# ============================================================================
# News articles from external APIs (Marketaux, GDELT) for XGBoost features.
# ============================================================================


class NewsItem(Base):
    """
    News articles from external APIs.

    Sources:
    - Marketaux: Crypto/financial news with sentiment
    - GDELT: Global news (stored separately in BigQuery, but can cache here)

    Used to compute news features for XGBoost training:
    - news_count_24h, news_sentiment_avg, news_momentum, etc.
    """
    __tablename__ = "news_items"

    id: Mapped[int] = mapped_column(primary_key=True)

    # Source tracking
    source: Mapped[str] = mapped_column(String(50), index=True)  # "marketaux", "gdelt"
    source_id: Mapped[Optional[str]] = mapped_column(String(255), unique=True)  # Dedupe key

    # Content
    title: Mapped[str] = mapped_column(Text)
    snippet: Mapped[Optional[str]] = mapped_column(Text)  # Article excerpt
    url: Mapped[Optional[str]] = mapped_column(Text)

    # Timestamps
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    # Sentiment (API-provided)
    sentiment_score: Mapped[Optional[float]] = mapped_column(Numeric(10, 6))  # -1 to 1 or API-specific

    # Categorization
    category: Mapped[Optional[str]] = mapped_column(String(50), index=True)  # CRYPTO, POLITICS, etc.
    symbols: Mapped[Optional[list]] = mapped_column(JSONB)  # ["BTC", "ETH"] for crypto
    entities: Mapped[Optional[dict]] = mapped_column(JSONB)  # Named entities

    # Raw API response (for future reprocessing)
    raw_response: Mapped[Optional[dict]] = mapped_column(JSONB)


# ============================================================================
# HISTORICAL DATA MODELS (for backtesting)
# ============================================================================
# These tables are separate from polymarket-ml's operational data and are used
# only for backtesting, not for XGBoost training.
# ============================================================================


class HistoricalMarket(Base):
    """
    Historical market data for backtesting.

    This is separate from the 'markets' table which contains
    polymarket-ml's high-granularity operational data.
    """
    __tablename__ = "historical_markets"

    id: Mapped[int] = mapped_column(primary_key=True)
    external_id: Mapped[str] = mapped_column(String(100), unique=True, index=True)  # Polymarket condition_id
    question: Mapped[Optional[str]] = mapped_column(Text)
    description: Mapped[Optional[str]] = mapped_column(Text)
    close_date: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), index=True)

    # Categories
    macro_category: Mapped[Optional[str]] = mapped_column(String(50), index=True)  # Crypto, Sports, Politics
    micro_category: Mapped[Optional[str]] = mapped_column(String(50), index=True)  # Bitcoin, NFL, Trump

    # Market metrics
    volume: Mapped[Optional[float]] = mapped_column(Numeric(20, 2))
    liquidity: Mapped[Optional[float]] = mapped_column(Numeric(20, 2))

    # Resolution data (critical for backtesting)
    resolution_status: Mapped[Optional[str]] = mapped_column(String(20), index=True)  # resolved, unresolved, disputed
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    winner: Mapped[Optional[str]] = mapped_column(String(50))  # YES, NO, or specific outcome name
    resolved_early: Mapped[Optional[bool]] = mapped_column(Boolean)

    # Metadata
    platform: Mapped[str] = mapped_column(String(20), default="polymarket")
    imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    # Relationships
    price_snapshots: Mapped[list["HistoricalPriceSnapshot"]] = relationship(
        back_populates="market", cascade="all, delete-orphan"
    )


class HistoricalPriceSnapshot(Base):
    """
    Historical price snapshot for backtesting.

    This is separate from the 'snapshots' table which contains
    polymarket-ml's high-granularity feature data.
    """
    __tablename__ = "historical_price_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True)
    market_id: Mapped[int] = mapped_column(ForeignKey("historical_markets.id", ondelete="CASCADE"), index=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)

    # OHLC prices (0-1 scale for Polymarket)
    price: Mapped[Optional[float]] = mapped_column(Numeric(10, 6))  # Close price
    open_price: Mapped[Optional[float]] = mapped_column(Numeric(10, 6))
    high_price: Mapped[Optional[float]] = mapped_column(Numeric(10, 6))
    low_price: Mapped[Optional[float]] = mapped_column(Numeric(10, 6))

    # Bid/Ask
    bid_price: Mapped[Optional[float]] = mapped_column(Numeric(10, 6))
    ask_price: Mapped[Optional[float]] = mapped_column(Numeric(10, 6))

    # Volume
    volume: Mapped[Optional[float]] = mapped_column(Numeric(20, 2))

    # Relationships
    market: Mapped["HistoricalMarket"] = relationship(back_populates="price_snapshots")

    __table_args__ = (
        # Unique constraint to prevent duplicate snapshots
        {"sqlite_autoincrement": True},
    )


# Alias for backwards compatibility with data.py imports
HistoricalMarketModel = HistoricalMarket
HistoricalPriceSnapshotModel = HistoricalPriceSnapshot


# ============================================================================
# CS:GO STRATEGY DATA MODELS
# ============================================================================
# Team performance data for the CS:GO volatility hedge strategy.
# ============================================================================


class CSGOTeam(Base):
    """
    CS:GO team statistics from historical match data.

    Used by CSGOVolatilityStrategy to calculate win rate differentials
    and determine position sizing based on signal strength.

    Data source: /home/theo/futarchy/csgo_team_leaderboard.csv
    """
    __tablename__ = "csgo_teams"

    id: Mapped[int] = mapped_column(primary_key=True)
    team_name: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    wins: Mapped[int] = mapped_column(Integer, default=0)
    losses: Mapped[int] = mapped_column(Integer, default=0)
    total_matches: Mapped[int] = mapped_column(Integer, default=0)
    win_rate_pct: Mapped[float] = mapped_column(Numeric(5, 2))

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class CSGOH2H(Base):
    """
    Head-to-head records between CS:GO teams.

    Normalized so that team1_name < team2_name alphabetically
    to avoid duplicate entries.

    Data source: /home/theo/futarchy/csgo_h2h_matrix.csv
    """
    __tablename__ = "csgo_h2h"

    id: Mapped[int] = mapped_column(primary_key=True)
    team1_name: Mapped[str] = mapped_column(String(100), index=True)
    team2_name: Mapped[str] = mapped_column(String(100), index=True)
    team1_wins: Mapped[int] = mapped_column(Integer, default=0)
    team2_wins: Mapped[int] = mapped_column(Integer, default=0)
    total_matches: Mapped[int] = mapped_column(Integer, default=0)

    # Timestamps
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        # Unique constraint on team pair
        {"sqlite_autoincrement": True},
    )


class CSGOMatch(Base):
    """
    CS:GO match metadata for real-time trading pipeline.

    Populated from Gamma API with game start times, team names,
    and tournament info. Supports manual override of game_start_time
    for strategy triggers.

    Used by:
    - CSGOWebSocketCollector: Subscribe to markets within 6h of game start
    - CSGOMomentumStrategy: Entry at game start
    - CSGOLongshotStrategy: Entry on price spikes
    """
    __tablename__ = "csgo_matches"

    id: Mapped[int] = mapped_column(primary_key=True)
    market_id: Mapped[int] = mapped_column(ForeignKey("markets.id"), index=True)
    gamma_id: Mapped[Optional[int]] = mapped_column(Integer, index=True)
    condition_id: Mapped[str] = mapped_column(String(100), unique=True, index=True)

    # Team names from Gamma API outcomes field
    team_yes: Mapped[Optional[str]] = mapped_column(String(100), index=True)
    team_no: Mapped[Optional[str]] = mapped_column(String(100), index=True)

    # Game timing
    game_start_time: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), index=True
    )
    game_start_override: Mapped[bool] = mapped_column(Boolean, default=False)
    end_date: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    # Match metadata
    tournament: Mapped[Optional[str]] = mapped_column(String(255))
    format: Mapped[Optional[str]] = mapped_column(String(20))  # BO1, BO3, BO5
    market_type: Mapped[Optional[str]] = mapped_column(String(50))  # moneyline, child_moneyline
    group_item_title: Mapped[Optional[str]] = mapped_column(String(100))  # Match Winner, Map 1 Winner
    game_id: Mapped[Optional[str]] = mapped_column(String(50))  # External reference
    map_number: Mapped[Optional[int]] = mapped_column(Integer)  # 1, 2, 3 for map winners; None for series

    # GRID API integration
    grid_series_id: Mapped[Optional[str]] = mapped_column(String(50), index=True)  # GRID series ID
    grid_yes_team_id: Mapped[Optional[str]] = mapped_column(String(50))  # Which GRID team = Polymarket YES
    grid_match_confidence: Mapped[Optional[Decimal]] = mapped_column(Numeric(5, 4))  # Match confidence 0-1

    # State
    subscribed: Mapped[bool] = mapped_column(Boolean, default=False, index=True)

    # Market lifecycle (CSGO-independent, not relying on markets table)
    closed: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    resolved: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    closed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    accepting_orders: Mapped[bool] = mapped_column(Boolean, default=True)
    outcome: Mapped[Optional[str]] = mapped_column(String(100))  # Winning team name
    last_status_check: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    # Market data (refreshed by status poller)
    yes_price: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 6))
    no_price: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 6))
    best_bid: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 6))  # YES token best bid
    best_ask: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 6))  # YES token best ask
    spread: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 6))    # Calculated: ask - bid
    volume_total: Mapped[Optional[Decimal]] = mapped_column(Numeric(20, 2))
    volume_24h: Mapped[Optional[Decimal]] = mapped_column(Numeric(20, 2))
    liquidity: Mapped[Optional[Decimal]] = mapped_column(Numeric(20, 2))

    # Full Gamma API response
    gamma_data: Mapped[Optional[dict]] = mapped_column(JSONB)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    # Relationship
    market: Mapped["Market"] = relationship()


class CSGOPriceTick(Base):
    """
    High-frequency price data from CSGO websocket.

    Stores every trade, book update, and price change event.
    Used for:
    - 5-second OHLC bar aggregation for charts
    - Real-time price monitoring

    Retention: 7 days (cleaned up by daily task)
    """
    __tablename__ = "csgo_price_ticks"

    id: Mapped[int] = mapped_column(primary_key=True)
    market_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    token_type: Mapped[str] = mapped_column(String(3), nullable=False)  # YES or NO
    event_type: Mapped[str] = mapped_column(String(20), nullable=False)  # trade, book, price_change

    # Price data
    price: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 6))
    best_bid: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 6))
    best_ask: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 6))
    spread: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 6))

    # Trade data (for trade events)
    trade_size: Mapped[Optional[Decimal]] = mapped_column(Numeric(15, 6))
    trade_side: Mapped[Optional[str]] = mapped_column(String(4))  # BUY or SELL

    # Calculated metrics
    price_velocity_1m: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 6))


class CSGOGridEvent(Base):
    """
    GRID game state change events correlated with Polymarket prices.

    Records score changes (round wins, map wins) detected from GRID API
    along with prices at detection and after delays (30s, 1m, 5m).

    Used for:
    - Measuring price sensitivity to game state changes
    - Measuring market latency (how fast markets reprice)
    - Building fair value models
    - Identifying front-running or mean reversion opportunities
    """
    __tablename__ = "csgo_grid_events"

    id: Mapped[int] = mapped_column(primary_key=True)

    # Linking
    market_id: Mapped[int] = mapped_column(Integer, index=True)  # CSGOMatch.market_id
    event_id: Mapped[str] = mapped_column(String(100), index=True)  # Polymarket event group
    grid_series_id: Mapped[str] = mapped_column(String(50), index=True)

    # Timing
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    grid_timestamp: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    # Event type
    event_type: Mapped[str] = mapped_column(String(20))  # "round", "map", "series"
    winner: Mapped[str] = mapped_column(String(5))  # "YES", "NO"

    # Score BEFORE event
    prev_round_yes: Mapped[int] = mapped_column(Integer)
    prev_round_no: Mapped[int] = mapped_column(Integer)
    prev_map_yes: Mapped[int] = mapped_column(Integer)
    prev_map_no: Mapped[int] = mapped_column(Integer)

    # Score AFTER event
    new_round_yes: Mapped[int] = mapped_column(Integer)
    new_round_no: Mapped[int] = mapped_column(Integer)
    new_map_yes: Mapped[int] = mapped_column(Integer)
    new_map_no: Mapped[int] = mapped_column(Integer)

    # Context
    format: Mapped[str] = mapped_column(String(10))  # "bo1", "bo3", "bo5"
    map_number: Mapped[int] = mapped_column(Integer)
    map_name: Mapped[Optional[str]] = mapped_column(String(50))
    is_overtime: Mapped[bool] = mapped_column(Boolean, default=False)
    rounds_in_event: Mapped[int] = mapped_column(Integer, default=1)  # >1 if missed rounds

    # Derived (computed on insert for easy querying)
    total_rounds_before: Mapped[int] = mapped_column(Integer)
    round_diff_before: Mapped[int] = mapped_column(Integer)  # YES - NO
    map_diff_before: Mapped[int] = mapped_column(Integer)

    # Price at detection (from CLOB)
    price_at_detection: Mapped[Decimal] = mapped_column(Numeric(10, 6))
    spread_at_detection: Mapped[Decimal] = mapped_column(Numeric(10, 6))
    best_bid_at_detection: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 6))
    best_ask_at_detection: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 6))
    price_source: Mapped[str] = mapped_column(String(20))  # "clob", "tick"

    # Price after (filled by background task)
    price_after_30sec: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 6))
    price_after_1min: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 6))
    price_after_5min: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 6))

    # Analysis helpers (computed on fill)
    price_move_30sec: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 6))
    price_move_1min: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 6))
    price_move_5min: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 6))
    move_direction_correct: Mapped[Optional[bool]] = mapped_column(Boolean)

    # Market state
    market_accepting_orders: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)

    # Debug
    grid_state_json: Mapped[Optional[dict]] = mapped_column(JSONB)
    notes: Mapped[Optional[str]] = mapped_column(Text)


class GRIDPollerState(Base):
    """
    Persisted state for GRID poller to survive restarts.

    Stores the last known state for each series being polled,
    allowing change detection to work across process restarts.
    """
    __tablename__ = "grid_poller_state"

    series_id: Mapped[str] = mapped_column(String(50), primary_key=True)
    market_id: Mapped[int] = mapped_column(Integer, index=True)
    last_state_json: Mapped[dict] = mapped_column(JSONB)
    last_poll_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    polls_count: Mapped[int] = mapped_column(Integer, default=0)
