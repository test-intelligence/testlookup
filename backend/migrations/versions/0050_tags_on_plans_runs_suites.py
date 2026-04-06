"""TG-1: Add tags JSON column to test_plans, test_runs, and suite_memberships.

Revision ID: 0050
Revises: 0049
"""
from alembic import op
import sqlalchemy as sa

revision = "0050"
down_revision = "0049"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("test_plans", sa.Column("tags", sa.JSON, nullable=True))
    op.add_column("test_runs", sa.Column("tags", sa.JSON, nullable=True))
    op.add_column("suite_memberships", sa.Column("tags", sa.JSON, nullable=True))


def downgrade() -> None:
    op.drop_column("suite_memberships", "tags")
    op.drop_column("test_runs", "tags")
    op.drop_column("test_plans", "tags")
