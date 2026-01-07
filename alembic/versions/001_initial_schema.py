"""Initial schema with all tables

Revision ID: 001
Revises:
Create Date: 2024-12-17

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # === MARKETS TABLE ===
    op.create_table(
        "markets",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("condition_id", sa.String(100), nullable=False),
        sa.Column("slug", sa.String(255), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        # Event grouping
        sa.Column("event_id", sa.String(100), nullable=True),
        sa.Column("event_slug", sa.String(255), nullable=True),
        sa.Column("event_title", sa.String(500), nullable=True),
        # Token IDs
        sa.Column("yes_token_id", sa.String(100), nullable=True),
        sa.Column("no_token_id", sa.String(100), nullable=True),
        # Timing
        sa.Column("start_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("end_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        # Initial state
        sa.Column("initial_price", sa.Numeric(10, 6), nullable=True),
        sa.Column("initial_spread", sa.Numeric(10, 6), nullable=True),
        sa.Column("initial_volume", sa.Numeric(20, 2), nullable=True),
        sa.Column("initial_liquidity", sa.Numeric(20, 2), nullable=True),
        # Resolution
        sa.Column("resolved", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("outcome", sa.String(20), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        # Collection tracking
        sa.Column("tier", sa.SmallInteger(), nullable=False, server_default="0"),
        sa.Column("active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("tracking_started_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("last_snapshot_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("snapshot_count", sa.Integer(), nullable=False, server_default="0"),
        # Metadata
        sa.Column("category", sa.String(100), nullable=True),
        sa.Column("tags", postgresql.JSONB(), nullable=True),
        sa.Column("neg_risk", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("competitive", sa.Numeric(5, 4), nullable=True),
        sa.Column("enable_order_book", sa.Boolean(), nullable=False, server_default="true"),
        # Timestamps
        sa.Column("first_seen", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
    )

    # Markets indexes
    op.create_index("ix_markets_condition_id", "markets", ["condition_id"], unique=True)
    op.create_index("ix_markets_tier", "markets", ["tier"])
    op.create_index("ix_markets_active", "markets", ["active"])
    op.create_index("ix_markets_resolved", "markets", ["resolved"])
    op.create_index("ix_markets_end_date", "markets", ["end_date"])
    op.create_index("ix_markets_event_id", "markets", ["event_id"])
    op.create_index("ix_markets_tier_active", "markets", ["tier", "active"])

    # === SNAPSHOTS TABLE ===
    op.create_table(
        "snapshots",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("market_id", sa.Integer(), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("tier", sa.SmallInteger(), nullable=False),
        # Price fields
        sa.Column("price", sa.Numeric(10, 6), nullable=False),
        sa.Column("best_bid", sa.Numeric(10, 6), nullable=True),
        sa.Column("best_ask", sa.Numeric(10, 6), nullable=True),
        sa.Column("spread", sa.Numeric(10, 6), nullable=True),
        sa.Column("last_trade_price", sa.Numeric(10, 6), nullable=True),
        # Momentum fields
        sa.Column("price_change_1d", sa.Numeric(10, 6), nullable=True),
        sa.Column("price_change_1w", sa.Numeric(10, 6), nullable=True),
        sa.Column("price_change_1m", sa.Numeric(10, 6), nullable=True),
        # Volume fields
        sa.Column("volume_total", sa.Numeric(20, 2), nullable=True),
        sa.Column("volume_24h", sa.Numeric(20, 2), nullable=True),
        sa.Column("volume_1w", sa.Numeric(20, 2), nullable=True),
        sa.Column("liquidity", sa.Numeric(20, 2), nullable=True),
        # Orderbook depth
        sa.Column("bid_depth_5", sa.Numeric(20, 2), nullable=True),
        sa.Column("bid_depth_10", sa.Numeric(20, 2), nullable=True),
        sa.Column("bid_depth_20", sa.Numeric(20, 2), nullable=True),
        sa.Column("bid_depth_50", sa.Numeric(20, 2), nullable=True),
        sa.Column("ask_depth_5", sa.Numeric(20, 2), nullable=True),
        sa.Column("ask_depth_10", sa.Numeric(20, 2), nullable=True),
        sa.Column("ask_depth_20", sa.Numeric(20, 2), nullable=True),
        sa.Column("ask_depth_50", sa.Numeric(20, 2), nullable=True),
        # Orderbook derived
        sa.Column("bid_levels", sa.SmallInteger(), nullable=True),
        sa.Column("ask_levels", sa.SmallInteger(), nullable=True),
        sa.Column("book_imbalance", sa.Numeric(10, 6), nullable=True),
        sa.Column("bid_wall_price", sa.Numeric(10, 6), nullable=True),
        sa.Column("bid_wall_size", sa.Numeric(20, 2), nullable=True),
        sa.Column("ask_wall_price", sa.Numeric(10, 6), nullable=True),
        sa.Column("ask_wall_size", sa.Numeric(20, 2), nullable=True),
        # Trade flow
        sa.Column("trade_count_1h", sa.Integer(), nullable=True),
        sa.Column("buy_count_1h", sa.Integer(), nullable=True),
        sa.Column("sell_count_1h", sa.Integer(), nullable=True),
        sa.Column("volume_1h", sa.Numeric(20, 2), nullable=True),
        sa.Column("buy_volume_1h", sa.Numeric(20, 2), nullable=True),
        sa.Column("sell_volume_1h", sa.Numeric(20, 2), nullable=True),
        sa.Column("avg_trade_size_1h", sa.Numeric(20, 2), nullable=True),
        sa.Column("max_trade_size_1h", sa.Numeric(20, 2), nullable=True),
        sa.Column("vwap_1h", sa.Numeric(10, 6), nullable=True),
        # Whale metrics
        sa.Column("whale_count_1h", sa.Integer(), nullable=True),
        sa.Column("whale_volume_1h", sa.Numeric(20, 2), nullable=True),
        sa.Column("whale_buy_volume_1h", sa.Numeric(20, 2), nullable=True),
        sa.Column("whale_sell_volume_1h", sa.Numeric(20, 2), nullable=True),
        sa.Column("whale_net_flow_1h", sa.Numeric(20, 2), nullable=True),
        sa.Column("whale_buy_ratio_1h", sa.Numeric(10, 6), nullable=True),
        sa.Column("time_since_whale", sa.Integer(), nullable=True),
        sa.Column("pct_volume_from_whales", sa.Numeric(10, 6), nullable=True),
        # Context fields
        sa.Column("hours_to_close", sa.Numeric(10, 4), nullable=True),
        sa.Column("day_of_week", sa.SmallInteger(), nullable=True),
        sa.Column("hour_of_day", sa.SmallInteger(), nullable=True),
        sa.ForeignKeyConstraint(["market_id"], ["markets.id"]),
        sa.PrimaryKeyConstraint("id"),
    )

    # Snapshots indexes
    op.create_index("ix_snapshots_market_id", "snapshots", ["market_id"])
    op.create_index("ix_snapshots_timestamp", "snapshots", ["timestamp"])
    op.create_index("ix_snapshots_market_timestamp", "snapshots", ["market_id", "timestamp"])

    # === TRADES TABLE ===
    op.create_table(
        "trades",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("market_id", sa.Integer(), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("price", sa.Numeric(10, 6), nullable=False),
        sa.Column("size", sa.Numeric(20, 2), nullable=False),
        sa.Column("side", sa.String(4), nullable=False),
        sa.Column("whale_tier", sa.SmallInteger(), nullable=False, server_default="0"),
        sa.Column("best_bid", sa.Numeric(10, 6), nullable=True),
        sa.Column("best_ask", sa.Numeric(10, 6), nullable=True),
        sa.Column("mid_price", sa.Numeric(10, 6), nullable=True),
        sa.ForeignKeyConstraint(["market_id"], ["markets.id"]),
        sa.PrimaryKeyConstraint("id"),
    )

    # Trades indexes
    op.create_index("ix_trades_market_id", "trades", ["market_id"])
    op.create_index("ix_trades_timestamp", "trades", ["timestamp"])
    op.create_index("ix_trades_market_timestamp", "trades", ["market_id", "timestamp"])

    # === ORDERBOOK_SNAPSHOTS TABLE ===
    op.create_table(
        "orderbook_snapshots",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("market_id", sa.Integer(), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("bids", postgresql.JSONB(), nullable=False),
        sa.Column("asks", postgresql.JSONB(), nullable=False),
        sa.Column("total_bid_depth", sa.Numeric(20, 2), nullable=False),
        sa.Column("total_ask_depth", sa.Numeric(20, 2), nullable=False),
        sa.Column("num_bid_levels", sa.SmallInteger(), nullable=False),
        sa.Column("num_ask_levels", sa.SmallInteger(), nullable=False),
        sa.Column("largest_bid_price", sa.Numeric(10, 6), nullable=True),
        sa.Column("largest_bid_size", sa.Numeric(20, 2), nullable=True),
        sa.Column("largest_ask_price", sa.Numeric(10, 6), nullable=True),
        sa.Column("largest_ask_size", sa.Numeric(20, 2), nullable=True),
        sa.ForeignKeyConstraint(["market_id"], ["markets.id"]),
        sa.PrimaryKeyConstraint("id"),
    )

    # Orderbook snapshots indexes
    op.create_index("ix_orderbook_snapshots_market_id", "orderbook_snapshots", ["market_id"])
    op.create_index("ix_orderbook_snapshots_timestamp", "orderbook_snapshots", ["timestamp"])

    # === WHALE_EVENTS TABLE ===
    op.create_table(
        "whale_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("market_id", sa.Integer(), nullable=False),
        sa.Column("trade_id", sa.BigInteger(), nullable=True),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("price", sa.Numeric(10, 6), nullable=False),
        sa.Column("size", sa.Numeric(20, 2), nullable=False),
        sa.Column("side", sa.String(4), nullable=False),
        sa.Column("whale_tier", sa.SmallInteger(), nullable=False),
        sa.Column("price_before", sa.Numeric(10, 6), nullable=True),
        sa.Column("price_after_1m", sa.Numeric(10, 6), nullable=True),
        sa.Column("price_after_5m", sa.Numeric(10, 6), nullable=True),
        sa.Column("impact_1m", sa.Numeric(10, 6), nullable=True),
        sa.Column("impact_5m", sa.Numeric(10, 6), nullable=True),
        sa.ForeignKeyConstraint(["market_id"], ["markets.id"]),
        sa.ForeignKeyConstraint(["trade_id"], ["trades.id"]),
        sa.PrimaryKeyConstraint("id"),
    )

    # Whale events indexes
    op.create_index("ix_whale_events_market_id", "whale_events", ["market_id"])
    op.create_index("ix_whale_events_timestamp", "whale_events", ["timestamp"])

    # === TASK_RUNS TABLE ===
    op.create_table(
        "task_runs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("task_name", sa.String(100), nullable=False),
        sa.Column("task_id", sa.String(100), nullable=False),
        sa.Column("tier", sa.SmallInteger(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("markets_processed", sa.Integer(), nullable=True),
        sa.Column("rows_inserted", sa.Integer(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("error_traceback", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )

    # Task runs indexes
    op.create_index("ix_task_runs_task_name", "task_runs", ["task_name"])
    op.create_index("ix_task_runs_status", "task_runs", ["status"])


def downgrade() -> None:
    op.drop_table("task_runs")
    op.drop_table("whale_events")
    op.drop_table("orderbook_snapshots")
    op.drop_table("trades")
    op.drop_table("snapshots")
    op.drop_table("markets")
