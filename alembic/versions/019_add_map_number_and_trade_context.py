"""Add map_number to CSGOMatch and match context to trades

Revision ID: 019_map_context
Revises: 018_csgo_lifecycle
Create Date: 2025-12-29

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers
revision = '019_map_context'
down_revision = '018_csgo_lifecycle'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add map_number to csgo_matches
    op.add_column(
        'csgo_matches',
        sa.Column('map_number', sa.Integer(), nullable=True)
    )

    # Add match context to csgo_trades for audit trail
    op.add_column(
        'csgo_trades',
        sa.Column('team_yes', sa.String(100), nullable=True)
    )
    op.add_column(
        'csgo_trades',
        sa.Column('team_no', sa.String(100), nullable=True)
    )
    op.add_column(
        'csgo_trades',
        sa.Column('format', sa.String(10), nullable=True)
    )
    op.add_column(
        'csgo_trades',
        sa.Column('map_number', sa.Integer(), nullable=True)
    )
    op.add_column(
        'csgo_trades',
        sa.Column('game_start_time', sa.DateTime(timezone=True), nullable=True)
    )

    # Parse existing group_item_title to populate map_number
    # "Map 1 Winner" -> 1, "Map 2 Winner" -> 2, etc.
    op.execute("""
        UPDATE csgo_matches
        SET map_number = CAST(
            REGEXP_REPLACE(group_item_title, '.*Map ([0-9]+).*', '\\1')
            AS INTEGER
        )
        WHERE group_item_title LIKE '%Map %'
          AND group_item_title ~ 'Map [0-9]+'
    """)


def downgrade() -> None:
    op.drop_column('csgo_trades', 'game_start_time')
    op.drop_column('csgo_trades', 'map_number')
    op.drop_column('csgo_trades', 'format')
    op.drop_column('csgo_trades', 'team_no')
    op.drop_column('csgo_trades', 'team_yes')
    op.drop_column('csgo_matches', 'map_number')
