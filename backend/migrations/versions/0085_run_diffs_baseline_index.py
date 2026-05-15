"""Add FK index ``ix_run_diffs_baseline_run_id``.

Revision ID: 0085
Revises: 0084
Create Date: 2026-05-16

Database audit 2026-05-16 (docs/DATABASE_AUDIT_2026-05-16.md, finding P3-4).
``RunDiff.baseline_run_id`` is the only baseline-pointer FK in the
run-comparison schema without an index. Its sibling
``RunBaseline.baseline_run_id`` already has ``ix_run_baselines_baseline``
(migration 0073). Both columns support the "show every run diffed
against baseline X" query — without the index, queries that filter on
``run_diffs.baseline_run_id`` do a seq scan.

Uses ``CREATE INDEX CONCURRENTLY`` so the build doesn't lock the
``run_diffs`` table (the table grows linearly with every run that has
a baseline, so a blocking build hurts in larger deployments). Each
``CONCURRENTLY`` must run outside a transaction → ``autocommit_block``.
Re-running the migration after a partial failure is safe via
``if_not_exists`` / ``if_exists``.
"""
from alembic import op


revision = "0085"
down_revision = "0084"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.create_index(
            "ix_run_diffs_baseline_run_id",
            "run_diffs",
            ["baseline_run_id"],
            postgresql_concurrently=True,
            if_not_exists=True,
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.drop_index(
            "ix_run_diffs_baseline_run_id",
            table_name="run_diffs",
            postgresql_concurrently=True,
            if_exists=True,
        )
