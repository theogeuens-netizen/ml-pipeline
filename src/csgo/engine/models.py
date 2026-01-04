"""
SQLAlchemy models for the isolated CSGO Trading Engine.

Tables:
- csgo_positions: Individual positions (YES or NO token)
- csgo_position_legs: Audit trail for entries/exits
- csgo_spreads: Linked YES+NO positions
- csgo_trades: Execution records
- csgo_strategy_state: Per-strategy capital and performance
- csgo_strategy_market_state: Per-market state for multi-stage strategies
"""

from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Optional, List

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.db.models import Base


class CSGOPositionStatus(str, Enum):
    """Position lifecycle status."""
    OPEN = "open"
    PARTIAL = "partial"  # Partially closed
    CLOSED = "closed"


class CSGOSpreadStatus(str, Enum):
    """Spread lifecycle status."""
    OPEN = "open"
    PARTIAL = "partial"  # One leg closed
    CLOSED = "closed"


class CSGOLegType(str, Enum):
    """Types of position legs."""
    ENTRY = "entry"
    ADD = "add"
    PARTIAL_EXIT = "partial_exit"
    FULL_EXIT = "full_exit"


class CSGOPosition(Base):
    """
    Individual position in the CSGO trading engine.

    Isolated from main positions table.
    Supports partial exits via remaining_shares tracking.
    """
    __tablename__ = "csgo_positions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    strategy_name: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    market_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    condition_id: Mapped[str] = mapped_column(String(100), nullable=False)

    # Token info
    token_id: Mapped[str] = mapped_column(String(100), nullable=False)
    token_type: Mapped[str] = mapped_column(String(3), nullable=False)  # 'YES' or 'NO'
    side: Mapped[str] = mapped_column(String(4), nullable=False)  # 'BUY' or 'SELL'

    # Size tracking (supports partial exits)
    initial_shares: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    remaining_shares: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    avg_entry_price: Mapped[Decimal] = mapped_column(Numeric(10, 6), nullable=False)
    cost_basis: Mapped[Decimal] = mapped_column(Numeric(20, 2), nullable=False)

    # Current state
    current_price: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 6))
    unrealized_pnl: Mapped[Optional[Decimal]] = mapped_column(Numeric(20, 2))
    realized_pnl: Mapped[Decimal] = mapped_column(Numeric(20, 2), default=0)

    # Spread linking
    spread_id: Mapped[Optional[int]] = mapped_column(
        BigInteger, ForeignKey("csgo_spreads.id"), index=True
    )

    # Match context (denormalized for fast access)
    team_yes: Mapped[Optional[str]] = mapped_column(String(100))
    team_no: Mapped[Optional[str]] = mapped_column(String(100))
    game_start_time: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    format: Mapped[Optional[str]] = mapped_column(String(10))  # BO1, BO3, BO5

    # Status
    status: Mapped[str] = mapped_column(
        String(20), default=CSGOPositionStatus.OPEN.value, index=True
    )
    close_reason: Mapped[Optional[str]] = mapped_column(String(50))

    # Timestamps
    opened_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    closed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    # Relationships
    legs: Mapped[List["CSGOPositionLeg"]] = relationship(
        "CSGOPositionLeg", back_populates="position", lazy="dynamic"
    )
    trades: Mapped[List["CSGOTrade"]] = relationship(
        "CSGOTrade", back_populates="position", lazy="dynamic"
    )
    spread: Mapped[Optional["CSGOSpread"]] = relationship(
        "CSGOSpread",
        foreign_keys=[spread_id],
        back_populates="positions",
    )

    __table_args__ = (
        # Unique constraint: one open position per strategy/market/token
        Index(
            "ux_csgo_pos_strategy_market_token",
            strategy_name, market_id, token_id,
            unique=True,
            postgresql_where=(status == CSGOPositionStatus.OPEN.value),
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<CSGOPosition(id={self.id}, strategy={self.strategy_name}, "
            f"token={self.token_type}, shares={self.remaining_shares}, status={self.status})>"
        )


class CSGOPositionLeg(Base):
    """
    Audit trail for position entries and exits.

    Every entry, add, or exit creates a leg record.
    Enables full replay of position history.
    """
    __tablename__ = "csgo_position_legs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    position_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("csgo_positions.id"), nullable=False, index=True
    )

    # Leg details
    leg_type: Mapped[str] = mapped_column(String(20), nullable=False)  # entry, add, partial_exit, full_exit
    shares_delta: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)  # +ve for entry, -ve for exit
    price: Mapped[Decimal] = mapped_column(Numeric(10, 6), nullable=False)
    cost_delta: Mapped[Decimal] = mapped_column(Numeric(20, 2), nullable=False)
    realized_pnl: Mapped[Optional[Decimal]] = mapped_column(Numeric(20, 2))  # For exits

    # Trigger context
    trigger_price: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 6))
    trigger_reason: Mapped[Optional[str]] = mapped_column(String(100))

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    # Relationships
    position: Mapped["CSGOPosition"] = relationship(
        "CSGOPosition", back_populates="legs"
    )
    trade: Mapped[Optional["CSGOTrade"]] = relationship(
        "CSGOTrade", back_populates="leg", uselist=False
    )

    def __repr__(self) -> str:
        return (
            f"<CSGOPositionLeg(id={self.id}, type={self.leg_type}, "
            f"shares={self.shares_delta}, price={self.price})>"
        )


class CSGOSpread(Base):
    """
    Linked YES + NO positions as a single spread.

    Tracks aggregate P&L across both legs.
    Used for scalping strategies that buy both sides.
    """
    __tablename__ = "csgo_spreads"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    strategy_name: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    market_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    condition_id: Mapped[str] = mapped_column(String(100), nullable=False)

    spread_type: Mapped[str] = mapped_column(String(20), nullable=False)  # 'scalp', 'hedge', 'arb'

    # Linked positions (set after positions are created)
    yes_position_id: Mapped[Optional[int]] = mapped_column(BigInteger)
    no_position_id: Mapped[Optional[int]] = mapped_column(BigInteger)

    # Aggregate tracking
    total_cost_basis: Mapped[Decimal] = mapped_column(Numeric(20, 2), default=0)
    total_realized_pnl: Mapped[Decimal] = mapped_column(Numeric(20, 2), default=0)
    total_unrealized_pnl: Mapped[Decimal] = mapped_column(Numeric(20, 2), default=0)

    # Match context
    team_yes: Mapped[Optional[str]] = mapped_column(String(100))
    team_no: Mapped[Optional[str]] = mapped_column(String(100))
    entry_yes_price: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 6))

    # Status
    status: Mapped[str] = mapped_column(
        String(20), default=CSGOSpreadStatus.OPEN.value, index=True
    )

    # Timestamps
    opened_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    closed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    # Relationships
    positions: Mapped[List["CSGOPosition"]] = relationship(
        "CSGOPosition",
        foreign_keys=[CSGOPosition.spread_id],
        back_populates="spread",
    )

    def __repr__(self) -> str:
        return (
            f"<CSGOSpread(id={self.id}, strategy={self.strategy_name}, "
            f"type={self.spread_type}, status={self.status})>"
        )


class CSGOTrade(Base):
    """
    Execution record for CSGO trades.

    Isolated from main executor_trades table.
    Links to position and leg for full audit trail.
    """
    __tablename__ = "csgo_trades"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    position_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("csgo_positions.id"), nullable=False, index=True
    )
    leg_id: Mapped[Optional[int]] = mapped_column(
        BigInteger, ForeignKey("csgo_position_legs.id")
    )

    # Execution details
    token_id: Mapped[str] = mapped_column(String(100), nullable=False)
    side: Mapped[str] = mapped_column(String(4), nullable=False)  # BUY or SELL
    shares: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    price: Mapped[Decimal] = mapped_column(Numeric(10, 6), nullable=False)
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(20, 2), nullable=False)

    # Orderbook state at execution
    best_bid: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 6))
    best_ask: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 6))
    spread: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 6))
    slippage: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 6))

    # Match context (denormalized for audit trail)
    team_yes: Mapped[Optional[str]] = mapped_column(String(100))
    team_no: Mapped[Optional[str]] = mapped_column(String(100))
    format: Mapped[Optional[str]] = mapped_column(String(10))
    map_number: Mapped[Optional[int]] = mapped_column(Integer)
    game_start_time: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    # Context
    trigger_tick_id: Mapped[Optional[str]] = mapped_column(String(50))

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    # Relationships
    position: Mapped["CSGOPosition"] = relationship(
        "CSGOPosition", back_populates="trades"
    )
    leg: Mapped[Optional["CSGOPositionLeg"]] = relationship(
        "CSGOPositionLeg", back_populates="trade"
    )

    def __repr__(self) -> str:
        return (
            f"<CSGOTrade(id={self.id}, side={self.side}, "
            f"shares={self.shares}, price={self.price})>"
        )


class CSGOStrategyState(Base):
    """
    Per-strategy capital allocation and performance.

    Each strategy has an isolated wallet with tracked P&L.
    """
    __tablename__ = "csgo_strategy_state"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    strategy_name: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)

    # Capital
    allocated_usd: Mapped[Decimal] = mapped_column(Numeric(20, 2), default=400)
    available_usd: Mapped[Decimal] = mapped_column(Numeric(20, 2), default=400)

    # Performance
    total_realized_pnl: Mapped[Decimal] = mapped_column(Numeric(20, 2), default=0)
    total_unrealized_pnl: Mapped[Decimal] = mapped_column(Numeric(20, 2), default=0)
    trade_count: Mapped[int] = mapped_column(Integer, default=0)
    win_count: Mapped[int] = mapped_column(Integer, default=0)
    loss_count: Mapped[int] = mapped_column(Integer, default=0)

    # Risk metrics
    max_drawdown_usd: Mapped[Decimal] = mapped_column(Numeric(20, 2), default=0)
    high_water_mark: Mapped[Decimal] = mapped_column(Numeric(20, 2), default=400)

    # State
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_trade_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    def __repr__(self) -> str:
        return (
            f"<CSGOStrategyState(strategy={self.strategy_name}, "
            f"available=${self.available_usd}, pnl=${self.total_realized_pnl})>"
        )


class CSGOStrategyMarketState(Base):
    """
    Per-market state for complex multi-stage strategies.

    Persists strategy stage and decision context across restarts.
    Enables stateful strategies like swing trading.
    """
    __tablename__ = "csgo_strategy_market_state"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    strategy_name: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    market_id: Mapped[int] = mapped_column(Integer, nullable=False)
    condition_id: Mapped[str] = mapped_column(String(100), nullable=False)

    # Strategy stage (e.g., 'WAITING', 'ENTERED_YES', 'SWITCHED_NO', 'EXITED')
    stage: Mapped[str] = mapped_column(String(50), nullable=False, default="WAITING")

    # Price tracking for decision logic
    entry_price: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 6))
    switch_price: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 6))
    exit_price: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 6))
    high_water_mark: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 6))
    low_water_mark: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 6))

    # Counters for complex logic
    switches_count: Mapped[int] = mapped_column(Integer, default=0)
    reentries_count: Mapped[int] = mapped_column(Integer, default=0)

    # Flexible state (for strategy-specific data)
    custom_state: Mapped[Optional[dict]] = mapped_column(JSONB)

    # Match context (denormalized for fast access)
    team_yes: Mapped[Optional[str]] = mapped_column(String(100))
    team_no: Mapped[Optional[str]] = mapped_column(String(100))
    current_side: Mapped[Optional[str]] = mapped_column(String(3))  # 'YES' or 'NO'

    # Status
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)

    # Timestamps
    stage_entered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        # Unique constraint: one state per strategy/market
        Index(
            "ux_csgo_strategy_market",
            strategy_name, market_id,
            unique=True,
        ),
        # Partial index for fast active state queries
        Index(
            "ix_csgo_sms_active",
            strategy_name,
            postgresql_where=(is_active == True),
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<CSGOStrategyMarketState(strategy={self.strategy_name}, "
            f"market={self.market_id}, stage={self.stage})>"
        )
