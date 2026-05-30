"""live_sessions: active-only unique (project_id, run_id); test_case_history: NOT NULL FKs

Revision ID: 0060
Revises: 0059
Create Date: 2026-04-14

Two related data-integrity fixes surfaced in the 2026-04-14 code review:

1. ``live_sessions`` — concurrent runners targeting the same (project_id, run_id)
   could each register a fresh active session, producing duplicate event streams
   that corrupt live dashboards and persisted run data. A partial UNIQUE index
   scoped to ``status = 'active'`` makes the second insert fail fast while still
   allowing a CI job to retry after the first session is completed/stale.

2. ``test_case_history`` — ``test_case_id`` and ``test_run_id`` were declared
   without an explicit ``NOT NULL``. Any orphaned history row defeats the ON
   DELETE CASCADE semantics and produces ghost timeline entries that reference
   nothing. Enforce NOT NULL at the DB level.

Upgrade strategy:
- Drop orphan history rows before tightening the NOT NULL. This is destructive
  but orphans are by definition unreferenced and cannot be repaired.
- For live_sessions, de-duplicate any existing duplicate active sessions per
  (project_id, run_id) by marking all but the most recent one as ``stale``
  before creating the partial UNIQUE index, otherwise index creation will fail.
"""
from alembic import op

# revision identifiers, used by Alembic.
revision = "0060"
down_revision = "0059"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Remove orphaned test_case_history rows. These reference a deleted
    #    test_case or test_run and cannot be recovered.
    op.execute(
        """
        DELETE FROM test_case_history
        WHERE test_case_id IS NULL
           OR test_run_id IS NULL
           OR test_case_id NOT IN (SELECT id FROM test_cases)
           OR test_run_id  NOT IN (SELECT id FROM test_runs)
        """
    )
    op.alter_column("test_case_history", "test_case_id", nullable=False)
    op.alter_column("test_case_history", "test_run_id", nullable=False)

    # 2. De-duplicate active live_sessions before adding the partial UNIQUE.
    #    Keep the most recent active session per (project_id, run_id); mark
    #    older duplicates as 'stale' so they no longer participate in the
    #    partial index.
    op.execute(
        """
        WITH ranked AS (
            SELECT id,
                   ROW_NUMBER() OVER (
                       PARTITION BY project_id, run_id
                       ORDER BY started_at DESC, id DESC
                   ) AS rn
            FROM live_sessions
            WHERE status = 'active'
        )
        UPDATE live_sessions
           SET status = 'stale',
               completed_at = COALESCE(completed_at, now())
         WHERE id IN (SELECT id FROM ranked WHERE rn > 1)
        """
    )

    op.create_index(
        "ux_live_sessions_active_project_run",
        "live_sessions",
        ["project_id", "run_id"],
        unique=True,
        postgresql_where="status = 'active'",
    )


def downgrade() -> None:
    op.drop_index(
        "ux_live_sessions_active_project_run",
        table_name="live_sessions",
    )
    op.alter_column("test_case_history", "test_run_id", nullable=True)
    op.alter_column("test_case_history", "test_case_id", nullable=True)
