"""Create local user and collections.

Revision ID: 20261020_0001
Revises:
"""
from typing import Sequence
from uuid import UUID

import sqlalchemy as sa
from alembic import op

revision: str = "20261020_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

LOCAL_USER_ID = "00000000-0000-0000-0000-000000000001"


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("display_name", sa.String(length=100), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "collections",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("purpose", sa.String(length=32), nullable=False),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("owner_id", "name", name="uq_collections_owner_name"),
    )
    op.create_index("ix_collections_owner_id", "collections", ["owner_id"])
    op.execute(
        sa.text(
            "INSERT INTO users (id, display_name) VALUES (:id, :display_name)"
        ).bindparams(
            sa.bindparam("id", value=UUID(LOCAL_USER_ID), type_=sa.Uuid()),
            sa.bindparam("display_name", value="本地用户", type_=sa.String()),
        )
    )


def downgrade() -> None:
    op.drop_index("ix_collections_owner_id", table_name="collections")
    op.drop_table("collections")
    op.drop_table("users")
