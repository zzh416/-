"""Add dated sources, extraction diagnostics and recommendation source snapshots."""
from alembic import op
import sqlalchemy as sa

revision = "20261025_0014"
down_revision = "20261022_0013"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("assets", sa.Column("source_date", sa.Date(), nullable=True))
    op.add_column("assets", sa.Column("source_metadata_version", sa.Integer(), server_default="1", nullable=False))
    op.add_column("question_extraction_runs", sa.Column("diagnostics", sa.JSON(), nullable=True))
    op.add_column("recommendation_items", sa.Column("source_statistics", sa.JSON(), nullable=True))


def downgrade():
    op.drop_column("recommendation_items", "source_statistics")
    op.drop_column("question_extraction_runs", "diagnostics")
    op.drop_column("assets", "source_metadata_version")
    op.drop_column("assets", "source_date")
