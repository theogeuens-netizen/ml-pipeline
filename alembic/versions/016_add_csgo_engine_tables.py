"""Add CSGO engine isolated tables.

Revision ID: 016_csgo_engine
Revises: 015_add_csgo_price_ticks
Create Date: 2024-12-28

Creates isolated tables for the CSGO real-time trading engine:
- csgo_positions: Individual positions (YES or NO token)
- csgo_position_legs: Audit trail for entries/exits
- csgo_spreads: Linked YES+NO positions
- csgo_trades: Execution records
- csgo_strategy_state: Per-strategy capital and performance
- csgo_strategy_market_state: Per-market state for multi-stage strategies
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "016_csgo_engine"
down_revision = "015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create csgo_strategy_state first (no dependencies)
    op.create_table(
        "csgo_strategy_state",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("strategy_name", sa.String(50), unique=True, nullable=False),
        # Capital
        sa.Column("allocated_usd", sa.Numeric(20, 2), server_default="400"),
        sa.Column("available_usd", sa.Numeric(20, 2), server_default="400"),
        # Performance
        sa.Column("total_realized_pnl", sa.Numeric(20, 2), server_default="0"),
        sa.Column("total_unrealized_pnl", sa.Numeric(20, 2), server_default="0"),
        sa.Column("trade_count", sa.Integer(), server_default="0"),
        sa.Column("win_count", sa.Integer(), server_default="0"),
        sa.Column("loss_count", sa.Integer(), server_default="0"),
        # Risk metrics
        sa.Column("max_drawdown_usd", sa.Numeric(20, 2), server_default="0"),
        sa.Column("high_water_mark", sa.Numeric(20, 2), server_default="400"),
        # State
        sa.Column("is_active", sa.Boolean(), server_default="true"),
        sa.Column("last_trade_at", sa.DateTime(timezone=True)),
        # Timestamps
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    # Create csgo_strategy_market_state (no dependencies)
    op.create_table(
        "csgo_strategy_market_state",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("strategy_name", sa.String(50), nullable=False, index=True),
        sa.Column("market_id", sa.Integer(), nullable=False),
        sa.Column("condition_id", sa.String(100), nullable=False),
        # Strategy stage
        sa.Column("stage", sa.String(50), nullable=False, server_default="WAITING"),
        # Price tracking
        sa.Column("entry_price", sa.Numeric(10, 6)),
        sa.Column("switch_price", sa.Numeric(10, 6)),
        sa.Column("exit_price", sa.Numeric(10, 6)),
        sa.Column("high_water_mark", sa.Numeric(10, 6)),
        sa.Column("low_water_mark", sa.Numeric(10, 6)),
        # Counters
        sa.Column("switches_count", sa.Integer(), server_default="0"),
        sa.Column("reentries_count", sa.Integer(), server_default="0"),
        # Flexible state
        sa.Column("custom_state", postgresql.JSONB()),
        # Match context
        sa.Column("team_yes", sa.String(100)),
        sa.Column("team_no", sa.String(100)),
        sa.Column("current_side", sa.String(3)),
        # Status
        sa.Column("is_active", sa.Boolean(), server_default="true"),
        # Timestamps
        sa.Column("stage_entered_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    # Unique constraint on strategy/market
    op.create_index(
        "ux_csgo_strategy_market",
        "csgo_strategy_market_state",
        ["strategy_name", "market_id"],
        unique=True,
    )

    # Partial index for active states
    op.execute("""
        CREATE INDEX ix_csgo_sms_active
        ON csgo_strategy_market_state (strategy_name)
        WHERE is_active = TRUE
    """)

    # Create csgo_spreads (referenced by csgo_positions)
    op.create_table(
        "csgo_spreads",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("strategy_name", sa.String(50), nullable=False, index=True),
        sa.Column("market_id", sa.Integer(), nullable=False, index=True),
        sa.Column("condition_id", sa.String(100), nullable=False),
        sa.Column("spread_type", sa.String(20), nullable=False),
        # Linked positions (set after positions created)
        sa.Column("yes_position_id", sa.BigInteger()),
        sa.Column("no_position_id", sa.BigInteger()),
        # Aggregate tracking
        sa.Column("total_cost_basis", sa.Numeric(20, 2), server_default="0"),
        sa.Column("total_realized_pnl", sa.Numeric(20, 2), server_default="0"),
        sa.Column("total_unrealized_pnl", sa.Numeric(20, 2), server_default="0"),
        # Match context
        sa.Column("team_yes", sa.String(100)),
        sa.Column("team_no", sa.String(100)),
        sa.Column("entry_yes_price", sa.Numeric(10, 6)),
        # Status
        sa.Column("status", sa.String(20), server_default="open", index=True),
        # Timestamps
        sa.Column("opened_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("closed_at", sa.DateTime(timezone=True)),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    # Create csgo_positions (references csgo_spreads)
    op.create_table(
        "csgo_positions",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("strategy_name", sa.String(50), nullable=False, index=True),
        sa.Column("market_id", sa.Integer(), nullable=False, index=True),
        sa.Column("condition_id", sa.String(100), nullable=False),
        # Token info
        sa.Column("token_id", sa.String(100), nullable=False),
        sa.Column("token_type", sa.String(3), nullable=False),
        sa.Column("side", sa.String(4), nullable=False),
        # Size tracking
        sa.Column("initial_shares", sa.Numeric(20, 6), nullable=False),
        sa.Column("remaining_shares", sa.Numeric(20, 6), nullable=False),
        sa.Column("avg_entry_price", sa.Numeric(10, 6), nullable=False),
        sa.Column("cost_basis", sa.Numeric(20, 2), nullable=False),
        # Current state
        sa.Column("current_price", sa.Numeric(10, 6)),
        sa.Column("unrealized_pnl", sa.Numeric(20, 2)),
        sa.Column("realized_pnl", sa.Numeric(20, 2), server_default="0"),
        # Spread linking
        sa.Column("spread_id", sa.BigInteger(), sa.ForeignKey("csgo_spreads.id"), index=True),
        # Match context
        sa.Column("team_yes", sa.String(100)),
        sa.Column("team_no", sa.String(100)),
        sa.Column("game_start_time", sa.DateTime(timezone=True)),
        sa.Column("format", sa.String(10)),
        # Status
        sa.Column("status", sa.String(20), server_default="open", index=True),
        sa.Column("close_reason", sa.String(50)),
        # Timestamps
        sa.Column("opened_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("closed_at", sa.DateTime(timezone=True)),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    # Unique constraint on open positions
    op.execute("""
        CREATE UNIQUE INDEX ux_csgo_pos_strategy_market_token
        ON csgo_positions (strategy_name, market_id, token_id)
        WHERE status = 'open'
    """)

    # Create csgo_position_legs (references csgo_positions)
    op.create_table(
        "csgo_position_legs",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("position_id", sa.BigInteger(), sa.ForeignKey("csgo_positions.id"), nullable=False, index=True),
        # Leg details
        sa.Column("leg_type", sa.String(20), nullable=False),
        sa.Column("shares_delta", sa.Numeric(20, 6), nullable=False),
        sa.Column("price", sa.Numeric(10, 6), nullable=False),
        sa.Column("cost_delta", sa.Numeric(20, 2), nullable=False),
        sa.Column("realized_pnl", sa.Numeric(20, 2)),
        # Trigger context
        sa.Column("trigger_price", sa.Numeric(10, 6)),
        sa.Column("trigger_reason", sa.String(100)),
        # Timestamps
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    # Create csgo_trades (references csgo_positions and csgo_position_legs)
    op.create_table(
        "csgo_trades",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("position_id", sa.BigInteger(), sa.ForeignKey("csgo_positions.id"), nullable=False, index=True),
        sa.Column("leg_id", sa.BigInteger(), sa.ForeignKey("csgo_position_legs.id")),
        # Execution details
        sa.Column("token_id", sa.String(100), nullable=False),
        sa.Column("side", sa.String(4), nullable=False),
        sa.Column("shares", sa.Numeric(20, 6), nullable=False),
        sa.Column("price", sa.Numeric(10, 6), nullable=False),
        sa.Column("cost_usd", sa.Numeric(20, 2), nullable=False),
        # Orderbook state
        sa.Column("best_bid", sa.Numeric(10, 6)),
        sa.Column("best_ask", sa.Numeric(10, 6)),
        sa.Column("spread", sa.Numeric(10, 6)),
        sa.Column("slippage", sa.Numeric(10, 6)),
        # Context
        sa.Column("trigger_tick_id", sa.String(50)),
        # Timestamps
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )


def downgrade() -> None:
    # Drop in reverse order of dependencies
    op.drop_table("csgo_trades")
    op.drop_table("csgo_position_legs")
    op.drop_index("ux_csgo_pos_strategy_market_token", table_name="csgo_positions")
    op.drop_table("csgo_positions")
    op.drop_table("csgo_spreads")
    op.drop_index("ix_csgo_sms_active", table_name="csgo_strategy_market_state")
    op.drop_index("ux_csgo_strategy_market", table_name="csgo_strategy_market_state")
    op.drop_table("csgo_strategy_market_state")
    op.drop_table("csgo_strategy_state")
