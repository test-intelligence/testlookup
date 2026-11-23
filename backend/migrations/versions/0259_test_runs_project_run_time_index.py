"""Index a project's runs by run time (product finding D-19).

Charts, the dashboard trend and headline, and the runs-list window now place a
run by WHEN IT EXECUTED: ``coalesce(start_time, created_at)``
(``services/run_time.py``), not by when TestLookup received it. A backfilled
archive, or a nightly report uploaded the next morning, used to land on its
upload day in every chart and every "last N days" window.

``ix_test_runs_project_run_time`` on ``(project_id, (coalesce(start_time,
created_at)))`` serves those windows the way ``ix_test_runs_project_status_created``
and ``ix_test_runs_project_release_created`` served the ``created_at`` ones:
tenant-scoped first, then the window range. Postgres uses an expression index
only for the same expression, which is why the query side spells it in one
place. On the EXISTING table, so it is built CONCURRENTLY inside
``autocommit_block`` (the 0166+ rule,
``tests/regression/test_migration_index_builds_are_concurrent.py``), an INVALID
leftover of a failed build dropped first (the 0214 / 0245 pattern). The
expression is the ORM ``Index`` twin (``models/postgres.py``).

No column changes and no backfill: ``start_time`` already holds the execution
time on every path that knows it, and the expression falls back to
``created_at`` for the runs that have none.

The downgrade drops the index (CONCURRENTLY).

Revision ID: 0259
Revises: 0258
"""
import sqlalchemy as sa
from alembic import op

revision = "0259"
down_revision = "0258"
branch_labels = None
depends_on = None

INDEX = "ix_test_runs_project_run_time"
_INDEX_SQL = (
    "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_test_runs_project_run_time "
    "ON test_runs (project_id, (coalesce(start_time, created_at)))"
)


def upgrade() -> None:
    # An index on an existing table is built CONCURRENTLY (the 0166+ rule); an
    # INVALID leftover of a failed build is dropped first.
    invalid = False if op.get_context().as_sql else _index_is_invalid(op.get_bind())
    with op.get_context().autocommit_block():
        if invalid:
            op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {INDEX}")
        op.execute(_INDEX_SQL)


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {INDEX}")


def _index_is_invalid(bind) -> bool:
    return bool(
        bind.execute(
            sa.text(
                "SELECT 1 FROM pg_class c JOIN pg_index i ON i.indexrelid = c.oid "
                "WHERE c.relname = :name AND NOT i.indisvalid"
            ),
            {"name": INDEX},
        ).first()
    )
