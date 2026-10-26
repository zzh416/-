"""Allow profile-grounded interviews without a question bank or recommendation run."""
from alembic import op
import sqlalchemy as sa

revision = "20261026_0020"
down_revision = "20261026_0019"
branch_labels = None
depends_on = None


def upgrade():
    op.alter_column("interview_sessions", "recommendation_run_id", existing_type=sa.Uuid(), nullable=True)


def downgrade():
    # PostgreSQL refuses this if AI-only sessions exist; never discard them silently.
    op.alter_column("interview_sessions", "recommendation_run_id", existing_type=sa.Uuid(), nullable=False)
