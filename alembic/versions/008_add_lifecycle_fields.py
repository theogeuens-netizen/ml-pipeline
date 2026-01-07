"""Add market lifecycle fields for trading status and UMA resolution tracking

Revision ID: 008
Revises: 007
Create Date: 2024-12-21

This migration adds fields to properly track:
1. Trading status (closed, accepting_orders) - separate from resolution
2. UMA resolution status (proposed, disputed, resolved, etc.)

These fields fix the gap where markets could be closed but not yet resolved,
which currently causes the scanner to generate signals for untradeable markets.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "008"
down_revision: Union[str, None] = "007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add trading status fields
    op.add_column(
        "markets",
        sa.Column("closed", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.add_column(
        "markets",
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "markets",
        sa.Column("accepting_orders", sa.Boolean(), nullable=False, server_default="true"),
    )
    op.add_column(
        "markets",
        sa.Column("accepting_orders_updated_at", sa.DateTime(timezone=True), nullable=True),
    )

    # Add UMA resolution status fields
    op.add_column(
        "markets",
        sa.Column("uma_resolution_status", sa.String(20), nullable=True),
    )
    op.add_column(
        "markets",
        sa.Column("uma_status_updated_at", sa.DateTime(timezone=True), nullable=True),
    )

    # Create indexes for efficient queries
    op.create_index("ix_markets_closed", "markets", ["closed"])
    op.create_index("ix_markets_accepting_orders", "markets", ["accepting_orders"])
    op.create_index("ix_markets_uma_resolution_status", "markets", ["uma_resolution_status"])


def downgrade() -> None:
    # Drop indexes
    op.drop_index("ix_markets_uma_resolution_status", table_name="markets")
    op.drop_index("ix_markets_accepting_orders", table_name="markets")
    op.drop_index("ix_markets_closed", table_name="markets")

    # Drop columns
    op.drop_column("markets", "uma_status_updated_at")
    op.drop_column("markets", "uma_resolution_status")
    op.drop_column("markets", "accepting_orders_updated_at")
    op.drop_column("markets", "accepting_orders")
    op.drop_column("markets", "closed_at")
    op.drop_column("markets", "closed")
