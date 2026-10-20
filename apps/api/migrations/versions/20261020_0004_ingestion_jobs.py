"""Add P2 source metadata and ingestion jobs.

Revision ID: 20261020_0004
Revises: 20261020_0003
"""
from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261020_0004"
down_revision: str | None = "20261020_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "assets",
        "status",
        existing_type=sa.String(length=20),
        type_=sa.String(length=32),
        existing_nullable=False,
    )
    op.add_column("assets", sa.Column("source_url", sa.Text(), nullable=True))
    op.add_column("assets", sa.Column("storage_key", sa.String(length=500), nullable=True))
    op.add_column("assets", sa.Column("content_type", sa.String(length=120), nullable=True))
    op.add_column("assets", sa.Column("byte_size", sa.BigInteger(), nullable=True))
    op.add_column("assets", sa.Column("content_sha256", sa.String(length=64), nullable=True))

    op.create_table(
        "ingestion_jobs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("asset_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=32), server_default="QUEUED", nullable=False),
        sa.Column("stage", sa.String(length=32), server_default="STORED", nullable=False),
        sa.Column("processed_units", sa.Integer(), server_default="0", nullable=False),
        sa.Column("total_units", sa.Integer(), nullable=True),
        sa.Column("retry_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("celery_task_id", sa.String(length=100), nullable=True),
        sa.Column("error_code", sa.String(length=80), nullable=True),
        sa.Column("error_message", sa.String(length=500), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["asset_id"], ["assets.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("asset_id"),
    )
    op.create_index("ix_ingestion_jobs_asset_id", "ingestion_jobs", ["asset_id"])
    op.create_index("ix_ingestion_jobs_owner_id", "ingestion_jobs", ["owner_id"])


def downgrade() -> None:
    op.drop_index("ix_ingestion_jobs_owner_id", table_name="ingestion_jobs")
    op.drop_index("ix_ingestion_jobs_asset_id", table_name="ingestion_jobs")
    op.drop_table("ingestion_jobs")
    op.drop_column("assets", "content_sha256")
    op.drop_column("assets", "byte_size")
    op.drop_column("assets", "content_type")
    op.drop_column("assets", "storage_key")
    op.drop_column("assets", "source_url")
    op.alter_column(
        "assets",
        "status",
        existing_type=sa.String(length=32),
        type_=sa.String(length=20),
        existing_nullable=False,
    )
