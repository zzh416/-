"""Version resume projects and saved recommendation project suggestions."""
from alembic import op
import sqlalchemy as sa

revision = "20261025_0015"
down_revision = "20261025_0014"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("profile_versions", sa.Column("projects", sa.JSON(), nullable=False, server_default="[]"))
    op.add_column("recommendation_items", sa.Column("project_matches", sa.JSON(), nullable=True))


def downgrade():
    op.drop_column("recommendation_items", "project_matches")
    op.drop_column("profile_versions", "projects")
