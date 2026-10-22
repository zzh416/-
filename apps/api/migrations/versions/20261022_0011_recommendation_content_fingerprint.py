"""Track the question and answer versions used by recommendation runs.

Revision ID: 20261022_0011
Revises: 20261022_0010
"""
from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261022_0011"
down_revision: str | None = "20261022_0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "recommendation_runs",
        sa.Column("content_fingerprint", sa.String(length=64), server_default="", nullable=False),
    )


def downgrade() -> None:
    op.drop_column("recommendation_runs", "content_fingerprint")
