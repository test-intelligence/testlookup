"""Add partial unique index on defects(test_case_id) WHERE resolution_status='OPEN'.

Ensures at most one open defect per test case at the database level,
preventing duplicates from concurrent triage pipelines.

Revision ID: 0044
Revises: 0043
"""
from alembic import op

revision = "0044"
down_revision = "0043"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_defects_test_case_open_unique",
        "defects",
        ["test_case_id"],
        unique=True,
        postgresql_where="resolution_status = 'OPEN' AND test_case_id IS NOT NULL",
    )


def downgrade() -> None:
    op.drop_index("ix_defects_test_case_open_unique", table_name="defects")
