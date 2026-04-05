"""Add component_owner_map JSONB to projects.

Revision ID: 0020
Revises: 0019
"""
from alembic import op
import sqlalchemy as sa

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "projects",
        sa.Column("component_owner_map", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("projects", "component_owner_map")
