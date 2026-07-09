"""add CI context columns to test_runs

Revision ID: 0101
Revises: 0100
Create Date: 2026-07-09

PMF backlog US-4.3: runs need to know which repo/PR/CI job produced them.
`pr_number` is the anchor for PR-scoped features (sticky PR summary comments,
commit attribution); `ci_run_url` deep-links back to the CI job. Populated by
SDK/CLI auto-detection from standard CI env vars or explicitly by callers.

The partial index keeps "all runs for PR N" fast without taxing the vast
majority of rows that carry no PR.
"""
from alembic import op
import sqlalchemy as sa

revision = "0101"
down_revision = "0100"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("test_runs", sa.Column("ci_provider", sa.String(30), nullable=True))
    op.add_column("test_runs", sa.Column("ci_repo", sa.String(300), nullable=True))
    op.add_column("test_runs", sa.Column("pr_number", sa.Integer(), nullable=True))
    op.add_column("test_runs", sa.Column("ci_actor", sa.String(120), nullable=True))
    op.add_column("test_runs", sa.Column("ci_run_url", sa.String(1000), nullable=True))
    op.create_index(
        "ix_test_runs_project_pr",
        "test_runs",
        ["project_id", "pr_number"],
        postgresql_where=sa.text("pr_number IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_test_runs_project_pr", table_name="test_runs")
    op.drop_column("test_runs", "ci_run_url")
    op.drop_column("test_runs", "ci_actor")
    op.drop_column("test_runs", "pr_number")
    op.drop_column("test_runs", "ci_repo")
    op.drop_column("test_runs", "ci_provider")
