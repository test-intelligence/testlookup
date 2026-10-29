"""The run context column and the branch-series index (W23b FAIL-13 / FAIL-12).

1. ``test_runs.run_context`` -- ``JSONB NULL``, no default: a catalogue change,
   no table rewrite. The runtime / dependency / image / allowlisted-env context
   a reporter sent beside its results (``schemas.RunContext``: validated, env
   values redacted before anything is stored). No backfill: a run ingested
   before W23b has no context, and "Not recorded by this reporter" is the truth
   for it. No CHECK: the schema and ``run_context_service.validated_or_none``
   own the shape (a CHECK on a JSONB document would need a function and gains
   nothing the stamping path does not already enforce).
2. ``ix_test_runs_project_branch_created`` on ``(project_id,
   (coalesce(lower(branch), '')), created_at)``: one series of a project's runs
   (a branch, case-insensitive, or the runs with no branch), newest first --
   the bisect narrowing's bounded walk (``bisect_service.narrow_range_for_test``).
   No existing index leads with project + branch. On the EXISTING table, so it
   is built CONCURRENTLY inside ``autocommit_block`` (the 0166+ rule,
   ``tests/regression/test_migration_index_builds_are_concurrent.py``), an
   INVALID leftover of a failed build dropped first (the 0214 pattern). The
   expression is the ORM ``Index`` twin (``models/postgres.py``).

The downgrade drops the index (CONCURRENTLY) and the column.

Revision ID: 0245
Revises: 0244
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0245"
down_revision = "0244"
branch_labels = None
depends_on = None

TABLE = "test_runs"
COLUMN = "run_context"
INDEX = "ix_test_runs_project_branch_created"
_INDEX_SQL = (
    "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_test_runs_project_branch_created "
    "ON test_runs (project_id, (coalesce(lower(branch), '')), created_at)"
)


def upgrade() -> None:
    # A nullable column with no default: a catalogue change, no table rewrite.
    op.add_column(TABLE, sa.Column(COLUMN, postgresql.JSONB(astext_type=sa.Text()), nullable=True))
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
    op.drop_column(TABLE, COLUMN)


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
