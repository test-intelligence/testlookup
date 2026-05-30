"""Performance: Add missing single-column status indexes.

Composite indexes (project_id, status) don't help queries that filter
by status alone. Add dedicated indexes for common status-only filters.

Revision ID: 0051
Revises: 0050
"""
from alembic import op

revision = "0051"
down_revision = "0050"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index("ix_test_cases_status_only", "test_cases", ["status"])
    op.create_index("ix_test_runs_status_only", "test_runs", ["status"])
    op.create_index("ix_sm_project_suite_status", "suite_memberships", ["project_id", "suite_name", "status"])


def downgrade() -> None:
    op.drop_index("ix_sm_project_suite_status", table_name="suite_memberships")
    op.drop_index("ix_test_runs_status_only", table_name="test_runs")
    op.drop_index("ix_test_cases_status_only", table_name="test_cases")
