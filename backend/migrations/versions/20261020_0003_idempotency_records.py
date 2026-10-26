"""Add persistent idempotency records.

Revision ID: 20261020_0003
Revises: 20261020_0002
"""
from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261020_0003"
down_revision: str | None = "20261020_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "idempotency_records",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("scope", sa.String(length=80), nullable=False),
        sa.Column("idempotency_key", sa.String(length=200), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("resource_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "owner_id", "scope", "idempotency_key", name="uq_idempotency_owner_scope_key"
        ),
    )
    op.create_index("ix_idempotency_records_owner_id", "idempotency_records", ["owner_id"])
    op.create_index("ix_idempotency_records_resource_id", "idempotency_records", ["resource_id"])


def downgrade() -> None:
    op.drop_index("ix_idempotency_records_resource_id", table_name="idempotency_records")
    op.drop_index("ix_idempotency_records_owner_id", table_name="idempotency_records")
    op.drop_table("idempotency_records")
