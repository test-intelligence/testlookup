"""Add test_runs.unknown_tests — uninterpretable results had no column.

A test result whose reported status is outside PASSED/FAILED/SKIPPED/BROKEN is
persisted as ``TestStatus.UNKNOWN``, but ``test_runs`` had columns for only the
four. On the file path ``total_tests`` is a COUNT(*), so those rows sat inside
the total with no column explaining them; on the live path they incremented no
counter at all and were erased from the total outright.

Revision ID: 0118
Revises: 0117
"""
import sqlalchemy as sa
from alembic import op

revision = "0118"
down_revision = "0117"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "test_runs",
        sa.Column(
            "unknown_tests",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )


def downgrade() -> None:
    op.drop_column("test_runs", "unknown_tests")
