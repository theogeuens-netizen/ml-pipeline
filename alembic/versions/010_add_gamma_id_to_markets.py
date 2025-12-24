"""Add gamma_id column to markets table for reliable API lookups

Revision ID: 010
Revises: 009
Create Date: 2024-12-23

The Gamma API numeric ID (e.g., 978832) is more reliable for lookups
than condition_id, especially for closed/resolved markets.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "010"
down_revision: Union[str, None] = "009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "markets",
        sa.Column("gamma_id", sa.Integer(), nullable=True),
    )
    op.create_index("ix_markets_gamma_id", "markets", ["gamma_id"])


def downgrade() -> None:
    op.drop_index("ix_markets_gamma_id", table_name="markets")
    op.drop_column("markets", "gamma_id")
