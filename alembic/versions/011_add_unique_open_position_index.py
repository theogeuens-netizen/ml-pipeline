"""add unique open position index per strategy/market

Revision ID: 011
Revises: 010
Create Date: 2024-03-24 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "011"
down_revision = "010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ux_positions_strategy_market_open",
        "positions",
        ["is_paper", "strategy_name", "market_id"],
        unique=True,
        postgresql_where=sa.text("status = 'open'"),
    )


def downgrade() -> None:
    op.drop_index("ux_positions_strategy_market_open", table_name="positions")
