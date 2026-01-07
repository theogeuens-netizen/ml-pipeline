"""Add composite indexes for monitoring endpoint performance.

Revision ID: 017_perf_indexes
Revises: 016_csgo_engine
Create Date: 2024-12-29

Adds composite indexes to speed up common monitoring queries:
- snapshots(timestamp, market_id) - time-range queries
- task_runs(started_at, status) - task aggregation queries
- markets(active, tier, resolved, yes_token_id) - WebSocket filtering
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "017_perf_indexes"
down_revision = "016_csgo_engine"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Snapshots time-range queries (most impactful - scans 18M+ rows)
    op.create_index(
        "ix_snapshots_timestamp_market",
        "snapshots",
        [sa.text("timestamp DESC"), "market_id"],
    )

    # Task aggregation queries
    op.create_index(
        "ix_taskruns_started_status",
        "task_runs",
        [sa.text("started_at DESC"), "status"],
    )

    # Market WebSocket subscription filtering (partial index)
    op.create_index(
        "ix_markets_ws_subscription",
        "markets",
        ["tier", "resolved", "yes_token_id"],
        postgresql_where=sa.text("active = true"),
    )


def downgrade() -> None:
    op.drop_index("ix_markets_ws_subscription", table_name="markets")
    op.drop_index("ix_taskruns_started_status", table_name="task_runs")
    op.drop_index("ix_snapshots_timestamp_market", table_name="snapshots")
