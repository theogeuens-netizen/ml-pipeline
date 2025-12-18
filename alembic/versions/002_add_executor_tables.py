"""Add executor tables for trading

Revision ID: 002
Revises: 001
Create Date: 2024-12-18

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # === SIGNALS TABLE ===
    op.create_table(
        "signals",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        # Source
        sa.Column("strategy_name", sa.String(50), nullable=False),
        sa.Column("market_id", sa.Integer(), nullable=False),
        # Signal details
        sa.Column("token_id", sa.String(100), nullable=False),
        sa.Column("side", sa.String(4), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("edge", sa.Numeric(10, 6), nullable=True),
        sa.Column("confidence", sa.Numeric(10, 6), nullable=True),
        # Market state at signal time
        sa.Column("price_at_signal", sa.Numeric(10, 6), nullable=False),
        sa.Column("best_bid", sa.Numeric(10, 6), nullable=True),
        sa.Column("best_ask", sa.Numeric(10, 6), nullable=True),
        # Sizing
        sa.Column("suggested_size_usd", sa.Numeric(20, 2), nullable=True),
        # Status
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("status_reason", sa.Text(), nullable=True),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["market_id"], ["markets.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_signals_created_at", "signals", ["created_at"])
    op.create_index("ix_signals_strategy_name", "signals", ["strategy_name"])
    op.create_index("ix_signals_market_id", "signals", ["market_id"])
    op.create_index("ix_signals_status", "signals", ["status"])

    # === EXECUTOR_ORDERS TABLE ===
    op.create_table(
        "executor_orders",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        # Link to signal
        sa.Column("signal_id", sa.BigInteger(), nullable=False),
        # Order details
        sa.Column("is_paper", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("token_id", sa.String(100), nullable=False),
        sa.Column("side", sa.String(4), nullable=False),
        sa.Column("order_type", sa.String(20), nullable=False),
        # Pricing
        sa.Column("limit_price", sa.Numeric(10, 6), nullable=True),
        sa.Column("executed_price", sa.Numeric(10, 6), nullable=True),
        # Size
        sa.Column("size_usd", sa.Numeric(20, 2), nullable=False),
        sa.Column("size_shares", sa.Numeric(20, 6), nullable=True),
        sa.Column("filled_shares", sa.Numeric(20, 6), nullable=False, server_default="0"),
        # External tracking
        sa.Column("polymarket_order_id", sa.String(100), nullable=True),
        # Status
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("status_message", sa.Text(), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("filled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["signal_id"], ["signals.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_executor_orders_created_at", "executor_orders", ["created_at"])
    op.create_index("ix_executor_orders_signal_id", "executor_orders", ["signal_id"])
    op.create_index("ix_executor_orders_status", "executor_orders", ["status"])

    # === POSITIONS TABLE ===
    op.create_table(
        "positions",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        # Source
        sa.Column("is_paper", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("strategy_name", sa.String(50), nullable=False),
        sa.Column("market_id", sa.Integer(), nullable=False),
        # Position details
        sa.Column("token_id", sa.String(100), nullable=False),
        sa.Column("side", sa.String(4), nullable=False),
        # Entry
        sa.Column("entry_order_id", sa.BigInteger(), nullable=False),
        sa.Column("entry_price", sa.Numeric(10, 6), nullable=False),
        sa.Column("entry_time", sa.DateTime(timezone=True), nullable=False),
        # Size
        sa.Column("size_shares", sa.Numeric(20, 6), nullable=False),
        sa.Column("cost_basis", sa.Numeric(20, 2), nullable=False),
        # Current state
        sa.Column("current_price", sa.Numeric(10, 6), nullable=True),
        sa.Column("current_value", sa.Numeric(20, 2), nullable=True),
        sa.Column("unrealized_pnl", sa.Numeric(20, 2), nullable=False, server_default="0"),
        sa.Column("unrealized_pnl_pct", sa.Numeric(10, 6), nullable=False, server_default="0"),
        # Exit
        sa.Column("exit_price", sa.Numeric(10, 6), nullable=True),
        sa.Column("exit_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("realized_pnl", sa.Numeric(20, 2), nullable=False, server_default="0"),
        # Hedge tracking
        sa.Column("hedge_position_id", sa.BigInteger(), nullable=True),
        sa.Column("is_hedge", sa.Boolean(), nullable=False, server_default="false"),
        # Status
        sa.Column("status", sa.String(20), nullable=False, server_default="open"),
        sa.Column("close_reason", sa.String(50), nullable=True),
        # Timestamps
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["market_id"], ["markets.id"]),
        sa.ForeignKeyConstraint(["entry_order_id"], ["executor_orders.id"]),
        sa.ForeignKeyConstraint(["hedge_position_id"], ["positions.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_positions_created_at", "positions", ["created_at"])
    op.create_index("ix_positions_strategy_name", "positions", ["strategy_name"])
    op.create_index("ix_positions_market_id", "positions", ["market_id"])
    op.create_index("ix_positions_status", "positions", ["status"])

    # === EXECUTOR_TRADES TABLE ===
    op.create_table(
        "executor_trades",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        # Links
        sa.Column("order_id", sa.BigInteger(), nullable=False),
        sa.Column("position_id", sa.BigInteger(), nullable=True),
        # Trade details
        sa.Column("is_paper", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("price", sa.Numeric(10, 6), nullable=False),
        sa.Column("size_shares", sa.Numeric(20, 6), nullable=False),
        sa.Column("size_usd", sa.Numeric(20, 2), nullable=False),
        sa.Column("side", sa.String(4), nullable=False),
        # Fees
        sa.Column("fee_usd", sa.Numeric(20, 6), nullable=False, server_default="0"),
        # External tracking
        sa.Column("polymarket_trade_id", sa.String(100), nullable=True),
        sa.ForeignKeyConstraint(["order_id"], ["executor_orders.id"]),
        sa.ForeignKeyConstraint(["position_id"], ["positions.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_executor_trades_timestamp", "executor_trades", ["timestamp"])
    op.create_index("ix_executor_trades_order_id", "executor_trades", ["order_id"])
    op.create_index("ix_executor_trades_position_id", "executor_trades", ["position_id"])

    # === STRATEGY_STATES TABLE ===
    op.create_table(
        "strategy_states",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("strategy_name", sa.String(50), nullable=False),
        # Configuration
        sa.Column("config_json", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="false"),
        # Runtime state
        sa.Column("last_scan_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("markets_scanned", sa.Integer(), nullable=False, server_default="0"),
        # Statistics
        sa.Column("total_signals", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_trades", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("winning_trades", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("losing_trades", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_pnl", sa.Numeric(20, 2), nullable=False, server_default="0"),
        sa.Column("best_trade_pnl", sa.Numeric(20, 2), nullable=False, server_default="0"),
        sa.Column("worst_trade_pnl", sa.Numeric(20, 2), nullable=False, server_default="0"),
        sa.Column("stats_json", postgresql.JSONB(), nullable=False, server_default="{}"),
        # Timestamps
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_strategy_states_strategy_name", "strategy_states", ["strategy_name"], unique=True)

    # === PAPER_BALANCES TABLE ===
    op.create_table(
        "paper_balances",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("balance_usd", sa.Numeric(20, 2), nullable=False, server_default="10000"),
        sa.Column("starting_balance_usd", sa.Numeric(20, 2), nullable=False, server_default="10000"),
        sa.Column("high_water_mark", sa.Numeric(20, 2), nullable=False, server_default="10000"),
        sa.Column("low_water_mark", sa.Numeric(20, 2), nullable=False, server_default="10000"),
        sa.Column("total_pnl", sa.Numeric(20, 2), nullable=False, server_default="0"),
        # Timestamps
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("paper_balances")
    op.drop_table("strategy_states")
    op.drop_table("executor_trades")
    op.drop_table("positions")
    op.drop_table("executor_orders")
    op.drop_table("signals")
