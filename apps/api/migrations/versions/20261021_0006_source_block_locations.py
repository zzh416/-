"""Persist source block types, locators, and failed ingestion units.

Revision ID: 20261021_0006
Revises: 20261020_0005
"""
from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261021_0006"
down_revision: str | None = "20261020_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "text_chunks",
        sa.Column("block_type", sa.String(length=32), server_default="TEXT", nullable=False),
    )
    op.add_column(
        "text_chunks",
        sa.Column("locator", sa.JSON(), server_default=sa.text("'{}'::json"), nullable=False),
    )
    op.add_column(
        "ingestion_jobs",
        sa.Column("unit_failures", sa.JSON(), server_default=sa.text("'[]'::json"), nullable=False),
    )


def downgrade() -> None:
    op.drop_column("ingestion_jobs", "unit_failures")
    op.drop_column("text_chunks", "locator")
    op.drop_column("text_chunks", "block_type")
