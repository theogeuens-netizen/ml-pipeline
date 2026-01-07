"""Add best_bid and best_ask columns to csgo_matches

Revision ID: 020_best_bid_ask
Revises: 019_map_context
Create Date: 2026-01-06

Stores actual bid/ask from CLOB /price endpoint.
Spread is now calculated as ask - bid (the simple and correct way).

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers
revision = '020_best_bid_ask'
down_revision = '019_map_context'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add best_bid column (YES token best bid from CLOB)
    op.add_column(
        'csgo_matches',
        sa.Column('best_bid', sa.Numeric(10, 6), nullable=True)
    )

    # Add best_ask column (YES token best ask from CLOB)
    op.add_column(
        'csgo_matches',
        sa.Column('best_ask', sa.Numeric(10, 6), nullable=True)
    )


def downgrade() -> None:
    op.drop_column('csgo_matches', 'best_ask')
    op.drop_column('csgo_matches', 'best_bid')
