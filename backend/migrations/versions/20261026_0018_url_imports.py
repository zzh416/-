"""Persist batch URL import history and item outcomes."""
from alembic import op
import sqlalchemy as sa

revision = "20261026_0018"
down_revision = "20261026_0017"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("url_import_batches",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("owner_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("collection_id", sa.Uuid(), sa.ForeignKey("collections.id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False))
    op.create_index("ix_url_import_batches_owner_id", "url_import_batches", ["owner_id"])
    op.create_index("ix_url_import_batches_collection_id", "url_import_batches", ["collection_id"])
    op.create_table("url_import_items",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("batch_id", sa.Uuid(), sa.ForeignKey("url_import_batches.id", ondelete="CASCADE"), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("input_url", sa.Text(), nullable=False),
        sa.Column("normalized_url", sa.Text(), nullable=True),
        sa.Column("disposition", sa.String(16), nullable=False),
        sa.Column("message", sa.String(500), nullable=True),
        sa.Column("duplicate_of", sa.Integer(), nullable=True),
        sa.Column("asset_id", sa.Uuid(), sa.ForeignKey("assets.id", ondelete="SET NULL"), nullable=True),
        sa.UniqueConstraint("batch_id", "ordinal", name="uq_url_import_items_batch_ordinal"))
    op.create_index("ix_url_import_items_batch_id", "url_import_items", ["batch_id"])
    op.create_index("ix_url_import_items_asset_id", "url_import_items", ["asset_id"])


def downgrade():
    op.drop_table("url_import_items")
    op.drop_table("url_import_batches")
