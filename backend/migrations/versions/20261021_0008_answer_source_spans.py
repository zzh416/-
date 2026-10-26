"""Preserve multi-block answer source spans.

Revision ID: 20261021_0008
Revises: 20261021_0007
"""
from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261021_0008"
down_revision: str | None = "20261021_0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "question_occurrences",
        sa.Column(
            "answer_spans",
            sa.JSON(),
            server_default=sa.text("'[]'::json"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("question_occurrences", "answer_spans")
