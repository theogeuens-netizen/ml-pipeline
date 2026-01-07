"""Add lifecycle and market data fields to csgo_matches.

Revision ID: 018_csgo_lifecycle
Revises: 017_perf_indexes
Create Date: 2024-12-29

Makes CSGO pipeline independent from main markets table for lifecycle tracking.
Adds:
- Market lifecycle: closed, resolved, closed_at, accepting_orders, outcome
- Market data: yes_price, no_price, spread, volume_total, volume_24h, liquidity
- Tracking: last_status_check
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "018_csgo_lifecycle"
down_revision = "017_perf_indexes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Market lifecycle fields
    op.add_column(
        "csgo_matches",
        sa.Column("closed", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.add_column(
        "csgo_matches",
        sa.Column("resolved", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.add_column(
        "csgo_matches",
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "csgo_matches",
        sa.Column("accepting_orders", sa.Boolean(), nullable=False, server_default="true"),
    )
    op.add_column(
        "csgo_matches",
        sa.Column("outcome", sa.String(100), nullable=True),
    )
    op.add_column(
        "csgo_matches",
        sa.Column("last_status_check", sa.DateTime(timezone=True), nullable=True),
    )

    # Market data fields
    op.add_column(
        "csgo_matches",
        sa.Column("yes_price", sa.Numeric(10, 6), nullable=True),
    )
    op.add_column(
        "csgo_matches",
        sa.Column("no_price", sa.Numeric(10, 6), nullable=True),
    )
    op.add_column(
        "csgo_matches",
        sa.Column("spread", sa.Numeric(10, 6), nullable=True),
    )
    op.add_column(
        "csgo_matches",
        sa.Column("volume_total", sa.Numeric(20, 2), nullable=True),
    )
    op.add_column(
        "csgo_matches",
        sa.Column("volume_24h", sa.Numeric(20, 2), nullable=True),
    )
    op.add_column(
        "csgo_matches",
        sa.Column("liquidity", sa.Numeric(20, 2), nullable=True),
    )

    # Indexes for efficient queries
    op.create_index("ix_csgo_matches_closed", "csgo_matches", ["closed"])
    op.create_index("ix_csgo_matches_resolved", "csgo_matches", ["resolved"])

    # Composite index for active in-play markets query
    op.create_index(
        "ix_csgo_matches_active_inplay",
        "csgo_matches",
        ["game_start_time", "closed", "resolved"],
        postgresql_where=sa.text("closed = false AND resolved = false"),
    )


def downgrade() -> None:
    op.drop_index("ix_csgo_matches_active_inplay", table_name="csgo_matches")
    op.drop_index("ix_csgo_matches_resolved", table_name="csgo_matches")
    op.drop_index("ix_csgo_matches_closed", table_name="csgo_matches")

    op.drop_column("csgo_matches", "liquidity")
    op.drop_column("csgo_matches", "volume_24h")
    op.drop_column("csgo_matches", "volume_total")
    op.drop_column("csgo_matches", "spread")
    op.drop_column("csgo_matches", "no_price")
    op.drop_column("csgo_matches", "yes_price")
    op.drop_column("csgo_matches", "last_status_check")
    op.drop_column("csgo_matches", "outcome")
    op.drop_column("csgo_matches", "accepting_orders")
    op.drop_column("csgo_matches", "closed_at")
    op.drop_column("csgo_matches", "resolved")
    op.drop_column("csgo_matches", "closed")
