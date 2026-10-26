"""Persistent timed text interview sessions with separate model-job tokens."""
from alembic import op
import sqlalchemy as sa

revision = "20261026_0017"
down_revision = "20261026_0016"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("interview_sessions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("owner_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("recommendation_run_id", sa.Uuid(), sa.ForeignKey("recommendation_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("state", sa.JSON(), nullable=False),
        sa.Column("job_token", sa.Uuid(), nullable=True),
        sa.Column("job_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False))
    op.create_index("ix_interview_sessions_owner_id", "interview_sessions", ["owner_id"])


def downgrade():
    op.drop_table("interview_sessions")
