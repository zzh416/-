"""Record the time a web asset snapshot was fetched.

Revision ID: 20261020_0005
Revises: 20261020_0004
"""
from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261020_0005"
down_revision: str | None = "20261020_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("assets", sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("assets", "fetched_at")
