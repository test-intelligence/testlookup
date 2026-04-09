"""Add avatar_color column to users table.

Revision ID: 0052
Revises: 0051
"""
from alembic import op
import sqlalchemy as sa

revision = "0052"
down_revision = "0051"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("avatar_color", sa.String(20), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("users", "avatar_color")
