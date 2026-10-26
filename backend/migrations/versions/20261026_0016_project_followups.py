"""Persist separate project follow-up chains without editing the source question bank."""
from alembic import op
import sqlalchemy as sa

revision = "20261026_0016"
down_revision = "20261025_0015"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("project_followup_runs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("owner_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("recommendation_run_id", sa.Uuid(), sa.ForeignKey("recommendation_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("project_snapshot", sa.JSON(), nullable=False),
        sa.Column("seeds", sa.JSON(), nullable=False),
        sa.Column("items", sa.JSON(), server_default="[]", nullable=False),
        sa.Column("rejection_counts", sa.JSON(), server_default="{}", nullable=False),
        sa.Column("status", sa.String(32), server_default="QUEUED", nullable=False),
        sa.Column("algorithm_version", sa.String(40), server_default="project-followups-v1", nullable=False),
        sa.Column("provider", sa.String(24), nullable=True), sa.Column("model", sa.String(100), nullable=True),
        sa.Column("retry_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("error_code", sa.String(80), nullable=True), sa.Column("error_message", sa.String(500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True), sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )
    for column in ("owner_id", "recommendation_run_id", "project_id"):
        op.create_index(f"ix_project_followup_runs_{column}", "project_followup_runs", [column])


def downgrade():
    op.drop_table("project_followup_runs")
