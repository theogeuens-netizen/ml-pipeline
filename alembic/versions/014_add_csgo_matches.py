"""Add CS:GO matches table for real-time trading pipeline.

Revision ID: 014
Revises: 013
Create Date: 2024-12-27

Tables:
- csgo_matches: CS:GO market metadata with game start times from Gamma API
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


# revision identifiers, used by Alembic.
revision: str = "014"
down_revision: Union[str, None] = "013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create csgo_matches table
    op.create_table(
        "csgo_matches",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("market_id", sa.Integer(), sa.ForeignKey("markets.id"), nullable=False),
        sa.Column("gamma_id", sa.Integer(), nullable=True),
        sa.Column("condition_id", sa.String(100), nullable=False, unique=True),
        # Team names from Gamma API outcomes field
        sa.Column("team_yes", sa.String(100), nullable=True),
        sa.Column("team_no", sa.String(100), nullable=True),
        # Game timing
        sa.Column("game_start_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("game_start_override", sa.Boolean(), nullable=False, default=False),
        sa.Column("end_date", sa.DateTime(timezone=True), nullable=True),
        # Match metadata
        sa.Column("tournament", sa.String(255), nullable=True),
        sa.Column("format", sa.String(20), nullable=True),  # BO1, BO3, BO5
        sa.Column("market_type", sa.String(50), nullable=True),  # moneyline, child_moneyline
        sa.Column("group_item_title", sa.String(100), nullable=True),  # Match Winner, Map 1 Winner
        sa.Column("game_id", sa.String(50), nullable=True),  # External reference
        # State
        sa.Column("subscribed", sa.Boolean(), nullable=False, default=False),
        # Full Gamma API response
        sa.Column("gamma_data", JSONB, nullable=True),
        # Timestamps
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
            nullable=False,
        ),
    )

    # Indexes for lookups
    op.create_index("ix_csgo_matches_market_id", "csgo_matches", ["market_id"])
    op.create_index("ix_csgo_matches_gamma_id", "csgo_matches", ["gamma_id"])
    op.create_index("ix_csgo_matches_condition_id", "csgo_matches", ["condition_id"])
    op.create_index("ix_csgo_matches_game_start_time", "csgo_matches", ["game_start_time"])
    op.create_index("ix_csgo_matches_team_yes", "csgo_matches", ["team_yes"])
    op.create_index("ix_csgo_matches_team_no", "csgo_matches", ["team_no"])
    # Composite index for finding upcoming matches
    op.create_index(
        "ix_csgo_matches_upcoming",
        "csgo_matches",
        ["game_start_time", "subscribed"],
    )


def downgrade() -> None:
    op.drop_index("ix_csgo_matches_upcoming", table_name="csgo_matches")
    op.drop_index("ix_csgo_matches_team_no", table_name="csgo_matches")
    op.drop_index("ix_csgo_matches_team_yes", table_name="csgo_matches")
    op.drop_index("ix_csgo_matches_game_start_time", table_name="csgo_matches")
    op.drop_index("ix_csgo_matches_condition_id", table_name="csgo_matches")
    op.drop_index("ix_csgo_matches_gamma_id", table_name="csgo_matches")
    op.drop_index("ix_csgo_matches_market_id", table_name="csgo_matches")
    op.drop_table("csgo_matches")
