"""Indexes for the three run pointers of the canonical catalog (W25b HYG-05).

``canonical_test_cases.first_seen_run_id`` / ``last_seen_run_id`` /
``deleted_at_run_id`` are ``ON DELETE SET NULL`` foreign keys to
``test_runs`` (0075) and none of them was indexed. Every run delete -- the
single-run DELETE, the retention purge (in chunks) and the project reset --
fires, per deleted run and per column, the referential action
``UPDATE ONLY canonical_test_cases SET <col> = NULL WHERE <col> = $1``, which
without an index reads the WHOLE catalog once per run per column:

* ``ix_ctc_first_seen_run`` -- the tests a run introduced;
* ``ix_ctc_last_seen_run`` -- every test of a project's newest run (deleting
  a bad upload, the common explicit delete);
* ``ix_ctc_deleted_at_run`` -- usually nothing (retirements are rare): today
  a full scan to find no row.

Each is PARTIAL (``<col> IS NOT NULL``): the referential predicate uses a
strict ``=``, which implies ``IS NOT NULL`` for the planner whatever the
parameter is, so the generic plan of that statement can use it; and
``deleted_at_run_id`` is NULL on every active row, so that index stays small.
The expressions are the ORM ``Index`` twins (``models/postgres.py``,
``CanonicalTestCase.__table_args__``).

On an EXISTING table, so each index is built CONCURRENTLY inside
``autocommit_block`` (the 0166+ rule,
``tests/regression/test_migration_index_builds_are_concurrent.py``): ingestion
keeps writing during the build. An INVALID leftover of a failed or cancelled
build under the same name is dropped first (the 0214 / 0245 pattern), or
``IF NOT EXISTS`` would keep it for ever; the catalog probe is skipped in
offline (``--sql``) mode. Every statement is a literal written out once per
index so the guard can resolve each one.

No row is read or written besides that catalog probe: no table change, no
backfill, no seed, no flag (the D26 parity clock stays untouched). The
downgrade drops exactly the three indexes, CONCURRENTLY.

Revision ID: 0249
Revises: 0248
"""
import sqlalchemy as sa
from alembic import op

revision = "0249"
down_revision = "0248"
branch_labels = None
depends_on = None

TABLE = "canonical_test_cases"
INDEXES = ("ix_ctc_first_seen_run", "ix_ctc_last_seen_run", "ix_ctc_deleted_at_run")
_FIRST_SEEN_SQL = (
    "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_ctc_first_seen_run "
    "ON canonical_test_cases (first_seen_run_id) WHERE first_seen_run_id IS NOT NULL"
)
_LAST_SEEN_SQL = (
    "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_ctc_last_seen_run "
    "ON canonical_test_cases (last_seen_run_id) WHERE last_seen_run_id IS NOT NULL"
)
_DELETED_AT_SQL = (
    "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_ctc_deleted_at_run "
    "ON canonical_test_cases (deleted_at_run_id) WHERE deleted_at_run_id IS NOT NULL"
)


def upgrade() -> None:
    # On the EXISTING table, so CONCURRENTLY inside autocommit_block (the 0166+ rule); an INVALID
    # leftover of a failed build (same name) is dropped first, or IF NOT EXISTS would keep it for ever.
    invalid = set() if op.get_context().as_sql else _invalid_indexes(op.get_bind())
    with op.get_context().autocommit_block():
        if "ix_ctc_first_seen_run" in invalid:
            op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_ctc_first_seen_run")
        op.execute(_FIRST_SEEN_SQL)
        if "ix_ctc_last_seen_run" in invalid:
            op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_ctc_last_seen_run")
        op.execute(_LAST_SEEN_SQL)
        if "ix_ctc_deleted_at_run" in invalid:
            op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_ctc_deleted_at_run")
        op.execute(_DELETED_AT_SQL)


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_ctc_deleted_at_run")
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_ctc_last_seen_run")
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_ctc_first_seen_run")


def _invalid_indexes(bind) -> set[str]:
    rows = bind.execute(
        sa.text(
            "SELECT c.relname FROM pg_class c JOIN pg_index i ON i.indexrelid = c.oid "
            "WHERE c.relname = ANY(:names) AND NOT i.indisvalid"
        ),
        {"names": list(INDEXES)},
    ).all()
    return {r[0] for r in rows}
