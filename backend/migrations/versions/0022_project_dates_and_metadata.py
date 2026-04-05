"""Add start_date, end_date, and tags to projects table.

Revision ID: 0022
Revises: 0021
"""
from alembic import op
import sqlalchemy as sa

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("projects", sa.Column("start_date", sa.DateTime(timezone=True), nullable=True))
    op.add_column("projects", sa.Column("end_date", sa.DateTime(timezone=True), nullable=True))
    op.add_column("projects", sa.Column("tags", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("projects", "tags")
    op.drop_column("projects", "end_date")
    op.drop_column("projects", "start_date")
