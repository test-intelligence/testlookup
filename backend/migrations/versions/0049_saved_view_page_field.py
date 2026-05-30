"""AC-2: Add page field to saved_views for analytics view filtering.

Revision ID: 0049
Revises: 0048
"""
from alembic import op
import sqlalchemy as sa

revision = "0049"
down_revision = "0048"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("saved_views", sa.Column("page", sa.String(50), nullable=True))
    op.create_index("ix_sv_page", "saved_views", ["page"])


def downgrade() -> None:
    op.drop_index("ix_sv_page", table_name="saved_views")
    op.drop_column("saved_views", "page")
