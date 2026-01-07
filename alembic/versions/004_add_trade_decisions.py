"""Add trade_decisions table for audit trail

Revision ID: 004
Revises: 003
Create Date: 2024-12-19

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

# revision identifiers, used by Alembic.
revision: str = "004"
down_revision: Union[str, None] = "003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create trade_decisions table
    op.create_table(
        "trade_decisions",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        # Strategy identification
        sa.Column("strategy_name", sa.String(100), nullable=False),
        sa.Column("strategy_sha", sa.String(20), nullable=False),
        # Market identification
        sa.Column("market_id", sa.BigInteger(), nullable=False),
        sa.Column("condition_id", sa.String(100), nullable=False),
        # What the strategy saw (for replay)
        sa.Column("market_snapshot", JSONB(), nullable=True),
        sa.Column("decision_inputs", JSONB(), nullable=True),
        # The decision made
        sa.Column("signal_side", sa.String(4), nullable=False),
        sa.Column("signal_reason", sa.Text(), nullable=False),
        sa.Column("signal_edge", sa.Numeric(10, 6), nullable=True),
        sa.Column("signal_size_usd", sa.Numeric(20, 2), nullable=True),
        # Outcome
        sa.Column("executed", sa.Boolean(), default=False, nullable=False),
        sa.Column("rejected_reason", sa.Text(), nullable=True),
        sa.Column("execution_price", sa.Numeric(10, 6), nullable=True),
        sa.Column("position_id", sa.BigInteger(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    # Create indexes for efficient queries
    op.create_index("ix_trade_decisions_timestamp", "trade_decisions", ["timestamp"])
    op.create_index("ix_trade_decisions_strategy_name", "trade_decisions", ["strategy_name"])
    op.create_index("ix_trade_decisions_market_id", "trade_decisions", ["market_id"])


def downgrade() -> None:
    op.drop_index("ix_trade_decisions_market_id", table_name="trade_decisions")
    op.drop_index("ix_trade_decisions_strategy_name", table_name="trade_decisions")
    op.drop_index("ix_trade_decisions_timestamp", table_name="trade_decisions")
    op.drop_table("trade_decisions")
