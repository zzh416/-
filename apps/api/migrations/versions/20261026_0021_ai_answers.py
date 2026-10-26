"""Keep reviewed AI answer supplements separate from source answers."""
from alembic import op
import sqlalchemy as sa

revision = "20261026_0021"
down_revision = "20261026_0020"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("ai_answer_runs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("owner_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("question_id", sa.Uuid(), sa.ForeignKey("questions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("question_version", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("review_status", sa.String(16), nullable=False),
        sa.Column("used_profile", sa.Boolean(), nullable=False),
        sa.Column("used_note", sa.Boolean(), nullable=False),
        sa.Column("input_fingerprint", sa.String(64), nullable=False),
        sa.Column("input_snapshot", sa.JSON(), nullable=False),
        sa.Column("content", sa.Text(), nullable=True),
        sa.Column("basis", sa.String(24), nullable=True),
        sa.Column("citations", sa.JSON(), nullable=False),
        sa.Column("provider", sa.String(32), nullable=True),
        sa.Column("model", sa.String(100), nullable=True),
        sa.Column("retry_count", sa.Integer(), nullable=False),
        sa.Column("request_times", sa.JSON(), nullable=False),
        sa.Column("job_token", sa.Uuid(), nullable=True),
        sa.Column("error_code", sa.String(80), nullable=True),
        sa.Column("error_message", sa.String(500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True))
    for name in ("owner_id", "question_id", "updated_at"):
        op.create_index(f"ix_ai_answer_runs_{name}", "ai_answer_runs", [name])
    for table in ("recommendation_items", "learning_records"):
        # Snapshot identifiers intentionally survive deletion/replacement of a supplement.
        op.add_column(table, sa.Column("ai_answer_id", sa.Uuid(), nullable=True))
        op.add_column(table, sa.Column("ai_answer_version", sa.Integer(), nullable=True))


def downgrade():
    for table in ("recommendation_items", "learning_records"):
        op.drop_column(table, "ai_answer_version")
        op.drop_column(table, "ai_answer_id")
    op.drop_table("ai_answer_runs")
