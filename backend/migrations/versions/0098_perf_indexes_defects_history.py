"""Perf indexes: release-gate defect count + windowed flaky-count scan.

Revision ID: 0098
Revises: 0097
Create Date: 2026-07-02

Two hot read paths acquired new, index-unfriendly shapes in the release-gate /
flaky-count fixes and had no matching composite index:

1. ``ix_defects_project_status_severity`` on
   ``defects (project_id, resolution_status, severity)`` — backs
   ``metrics_service.count_open_critical_defects`` (release council + dashboard
   readiness), which filters ``project_id = ? AND resolution_status = 'OPEN' AND
   severity = 'CRITICAL'`` on every /release-gate + /overview render. The table
   only had a single-column ``ix_defects_project_id`` (migration 0082), so the
   status/severity predicates fell to a heap filter.

2. ``ix_history_fingerprint_date_status`` on
   ``test_case_history (test_fingerprint, created_at, status)`` — a covering
   variant for the windowed flaky-count scan (``_count_flaky_tests``), which
   partitions/orders by ``(test_fingerprint, created_at)`` then filters
   ``status IN ('FAILED','BROKEN')``. The existing
   ``ix_history_fingerprint_date`` already serves the partition+order; adding
   ``status`` makes that scan index-only. The 2-col index is kept (smaller;
   still used by other queries).

Each ``CREATE INDEX`` runs ``CONCURRENTLY`` (same pattern as migrations 0082 /
0090) so production tables don't take a write lock during the build.
``autocommit_block`` is required because ``CREATE INDEX CONCURRENTLY`` cannot run
inside a transaction block. Each index is its own block so a failure on one
doesn't roll back the other, and ``if_not_exists`` / ``if_exists`` make both
directions safe to re-run after a partial failure.
"""
from alembic import op


revision = "0098"
down_revision = "0097"
branch_labels = None
depends_on = None


_INDEX_SPECS = (
    ("ix_defects_project_status_severity", "defects",
     ["project_id", "resolution_status", "severity"]),
    ("ix_history_fingerprint_date_status", "test_case_history",
     ["test_fingerprint", "created_at", "status"]),
)


def upgrade() -> None:
    for index_name, table, cols in _INDEX_SPECS:
        with op.get_context().autocommit_block():
            op.create_index(
                index_name,
                table,
                cols,
                postgresql_concurrently=True,
                if_not_exists=True,
            )


def downgrade() -> None:
    for index_name, table, _cols in _INDEX_SPECS:
        with op.get_context().autocommit_block():
            op.drop_index(
                index_name,
                table_name=table,
                postgresql_concurrently=True,
                if_exists=True,
            )
