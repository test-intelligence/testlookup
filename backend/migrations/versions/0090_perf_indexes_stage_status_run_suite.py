"""Add two performance indexes flagged by the Phase AUTO perf loop.

Revision ID: 0090
Revises: 0089
Create Date: 2026-06-03

1. ``ix_agent_stage_results_stage_status`` on
   ``agent_stage_results (stage_name, status)`` — backs the repeated-failure
   check in ``agent_cost_service.check_alerts``, which counts FAILED stages by
   ``stage_name`` over a 24h window. The table already has
   ``ix_stage_results_pipeline`` (migration 0004) on ``pipeline_run_id`` — the
   dominant per-pipeline filter — so this only adds the cross-pipeline
   ``(stage_name, status)`` aggregate path. ``agent_stage_results`` is
   append-mostly (one row per pipeline stage), so write overhead is low.

2. ``ix_test_cases_run_suite`` on ``test_cases (test_run_id, suite_name)`` —
   backs the run-scoped suite-breakdown reads (``coverage_stats``,
   ``summary_report`` per-suite, ``suite_history``). ``test_cases`` previously
   had only a GIN trigram index on ``suite_name`` (for ILIKE search, migration
   0058), not a btree for equality / GROUP BY. Mirrors the existing
   ``ix_test_cases_run_status (test_run_id, status)``. ``test_cases`` is the
   highest-write table, so the index is built CONCURRENTLY (below) to avoid a
   write lock during the build.

Each ``CREATE INDEX`` runs ``CONCURRENTLY`` (same pattern as migration 0082) so
production tables don't take a write lock during the build. ``autocommit_block``
is required because ``CREATE INDEX CONCURRENTLY`` cannot run inside a
transaction block. Each index is its own block so a failure on one doesn't roll
back the other, and ``if_not_exists`` / ``if_exists`` make both directions safe
to re-run after a partial failure.
"""
from alembic import op


revision = "0090"
down_revision = "0089"
branch_labels = None
depends_on = None


_INDEX_SPECS = (
    ("ix_agent_stage_results_stage_status", "agent_stage_results", ["stage_name", "status"]),
    ("ix_test_cases_run_suite", "test_cases", ["test_run_id", "suite_name"]),
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
