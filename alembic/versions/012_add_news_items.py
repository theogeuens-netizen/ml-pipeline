"""Add news_items table for news data collection.

Stores news articles from Marketaux API (crypto-focused).
GDELT data is accessed directly via BigQuery.

Revision ID: 012
Revises: 011
Create Date: 2024-12-26

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


# revision identifiers, used by Alembic.
revision: str = '012'
down_revision: Union[str, None] = '011'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create news_items table
    op.create_table(
        'news_items',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('source', sa.String(50), nullable=False),  # "marketaux", "gdelt"
        sa.Column('source_id', sa.String(255), nullable=True),  # Dedupe key
        sa.Column('title', sa.Text(), nullable=False),
        sa.Column('snippet', sa.Text(), nullable=True),
        sa.Column('url', sa.Text(), nullable=True),
        sa.Column('published_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('fetched_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),

        # Sentiment
        sa.Column('sentiment_score', sa.Numeric(10, 6), nullable=True),

        # Categorization
        sa.Column('category', sa.String(50), nullable=True),
        sa.Column('symbols', JSONB, nullable=True),  # ["BTCUSD", "ETHUSD"]
        sa.Column('entities', JSONB, nullable=True),  # From API entity extraction

        # Raw response for future use
        sa.Column('raw_response', JSONB, nullable=True),

        sa.PrimaryKeyConstraint('id'),
    )

    # Create unique constraint on source_id for deduplication
    op.create_index(
        'idx_news_items_source_id',
        'news_items',
        ['source_id'],
        unique=True,
        postgresql_where=sa.text('source_id IS NOT NULL'),
    )

    # Create indexes for common queries
    op.create_index('idx_news_items_source', 'news_items', ['source'])
    op.create_index('idx_news_items_published_at', 'news_items', ['published_at'])
    op.create_index('idx_news_items_category', 'news_items', ['category'])

    # Composite index for time-range queries by source
    op.create_index(
        'idx_news_items_source_published',
        'news_items',
        ['source', 'published_at'],
    )


def downgrade() -> None:
    # Drop indexes
    op.drop_index('idx_news_items_source_published', table_name='news_items')
    op.drop_index('idx_news_items_category', table_name='news_items')
    op.drop_index('idx_news_items_published_at', table_name='news_items')
    op.drop_index('idx_news_items_source', table_name='news_items')
    op.drop_index('idx_news_items_source_id', table_name='news_items')

    # Drop table
    op.drop_table('news_items')
