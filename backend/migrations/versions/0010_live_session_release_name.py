"""Add release_name column to live_sessions.

Revision ID: 0010
Revises: 0009
Create Date: 2026-03-26
"""
from alembic import op
import sqlalchemy as sa

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "live_sessions",
        sa.Column("release_name", sa.String(255), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("live_sessions", "release_name")
