"""Test results keep when they ran: start, finish, phase split, worker (EXEC-01).

``test_cases.duration_ms`` was the only timing a result kept, although most
report formats say more: TestNG, NUnit and TRX write a start and an end per
test, TestNG reports its ``@BeforeMethod`` / ``@AfterMethod`` time, pytest its
setup / call / teardown, Playwright the worker that ran each test. Seven
nullable columns hold what the report said, and nothing when it said nothing:

* ``started_at`` / ``finished_at`` (TIMESTAMPTZ, UTC);
* ``setup_ms`` / ``call_ms`` / ``teardown_ms`` (INT): fixture time attributed to
  the test, the test body, after-fixture time;
* ``worker_id`` (VARCHAR(64)): the parallel worker / runner slot;
* ``timing_source`` (VARCHAR(16)): ``report`` | ``derived`` | ``live``
  (``app.services.report_timings.TIMING_SOURCES``).

``duration_ms`` keeps its meaning (the total the framework reported). No
backfill is possible: the timestamps were never stored.

``ADD COLUMN ... NULL`` without a default is a catalog change in Postgres 11+,
so the 1M-row table is not rewritten.

``ix_test_cases_run_duration`` becomes ``(test_run_id, duration_ms DESC NULLS
LAST)``: "the slowest N tests of this run" and the run's timing breakdown read
it in order, unknown durations last, without sorting a 10k-row run. The NAME is
not new: 0004 built ``(test_run_id, duration_ms)`` under it (never declared in
the ORM). A backward scan of that one yields NULLs first, so it cannot serve
``DESC NULLS LAST``, and two indexes on the same columns of the insert-heaviest
table would be pure write cost. So this replaces it -- found by an integration
test, after the first version's ``IF NOT EXISTS`` silently kept 0004's index.

Whatever holds the name and is not this very index, valid (0004's index, the
INVALID leftover of a failed concurrent build, another definition) is dropped
CONCURRENTLY first, decided from the catalog exactly as 0166 / 0167 decide it:
the server renders this definition on an empty copy of the table, in a
savepoint that is rolled back, and the two renderings are compared. Built and
dropped CONCURRENTLY inside an autocommit block
(``tests/regression/test_migration_index_builds_are_concurrent.py``): every
ingest writes ``test_cases``. The columns commit before the build.

The downgrade undoes exactly that: it drops this index and rebuilds 0004's
under the same name, then drops the columns.

Revision ID: 0202
Revises: 0201
"""

from alembic import op
import sqlalchemy as sa


revision = "0202"
down_revision = "0201"
branch_labels = None
depends_on = None

TABLE = "test_cases"
INDEX = "ix_test_cases_run_duration"
#: The index, after ``ON test_cases``.
DEFINITION = "(test_run_id, duration_ms DESC NULLS LAST)"
#: What 0004 built under the same name; the downgrade puts it back.
PREVIOUS_DEFINITION = "(test_run_id, duration_ms)"
_PROBE = "_alembic_0202_probe"


def _columns() -> list[sa.Column]:
    return [
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("setup_ms", sa.Integer(), nullable=True),
        sa.Column("call_ms", sa.Integer(), nullable=True),
        sa.Column("teardown_ms", sa.Integer(), nullable=True),
        sa.Column("worker_id", sa.String(64), nullable=True),
        sa.Column("timing_source", sa.String(16), nullable=True),
    ]


#: Column names in the order the upgrade adds them (the downgrade drops them in reverse).
COLUMNS = tuple(column.name for column in _columns())


def upgrade() -> None:
    for column in _columns():
        op.add_column(TABLE, column)
    # Offline (--sql) there is no catalog to read: drop whatever holds the
    # name, concurrently, and rebuild.
    stale = INDEX if op.get_context().as_sql else _stale_index(op.get_bind())
    with op.get_context().autocommit_block():
        if stale:
            op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {stale}")
        op.execute(f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {INDEX} ON {TABLE} {DEFINITION}")


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {INDEX}")
        op.execute(f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {INDEX} ON {TABLE} {PREVIOUS_DEFINITION}")
    for name in reversed(COLUMNS):
        op.drop_column(TABLE, name)


def _stale_index(bind) -> str | None:
    """The qualified name of whatever holds INDEX's name but is not this index.

    None when the name is free, or already holds this index, valid.
    """
    found = bind.execute(
        sa.text(
            "SELECT format('%I.%I', n.nspname, c.relname) AS name, c.relkind = 'i' AS is_index, "
            "i.indisvalid AS valid, i.indrelid = t.oid AS on_table, "
            "pg_get_indexdef(i.indexrelid) AS definition "
            "FROM pg_class t "
            "JOIN pg_class c ON c.relnamespace = t.relnamespace AND c.relname = :index "
            "JOIN pg_namespace n ON n.oid = c.relnamespace "
            "LEFT JOIN pg_index i ON i.indexrelid = c.oid "
            "WHERE t.oid = CAST(:table AS regclass)"
        ),
        {"index": INDEX, "table": TABLE},
    ).first()
    if found is None:
        return None
    if not found.is_index:
        raise RuntimeError(
            f"{found.name} exists and is not an index, so CREATE INDEX IF NOT EXISTS "
            f"would skip building {INDEX}; rename or drop it, then rerun the migration"
        )
    if not found.on_table:
        # Index names are unique per schema, so another table's index blocks
        # the build just as a table does -- and it is not this migration's to drop.
        raise RuntimeError(
            f"{found.name} is an index on another table, so CREATE INDEX IF NOT "
            f"EXISTS would skip building {INDEX}; rename it, then rerun the migration"
        )
    if found.valid and _rendered(found.definition) == _rendered(_as_built(bind)):
        return None
    return found.name


def _as_built(bind) -> str:
    """How this server renders DEFINITION, read off an index on an empty copy of
    the table. The copy lives in a savepoint that is rolled back, locks included."""
    savepoint = bind.begin_nested()
    try:
        bind.execute(sa.text(f"CREATE TABLE {_PROBE} (LIKE {TABLE})"))
        bind.execute(sa.text(f"CREATE INDEX {_PROBE}_ix ON {_PROBE} {DEFINITION}"))
        return bind.execute(
            sa.text("SELECT pg_get_indexdef(CAST(:index AS regclass))"),
            {"index": f"{_PROBE}_ix"},
        ).scalar_one()
    finally:
        savepoint.rollback()


def _rendered(definition: str) -> tuple[bool, str]:
    """A rendered definition without the index's own name and table."""
    head, _, tail = definition.partition(" USING ")
    return head.startswith("CREATE UNIQUE "), tail
