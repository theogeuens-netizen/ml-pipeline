"""Add rule validation and improvement tracking tables

Revision ID: 009
Revises: 008
Create Date: 2024-12-22

Tables added:
- rule_validations: Track Claude verification of rule categorizations
- rule_improvements: Track suggested improvements to rules
- rule_suggestions: Track suggestions for new rules from uncategorized patterns
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


revision: str = "009"
down_revision: Union[str, None] = "008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Rule validation results - comparing rule vs Claude categorization
    op.create_table(
        "rule_validations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("rule_id", sa.Integer(), nullable=False),
        sa.Column("market_id", sa.Integer(), nullable=False),
        # Rule's categorization
        sa.Column("rule_l1", sa.String(50), nullable=False),
        sa.Column("rule_l2", sa.String(50), nullable=False),
        sa.Column("rule_l3", sa.String(50), nullable=True),
        # Claude's categorization
        sa.Column("claude_l1", sa.String(50), nullable=False),
        sa.Column("claude_l2", sa.String(50), nullable=False),
        sa.Column("claude_l3", sa.String(50), nullable=True),
        # Validation result
        sa.Column("is_match", sa.Boolean(), nullable=False),
        sa.Column("mismatch_type", sa.String(20), nullable=True),  # l1, l2, l3, or null if match
        sa.Column("mismatch_reason", sa.Text(), nullable=True),
        sa.Column("validated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["rule_id"], ["categorization_rules.id"]),
        sa.ForeignKeyConstraint(["market_id"], ["markets.id"]),
    )
    op.create_index("ix_rule_validations_rule_id", "rule_validations", ["rule_id"])
    op.create_index("ix_rule_validations_is_match", "rule_validations", ["is_match"])

    # Rule improvement suggestions
    op.create_table(
        "rule_improvements",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("rule_id", sa.Integer(), nullable=False),
        sa.Column("improvement_type", sa.String(30), nullable=False),  # add_keyword, remove_keyword, add_negative, fix_l3_pattern, update_default
        sa.Column("suggestion", JSONB, nullable=False),  # {keyword: "foo", reason: "..."}
        sa.Column("example_market_ids", JSONB, nullable=True),  # [123, 456, ...]
        sa.Column("confidence", sa.Float(), nullable=True),  # 0-1 confidence score
        sa.Column("status", sa.String(20), server_default="pending"),  # pending, approved, rejected, applied
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["rule_id"], ["categorization_rules.id"]),
    )
    op.create_index("ix_rule_improvements_rule_id", "rule_improvements", ["rule_id"])
    op.create_index("ix_rule_improvements_status", "rule_improvements", ["status"])

    # New rule suggestions from uncategorized patterns
    op.create_table(
        "rule_suggestions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("suggested_l1", sa.String(50), nullable=False),
        sa.Column("suggested_l2", sa.String(50), nullable=False),
        sa.Column("suggested_keywords", JSONB, nullable=False),  # ["keyword1", "keyword2"]
        sa.Column("example_markets", JSONB, nullable=False),  # [{id: 123, question: "..."}, ...]
        sa.Column("occurrence_count", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(20), server_default="pending"),  # pending, approved, rejected, created
        sa.Column("created_rule_id", sa.Integer(), nullable=True),  # FK to created rule if approved
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_rule_suggestions_status", "rule_suggestions", ["status"])
    op.create_index("ix_rule_suggestions_l1_l2", "rule_suggestions", ["suggested_l1", "suggested_l2"])

    # Add confidence and verification columns to markets
    op.add_column("markets", sa.Column("categorization_confidence", sa.String(10), nullable=True))  # high, medium, low
    op.add_column("markets", sa.Column("verification_status", sa.String(20), nullable=True))  # verified, mismatch, pending


def downgrade() -> None:
    op.drop_column("markets", "verification_status")
    op.drop_column("markets", "categorization_confidence")

    op.drop_index("ix_rule_suggestions_l1_l2", table_name="rule_suggestions")
    op.drop_index("ix_rule_suggestions_status", table_name="rule_suggestions")
    op.drop_table("rule_suggestions")

    op.drop_index("ix_rule_improvements_status", table_name="rule_improvements")
    op.drop_index("ix_rule_improvements_rule_id", table_name="rule_improvements")
    op.drop_table("rule_improvements")

    op.drop_index("ix_rule_validations_is_match", table_name="rule_validations")
    op.drop_index("ix_rule_validations_rule_id", table_name="rule_validations")
    op.drop_table("rule_validations")
