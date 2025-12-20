"""Add category taxonomy columns (L1, L2, L3) to markets table

Revision ID: 005
Revises: 004
Create Date: 2024-12-19

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "005"
down_revision: Union[str, None] = "004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add category taxonomy columns
    op.add_column("markets", sa.Column("category_l1", sa.String(50), nullable=True))
    op.add_column("markets", sa.Column("category_l2", sa.String(50), nullable=True))
    op.add_column("markets", sa.Column("category_l3", sa.String(50), nullable=True))
    op.add_column(
        "markets",
        sa.Column("categorized_at", sa.DateTime(timezone=True), nullable=True),
    )

    # Create indexes for efficient filtering by category
    op.create_index("ix_markets_category_l1", "markets", ["category_l1"])
    op.create_index("ix_markets_category_l2", "markets", ["category_l2"])
    op.create_index("ix_markets_category_l3", "markets", ["category_l3"])

    # Composite index for common query patterns (e.g., L1 + L2 filtering)
    op.create_index("ix_markets_category_l1_l2", "markets", ["category_l1", "category_l2"])


def downgrade() -> None:
    # Drop indexes
    op.drop_index("ix_markets_category_l1_l2", table_name="markets")
    op.drop_index("ix_markets_category_l3", table_name="markets")
    op.drop_index("ix_markets_category_l2", table_name="markets")
    op.drop_index("ix_markets_category_l1", table_name="markets")

    # Drop columns
    op.drop_column("markets", "categorized_at")
    op.drop_column("markets", "category_l3")
    op.drop_column("markets", "category_l2")
    op.drop_column("markets", "category_l1")
