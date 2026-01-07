"""Add CS:GO price ticks table for high-frequency price data.

Revision ID: 015
Revises: 014
Create Date: 2024-12-28

Tables:
- csgo_price_ticks: High-frequency price data from CSGO websocket
  Stores every trade, book update, and price change event
  Aggregated to 5-second bars for chart display
  Retained for 7 days, then cleaned up
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "015"
down_revision: Union[str, None] = "014"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create csgo_price_ticks table
    op.create_table(
        "csgo_price_ticks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("market_id", sa.Integer(), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("token_type", sa.String(3), nullable=False),  # YES or NO
        sa.Column("event_type", sa.String(20), nullable=False),  # trade, book, price_change
        # Price data
        sa.Column("price", sa.Numeric(10, 6), nullable=True),
        sa.Column("best_bid", sa.Numeric(10, 6), nullable=True),
        sa.Column("best_ask", sa.Numeric(10, 6), nullable=True),
        sa.Column("spread", sa.Numeric(10, 6), nullable=True),
        # Trade data (for trade events)
        sa.Column("trade_size", sa.Numeric(15, 6), nullable=True),
        sa.Column("trade_side", sa.String(4), nullable=True),  # BUY or SELL
        # Calculated metrics
        sa.Column("price_velocity_1m", sa.Numeric(10, 6), nullable=True),
    )

    # Index for time-range queries on a market (primary access pattern)
    op.create_index(
        "idx_csgo_ticks_market_time",
        "csgo_price_ticks",
        ["market_id", "timestamp"],
    )

    # Index for cleanup task (delete old ticks)
    op.create_index(
        "idx_csgo_ticks_timestamp",
        "csgo_price_ticks",
        ["timestamp"],
    )


def downgrade() -> None:
    op.drop_index("idx_csgo_ticks_timestamp", table_name="csgo_price_ticks")
    op.drop_index("idx_csgo_ticks_market_time", table_name="csgo_price_ticks")
    op.drop_table("csgo_price_ticks")
