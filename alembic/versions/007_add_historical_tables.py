"""Add historical_markets and historical_price_snapshots tables for backtesting.

These tables are separate from polymarket-ml's operational data and are used
only for backtesting, not for XGBoost training.

Revision ID: 007
Revises: 006
Create Date: 2024-12-21

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '007'
down_revision: Union[str, None] = '006'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create historical_markets table
    op.create_table(
        'historical_markets',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('external_id', sa.String(100), nullable=False),
        sa.Column('question', sa.Text(), nullable=True),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('close_date', sa.DateTime(timezone=True), nullable=True),

        # Categories
        sa.Column('macro_category', sa.String(50), nullable=True),
        sa.Column('micro_category', sa.String(50), nullable=True),

        # Market metrics
        sa.Column('volume', sa.Numeric(20, 2), nullable=True),
        sa.Column('liquidity', sa.Numeric(20, 2), nullable=True),

        # Resolution data (critical for backtesting)
        sa.Column('resolution_status', sa.String(20), nullable=True),
        sa.Column('resolved_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('winner', sa.String(50), nullable=True),
        sa.Column('resolved_early', sa.Boolean(), nullable=True),

        # Metadata
        sa.Column('platform', sa.String(20), server_default='polymarket', nullable=False),
        sa.Column('imported_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),

        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('external_id'),
    )

    # Create indexes for historical_markets
    op.create_index('idx_hist_markets_external_id', 'historical_markets', ['external_id'])
    op.create_index('idx_hist_markets_close_date', 'historical_markets', ['close_date'])
    op.create_index('idx_hist_markets_macro_category', 'historical_markets', ['macro_category'])
    op.create_index('idx_hist_markets_micro_category', 'historical_markets', ['micro_category'])
    op.create_index('idx_hist_markets_resolution_status', 'historical_markets', ['resolution_status'])

    # Create historical_price_snapshots table
    op.create_table(
        'historical_price_snapshots',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('market_id', sa.Integer(), nullable=False),
        sa.Column('timestamp', sa.DateTime(timezone=True), nullable=False),

        # OHLC prices (0-1 scale for Polymarket)
        sa.Column('price', sa.Numeric(10, 6), nullable=True),
        sa.Column('open_price', sa.Numeric(10, 6), nullable=True),
        sa.Column('high_price', sa.Numeric(10, 6), nullable=True),
        sa.Column('low_price', sa.Numeric(10, 6), nullable=True),

        # Bid/Ask
        sa.Column('bid_price', sa.Numeric(10, 6), nullable=True),
        sa.Column('ask_price', sa.Numeric(10, 6), nullable=True),

        # Volume
        sa.Column('volume', sa.Numeric(20, 2), nullable=True),

        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['market_id'], ['historical_markets.id'], ondelete='CASCADE'),
    )

    # Create indexes for historical_price_snapshots
    op.create_index('idx_hist_snapshots_market_id', 'historical_price_snapshots', ['market_id'])
    op.create_index('idx_hist_snapshots_timestamp', 'historical_price_snapshots', ['timestamp'])
    op.create_index('idx_hist_snapshots_market_ts', 'historical_price_snapshots', ['market_id', 'timestamp'])


def downgrade() -> None:
    # Drop indexes first
    op.drop_index('idx_hist_snapshots_market_ts', table_name='historical_price_snapshots')
    op.drop_index('idx_hist_snapshots_timestamp', table_name='historical_price_snapshots')
    op.drop_index('idx_hist_snapshots_market_id', table_name='historical_price_snapshots')

    op.drop_index('idx_hist_markets_resolution_status', table_name='historical_markets')
    op.drop_index('idx_hist_markets_micro_category', table_name='historical_markets')
    op.drop_index('idx_hist_markets_macro_category', table_name='historical_markets')
    op.drop_index('idx_hist_markets_close_date', table_name='historical_markets')
    op.drop_index('idx_hist_markets_external_id', table_name='historical_markets')

    # Drop tables
    op.drop_table('historical_price_snapshots')
    op.drop_table('historical_markets')
