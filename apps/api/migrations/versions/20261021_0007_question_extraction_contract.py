"""Add question extraction runs and source occurrences.

Revision ID: 20261021_0007
Revises: 20261021_0006
"""
from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261021_0007"
down_revision: str | None = "20261021_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "question_extraction_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("asset_id", sa.Uuid(), nullable=False),
        sa.Column("asset_version_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=32), server_default="QUEUED", nullable=False),
        sa.Column("stage", sa.String(length=32), server_default="QUEUED", nullable=False),
        sa.Column("question_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("answered_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("missing_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("model", sa.String(length=100), nullable=True),
        sa.Column("celery_task_id", sa.String(length=100), nullable=True),
        sa.Column("error_code", sa.String(length=80), nullable=True),
        sa.Column("error_message", sa.String(length=500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["asset_id"], ["assets.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["asset_version_id"], ["asset_versions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_question_extraction_runs_owner_id", "question_extraction_runs", ["owner_id"])
    op.create_index("ix_question_extraction_runs_asset_id", "question_extraction_runs", ["asset_id"])
    op.create_index(
        "ix_question_extraction_runs_asset_version_id",
        "question_extraction_runs",
        ["asset_version_id"],
    )
    op.create_table(
        "question_occurrences",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("question_id", sa.Uuid(), nullable=False),
        sa.Column("extraction_run_id", sa.Uuid(), nullable=False),
        sa.Column("asset_version_id", sa.Uuid(), nullable=False),
        sa.Column("source_chunk_id", sa.Uuid(), nullable=True),
        sa.Column("answer_source_chunk_id", sa.Uuid(), nullable=True),
        sa.Column("question_start", sa.Integer(), nullable=False),
        sa.Column("question_end", sa.Integer(), nullable=False),
        sa.Column("answer_start", sa.Integer(), nullable=True),
        sa.Column("answer_end", sa.Integer(), nullable=True),
        sa.Column("question_quote", sa.Text(), nullable=False),
        sa.Column("answer_quote", sa.Text(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("review_status", sa.String(length=24), server_default="PENDING", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["answer_source_chunk_id"], ["text_chunks.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["asset_version_id"], ["asset_versions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["extraction_run_id"], ["question_extraction_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["question_id"], ["questions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_chunk_id"], ["text_chunks.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "extraction_run_id",
            "source_chunk_id",
            "question_start",
            "question_end",
            name="uq_question_occurrences_run_source_range",
        ),
    )
    op.create_index("ix_question_occurrences_question_id", "question_occurrences", ["question_id"])
    op.create_index("ix_question_occurrences_extraction_run_id", "question_occurrences", ["extraction_run_id"])
    op.create_index("ix_question_occurrences_asset_version_id", "question_occurrences", ["asset_version_id"])


def downgrade() -> None:
    op.drop_index("ix_question_occurrences_asset_version_id", table_name="question_occurrences")
    op.drop_index("ix_question_occurrences_extraction_run_id", table_name="question_occurrences")
    op.drop_index("ix_question_occurrences_question_id", table_name="question_occurrences")
    op.drop_table("question_occurrences")
    op.drop_index("ix_question_extraction_runs_asset_version_id", table_name="question_extraction_runs")
    op.drop_index("ix_question_extraction_runs_asset_id", table_name="question_extraction_runs")
    op.drop_index("ix_question_extraction_runs_owner_id", table_name="question_extraction_runs")
    op.drop_table("question_extraction_runs")
