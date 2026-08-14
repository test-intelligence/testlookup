"""Add test_runs.environment — the missing environment dimension.

Phase 0 (P0-1) of the test-intelligence roadmap
(``architecture/TEST_INTELLIGENCE_PLAN.md``).

``test_runs`` carried branch, commit, CI provider/repo/actor and the OpenShift
fields, but nothing that answers "which environment did this run execute
against?". Two downstream items need it:

* the per-test history timeline, where the practitioner ask is explicitly a
  history annotated with environment/device metadata; and
* the continuous flakiness score, which fuses *environment consistency* as one
  of its four signals — a test that only fails on one runner profile is a
  different animal from one that fails everywhere.

Nullable and unindexed-by-default-value: every existing row keeps NULL, and the
read path derives a fallback key from (ci_provider, oc_namespace, branch) so old
runs are not silently mislabelled as belonging to one environment. The column is
populated going forward from an optional ingestion field — no breaking wire
change for existing SDK/CLI callers.

Revision ID: 0129
Revises: 0128
"""
from alembic import op
import sqlalchemy as sa

revision = "0129"
down_revision = "0128"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "test_runs",
        sa.Column("environment", sa.String(length=100), nullable=True),
    )
    # Composite with project_id: environment is only ever queried within a
    # project (house rule — nothing is globally scoped), and the flakiness
    # score groups by (project, environment).
    op.create_index(
        "ix_test_runs_project_environment",
        "test_runs",
        ["project_id", "environment"],
    )


def downgrade() -> None:
    op.drop_index("ix_test_runs_project_environment", table_name="test_runs")
    op.drop_column("test_runs", "environment")
