"""Add tier_transitions table for monitoring

Revision ID: 003
Revises: 902d38f5fe96
Create Date: 2024-12-19

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "003"
down_revision: Union[str, None] = "002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create tier_transitions table
    op.create_table(
        "tier_transitions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("market_id", sa.Integer(), nullable=False),
        sa.Column("condition_id", sa.String(100), nullable=False),
        sa.Column("market_slug", sa.String(255), nullable=True),
        sa.Column("from_tier", sa.SmallInteger(), nullable=False),
        sa.Column("to_tier", sa.SmallInteger(), nullable=False),
        sa.Column("transitioned_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("hours_to_close", sa.Numeric(10, 4), nullable=True),
        sa.Column("reason", sa.String(50), nullable=True),
        sa.ForeignKeyConstraint(["market_id"], ["markets.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    # Create indexes for efficient queries
    op.create_index("ix_tier_transitions_market_id", "tier_transitions", ["market_id"])
    op.create_index("ix_tier_transitions_transitioned_at", "tier_transitions", ["transitioned_at"])


def downgrade() -> None:
    op.drop_index("ix_tier_transitions_transitioned_at", table_name="tier_transitions")
    op.drop_index("ix_tier_transitions_market_id", table_name="tier_transitions")
    op.drop_table("tier_transitions")
