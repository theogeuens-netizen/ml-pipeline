"""Add CS:GO team leaderboard and head-to-head tables.

Revision ID: 013
Revises: 012
Create Date: 2024-12-26

Tables:
- csgo_teams: Team stats from historical match data
- csgo_h2h: Head-to-head records between teams
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "013"
down_revision: Union[str, None] = "012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create csgo_teams table
    op.create_table(
        "csgo_teams",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("team_name", sa.String(100), nullable=False, unique=True),
        sa.Column("wins", sa.Integer(), nullable=False, default=0),
        sa.Column("losses", sa.Integer(), nullable=False, default=0),
        sa.Column("total_matches", sa.Integer(), nullable=False, default=0),
        sa.Column("win_rate_pct", sa.Numeric(5, 2), nullable=False),
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

    # Index for team name lookups
    op.create_index("ix_csgo_teams_team_name", "csgo_teams", ["team_name"])
    # Index for leaderboard sorting
    op.create_index("ix_csgo_teams_win_rate", "csgo_teams", ["win_rate_pct"])

    # Create csgo_h2h table (head-to-head records)
    op.create_table(
        "csgo_h2h",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("team1_name", sa.String(100), nullable=False),
        sa.Column("team2_name", sa.String(100), nullable=False),
        sa.Column("team1_wins", sa.Integer(), nullable=False, default=0),
        sa.Column("team2_wins", sa.Integer(), nullable=False, default=0),
        sa.Column("total_matches", sa.Integer(), nullable=False, default=0),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
            nullable=False,
        ),
        # Unique constraint on team pair (team1 always < team2 alphabetically)
        sa.UniqueConstraint("team1_name", "team2_name", name="uq_csgo_h2h_teams"),
    )

    # Indexes for H2H lookups
    op.create_index("ix_csgo_h2h_team1", "csgo_h2h", ["team1_name"])
    op.create_index("ix_csgo_h2h_team2", "csgo_h2h", ["team2_name"])


def downgrade() -> None:
    # Drop csgo_h2h
    op.drop_index("ix_csgo_h2h_team2", table_name="csgo_h2h")
    op.drop_index("ix_csgo_h2h_team1", table_name="csgo_h2h")
    op.drop_table("csgo_h2h")

    # Drop csgo_teams
    op.drop_index("ix_csgo_teams_win_rate", table_name="csgo_teams")
    op.drop_index("ix_csgo_teams_team_name", table_name="csgo_teams")
    op.drop_table("csgo_teams")
