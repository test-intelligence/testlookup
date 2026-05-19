"""Add missing FK indexes on hot-path tables.

Revision ID: 0082
Revises: 0081
Create Date: 2026-05-16

Database audit 2026-05-16 (docs/DATABASE_AUDIT_2026-05-16.md, finding P1-4)
identified four foreign-key columns lacking indexes on tables that are
queried in hot paths. Postgres does NOT auto-index FKs, so every WHERE /
JOIN on these columns performs a sequential scan.

The four indexes:

- ``ix_history_test_case_id`` on ``test_case_history.test_case_id`` — used
  by "give me this case's timeline" queries. The table grows linearly with
  every ingested test result, so the seq scan cost compounds over time.

- ``ix_history_test_run_id`` on ``test_case_history.test_run_id`` — used
  by "give me this run's per-test timeline" queries (run intelligence,
  AI pipeline).

- ``ix_defects_project_id`` on ``defects.project_id`` — used by every
  ``GET /api/v1/analytics/defects?project_id=X`` call and by the Defects
  page row count.

- ``ix_quality_gates_project`` on ``quality_gates.project_id`` — used by
  per-project gate rule lookups during run evaluation.

Each ``CREATE INDEX`` runs ``CONCURRENTLY`` so production tables don't
take a write lock during the build. ``autocommit_block`` is required
because ``CREATE INDEX CONCURRENTLY`` cannot run inside a transaction
block (Postgres rejects it with ``CREATE INDEX CONCURRENTLY cannot run
inside a transaction block``). Each statement is its own block so a
failure on one index doesn't roll back the others.

The downgrade path drops each index with ``IF EXISTS`` so re-running the
downgrade after a partial failure is safe.

Not in this migration (called out in the audit but not fixed here):

- ``ChatSession.project_id`` — chat is currently disabled (see
  ``frontend/src/App.tsx``). When the route is re-enabled the index
  goes in alongside.
- ``CoverageSnapshot.project_id`` — already indirectly indexed via the
  composite ``uq_coverage_project_date`` unique constraint, which leads
  with ``project_id``.
- ``RunDiff.baseline_run_id`` / ``RunBaseline.baseline_run_id`` — lower
  priority (P3 in the audit); add when those flows become hot paths.
"""
from alembic import op


revision = "0082"
down_revision = "0081"
branch_labels = None
depends_on = None


_INDEX_SPECS = (
    ("ix_history_test_case_id", "test_case_history", ["test_case_id"]),
    ("ix_history_test_run_id",  "test_case_history", ["test_run_id"]),
    ("ix_defects_project_id",   "defects",           ["project_id"]),
    ("ix_quality_gates_project","quality_gates",     ["project_id"]),
)


def upgrade() -> None:
    # Each CONCURRENTLY index needs its own autocommit_block — Postgres
    # rejects CONCURRENTLY inside a transaction.
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
    for index_name, table, _ in _INDEX_SPECS:
        with op.get_context().autocommit_block():
            op.drop_index(
                index_name,
                table_name=table,
                postgresql_concurrently=True,
                if_exists=True,
            )
