"""Persist manual learning states and confirmed interview weak questions."""
import sqlalchemy as sa
from alembic import op

revision = "20261026_0019"
down_revision = "20261026_0018"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("learning_records",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("owner_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("identity_key", sa.String(100), nullable=False),
        sa.Column("question_id", sa.Uuid(), sa.ForeignKey("questions.id", ondelete="CASCADE"), nullable=True),
        sa.Column("source_kind", sa.String(16), nullable=False),
        sa.Column("source_session_id", sa.Uuid(), sa.ForeignKey("interview_sessions.id", ondelete="SET NULL"), nullable=True),
        sa.Column("source_turn_id", sa.String(36), nullable=True),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("question_version", sa.Integer(), nullable=False),
        sa.Column("answer_version", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("owner_id", "identity_key", name="uq_learning_owner_identity"))
    op.create_index("ix_learning_records_owner_id", "learning_records", ["owner_id"])
    op.create_index("ix_learning_records_question_id", "learning_records", ["question_id"])


def downgrade():
    op.drop_table("learning_records")
