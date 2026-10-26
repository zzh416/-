"""Add durable question, answer, and review change history.

Revision ID: 20261022_0013
Revises: 20261022_0012
"""
from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261022_0013"
down_revision: str | None = "20261022_0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "question_changes",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("question_id", sa.Uuid(), nullable=False),
        sa.Column("occurrence_id", sa.Uuid(), nullable=True),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("field", sa.String(length=24), nullable=False),
        sa.Column("action", sa.String(length=24), nullable=False),
        sa.Column("previous_value", sa.Text(), nullable=True),
        sa.Column("new_value", sa.Text(), nullable=True),
        sa.Column("entity_version", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["occurrence_id"], ["question_occurrences.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["question_id"], ["questions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_question_changes_occurrence_id"), "question_changes", ["occurrence_id"])
    op.create_index(op.f("ix_question_changes_owner_id"), "question_changes", ["owner_id"])
    op.create_index(op.f("ix_question_changes_question_id"), "question_changes", ["question_id"])


def downgrade() -> None:
    op.drop_index(op.f("ix_question_changes_question_id"), table_name="question_changes")
    op.drop_index(op.f("ix_question_changes_owner_id"), table_name="question_changes")
    op.drop_index(op.f("ix_question_changes_occurrence_id"), table_name="question_changes")
    op.drop_table("question_changes")
