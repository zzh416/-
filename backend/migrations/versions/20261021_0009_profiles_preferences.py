"""Add resume profiles, versioned skills, and preference rules.

Revision ID: 20261021_0009
Revises: 20261021_0008
"""
from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261021_0009"
down_revision: str | None = "20261021_0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column("assets", "collection_id", existing_type=sa.Uuid(), nullable=True)
    op.add_column(
        "assets",
        sa.Column("kind", sa.String(length=24), server_default="LIBRARY", nullable=False),
    )

    op.create_table(
        "user_profiles",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("resume_asset_id", sa.Uuid(), nullable=True),
        sa.Column("current_version", sa.Integer(), server_default="0", nullable=False),
        sa.Column("extraction_status", sa.String(length=32), server_default="EMPTY", nullable=False),
        sa.Column("target_role", sa.String(length=200), nullable=True),
        sa.Column("model", sa.String(length=100), nullable=True),
        sa.Column("error_code", sa.String(length=80), nullable=True),
        sa.Column("error_message", sa.String(length=500), nullable=True),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["resume_asset_id"], ["assets.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("owner_id"),
        sa.UniqueConstraint("resume_asset_id"),
    )
    op.create_index("ix_user_profiles_owner_id", "user_profiles", ["owner_id"])

    op.create_table(
        "profile_versions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("profile_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("resume_asset_version_id", sa.Uuid(), nullable=True),
        sa.Column("target_role", sa.String(length=200), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["profile_id"], ["user_profiles.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["resume_asset_version_id"], ["asset_versions.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "profile_id", "version", name="uq_profile_versions_profile_version"
        ),
    )
    op.create_index("ix_profile_versions_profile_id", "profile_versions", ["profile_id"])

    op.create_table(
        "profile_skills",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("profile_version_id", sa.Uuid(), nullable=False),
        sa.Column("display_name", sa.String(length=120), nullable=False),
        sa.Column("canonical_name", sa.String(length=120), nullable=False),
        sa.Column("category", sa.String(length=40), nullable=False),
        sa.Column("proficiency", sa.String(length=24), server_default="UNSPECIFIED", nullable=False),
        sa.Column("evidence", sa.Text(), nullable=True),
        sa.Column("review_status", sa.String(length=24), server_default="PENDING", nullable=False),
        sa.Column("source", sa.String(length=24), server_default="MODEL", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(
            ["profile_version_id"], ["profile_versions.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "profile_version_id",
            "canonical_name",
            name="uq_profile_skills_version_canonical",
        ),
    )
    op.create_index(
        "ix_profile_skills_profile_version_id", "profile_skills", ["profile_version_id"]
    )

    op.create_table(
        "preference_rules",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("rule_type", sa.String(length=16), nullable=False),
        sa.Column("technology", sa.String(length=120), nullable=False),
        sa.Column("canonical_technology", sa.String(length=120), nullable=False),
        sa.Column("category", sa.String(length=40), nullable=False),
        sa.Column("scope", sa.String(length=16), server_default="BOTH", nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_preference_rules_owner_id", "preference_rules", ["owner_id"])
    op.create_index(
        "ix_preference_rules_canonical_technology",
        "preference_rules",
        ["canonical_technology"],
    )


def downgrade() -> None:
    op.drop_index("ix_preference_rules_canonical_technology", table_name="preference_rules")
    op.drop_index("ix_preference_rules_owner_id", table_name="preference_rules")
    op.drop_table("preference_rules")
    op.drop_index("ix_profile_skills_profile_version_id", table_name="profile_skills")
    op.drop_table("profile_skills")
    op.drop_index("ix_profile_versions_profile_id", table_name="profile_versions")
    op.drop_table("profile_versions")
    op.drop_index("ix_user_profiles_owner_id", table_name="user_profiles")
    op.drop_table("user_profiles")
    op.execute("DELETE FROM assets WHERE kind = 'RESUME'")
    op.drop_column("assets", "kind")
    op.alter_column("assets", "collection_id", existing_type=sa.Uuid(), nullable=False)
