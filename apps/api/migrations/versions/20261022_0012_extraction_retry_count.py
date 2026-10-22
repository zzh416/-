"""Track bounded retries for question extraction runs.

Revision ID: 20261022_0012
Revises: 20261022_0011
"""
from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261022_0012"
down_revision: str | None = "20261022_0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "question_extraction_runs",
        sa.Column("retry_count", sa.Integer(), server_default="0", nullable=False),
    )


def downgrade() -> None:
    op.drop_column("question_extraction_runs", "retry_count")
