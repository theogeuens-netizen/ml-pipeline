"""Add categorization rules tables and tracking columns

Revision ID: 006
Revises: 005
Create Date: 2024-12-20

This migration adds:
- categorization_rules table: Store rules in database (not hardcoded)
- rule_validations table: Track validation results for rule accuracy
- markets.categorization_method: Track how each market was categorized
- markets.matched_rule_id: Link to the rule that categorized the market
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


# revision identifiers, used by Alembic.
revision: str = "006"
down_revision: Union[str, None] = "005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create categorization_rules table
    op.create_table(
        "categorization_rules",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(100), unique=True, nullable=False),
        sa.Column("l1", sa.String(50), nullable=False),
        sa.Column("l2", sa.String(50), nullable=False),

        # Matching criteria
        sa.Column("keywords", JSONB, nullable=False),  # ["bitcoin", "btc"]
        sa.Column("negative_keywords", JSONB, server_default="[]"),  # Exclusions
        sa.Column("l3_patterns", JSONB, server_default="{}"),  # {"SPREAD": ["pattern"]}
        sa.Column("l3_default", sa.String(50), nullable=True),  # Fallback L3

        # Stats (updated by validation)
        sa.Column("times_matched", sa.Integer(), server_default="0"),
        sa.Column("times_validated", sa.Integer(), server_default="0"),
        sa.Column("times_correct", sa.Integer(), server_default="0"),

        # Meta
        sa.Column("enabled", sa.Boolean(), server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("notes", sa.Text(), nullable=True),
    )

    # Create rule_validations table
    op.create_table(
        "rule_validations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("market_id", sa.Integer(), sa.ForeignKey("markets.id"), nullable=False),
        sa.Column("rule_id", sa.Integer(), sa.ForeignKey("categorization_rules.id"), nullable=True),

        # What rule predicted
        sa.Column("rule_l1", sa.String(50), nullable=True),
        sa.Column("rule_l2", sa.String(50), nullable=True),
        sa.Column("rule_l3", sa.String(50), nullable=True),

        # Ground truth (from Claude or human)
        sa.Column("correct_l1", sa.String(50), nullable=True),
        sa.Column("correct_l2", sa.String(50), nullable=True),
        sa.Column("correct_l3", sa.String(50), nullable=True),

        # Result
        sa.Column("is_correct", sa.Boolean(), nullable=True),
        sa.Column("validated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("validated_by", sa.String(50), nullable=True),  # 'claude', 'human'
    )

    # Add categorization tracking columns to markets
    op.add_column(
        "markets",
        sa.Column("categorization_method", sa.String(20), nullable=True)
    )  # 'rule', 'claude', 'event'

    op.add_column(
        "markets",
        sa.Column(
            "matched_rule_id",
            sa.Integer(),
            sa.ForeignKey("categorization_rules.id"),
            nullable=True
        )
    )

    # Create indexes
    op.create_index("ix_categorization_rules_enabled", "categorization_rules", ["enabled"])
    op.create_index("ix_categorization_rules_l1_l2", "categorization_rules", ["l1", "l2"])
    op.create_index("ix_rule_validations_market_id", "rule_validations", ["market_id"])
    op.create_index("ix_rule_validations_rule_id", "rule_validations", ["rule_id"])
    op.create_index("ix_rule_validations_validated_at", "rule_validations", ["validated_at"])
    op.create_index("ix_markets_categorization_method", "markets", ["categorization_method"])


def downgrade() -> None:
    # Drop indexes
    op.drop_index("ix_markets_categorization_method", table_name="markets")
    op.drop_index("ix_rule_validations_validated_at", table_name="rule_validations")
    op.drop_index("ix_rule_validations_rule_id", table_name="rule_validations")
    op.drop_index("ix_rule_validations_market_id", table_name="rule_validations")
    op.drop_index("ix_categorization_rules_l1_l2", table_name="categorization_rules")
    op.drop_index("ix_categorization_rules_enabled", table_name="categorization_rules")

    # Drop columns from markets
    op.drop_column("markets", "matched_rule_id")
    op.drop_column("markets", "categorization_method")

    # Drop tables
    op.drop_table("rule_validations")
    op.drop_table("categorization_rules")
