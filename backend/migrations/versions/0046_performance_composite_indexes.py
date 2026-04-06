"""Add composite indexes for analytics performance (Phase 3).

Adds a 3-column composite index on test_runs(project_id, status, created_at)
to accelerate the most common analytics queries that filter by project, status,
and time range simultaneously.

Revision ID: 0046
Revises: 0045
"""
from alembic import op

revision = "0046"
down_revision = "0045"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_test_runs_project_status_created",
        "test_runs",
        ["project_id", "status", "created_at"],
        if_not_exists=True,
    )


def downgrade() -> None:
    op.drop_index("ix_test_runs_project_status_created", table_name="test_runs")
