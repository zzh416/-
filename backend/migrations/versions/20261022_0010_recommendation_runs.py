"""Add versioned recommendation runs and explainable result snapshots.

Revision ID: 20261022_0010
Revises: 20261021_0009
"""
from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261022_0010"
down_revision: str | None = "20261021_0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "recommendation_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("profile_version_id", sa.Uuid(), nullable=True),
        sa.Column("profile_version", sa.Integer(), server_default="0", nullable=False),
        sa.Column("profile_snapshot", sa.JSON(), server_default="{}", nullable=False),
        sa.Column("rule_snapshot", sa.JSON(), server_default="[]", nullable=False),
        sa.Column("rules_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("algorithm_version", sa.String(length=40), nullable=False),
        sa.Column("status", sa.String(length=24), server_default="SUCCEEDED", nullable=False),
        sa.Column("total_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("included_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("excluded_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("answered_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("missing_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["profile_version_id"], ["profile_versions.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_recommendation_runs_owner_id", "recommendation_runs", ["owner_id"])

    op.create_table(
        "recommendation_items",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("question_id", sa.Uuid(), nullable=True),
        sa.Column("collection_id", sa.Uuid(), nullable=True),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("question_version", sa.Integer(), nullable=False),
        sa.Column("answer_version", sa.Integer(), nullable=True),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("answer_content", sa.Text(), nullable=True),
        sa.Column("answer_source_type", sa.String(length=24), nullable=True),
        sa.Column("decision", sa.String(length=16), nullable=False),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("answer_state", sa.String(length=24), nullable=False),
        sa.Column("usable_answer_state", sa.String(length=24), nullable=False),
        sa.Column("question_technologies", sa.JSON(), server_default="[]", nullable=False),
        sa.Column("answer_technologies", sa.JSON(), server_default="[]", nullable=False),
        sa.Column("reason_codes", sa.JSON(), server_default="[]", nullable=False),
        sa.Column("reasons", sa.JSON(), server_default="[]", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["recommendation_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["question_id"], ["questions.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["collection_id"], ["collections.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "run_id", "question_id", name="uq_recommendation_items_run_question"
        ),
    )
    op.create_index("ix_recommendation_items_run_id", "recommendation_items", ["run_id"])
    op.create_index(
        "ix_recommendation_items_question_id", "recommendation_items", ["question_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_recommendation_items_question_id", table_name="recommendation_items")
    op.drop_index("ix_recommendation_items_run_id", table_name="recommendation_items")
    op.drop_table("recommendation_items")
    op.drop_index("ix_recommendation_runs_owner_id", table_name="recommendation_runs")
    op.drop_table("recommendation_runs")
