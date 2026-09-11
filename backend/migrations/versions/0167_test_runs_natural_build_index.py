"""Index the run list's natural build order, so "All time" stops sorting every run.

Re-audit M6. ``GET /api/v1/runs`` orders runs by a natural-sort key computed
from ``build_number`` -- ``ui-2`` before ``ui-10`` -- then ``build_number``,
``created_at`` and ``id``. ``days=0`` ("All time") puts no bound on the rows
that ORDER BY sees, and no existing index could serve it: ``(project_id,
build_number)`` is the right column, but a btree on the raw column cannot supply
an order whose first key is an expression over it. So every page of "All time"
sorted the project's whole run history, and ``/runs/failed-ids`` did the same.

This indexes the key exactly as ``runs_service.natural_build_number_key``
renders it, after ``project_id`` for the per-project filter the list applies,
and in the list's own direction, so a page is an index scan and a LIMIT. Two
properties of that expression changed in the same commit, because the index
depends on them:

* Its constants are SQL literals. An expression index is used only for a query
  expression written identically, and under a generic plan a bind parameter is
  ``$n`` -- never the literal the index was built with.
* It casts to ``numeric[]``, not ``bigint[]``. A digit run longer than nineteen
  digits overflowed ``bigint``; with the expression indexed, that would have
  failed the run's INSERT rather than the listing.

Built CONCURRENTLY, as 0082 and 0152-0153 build theirs, because every ingest
writes ``test_runs``. Whatever already holds the index's name -- the INVALID
index a failed concurrent build leaves, or an index of that name with another
definition -- is dropped CONCURRENTLY first, decided from the catalog exactly
as 0166 decides it (see there for why the server renders both definitions).
"""
import sqlalchemy as sa
from alembic import op

revision = "0167"
down_revision = "0166"
branch_labels = None
depends_on = None

INDEX = "ix_test_runs_project_natural_build"
TABLE = "test_runs"
_PROBE = "_alembic_0167_probe"

# Must stay identical to runs_service.natural_build_number_key(), or the
# planner will not match it. tests/integration/test_run_list_natural_order_postgres.py
# proves it does, for the statement the run list executes, under a forced
# generic plan.
_KEY = (
    "CASE WHEN build_number ~ '[0-9]' "
    "THEN CAST(string_to_array(trim(regexp_replace(build_number, '[^0-9]+', ' ', 'g')), ' ') "
    "AS NUMERIC[]) END"
)
# The index, after "ON test_runs".
DEFINITION = (
    f"(project_id, ({_KEY}) DESC NULLS FIRST, build_number DESC, "
    "created_at DESC, id DESC)"
)


def upgrade() -> None:
    # Offline (--sql) there is no catalog to read, so the script drops whatever
    # holds the name, concurrently, and rebuilds. Delete that line from the
    # script if the index was built by hand ahead of time.
    stale = INDEX if op.get_context().as_sql else _stale_index(op.get_bind())
    with op.get_context().autocommit_block():
        if stale:
            op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {stale}")
        op.execute(f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {INDEX} ON {TABLE} {DEFINITION}")


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {INDEX}")


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
        # the build just as a table does -- and it is not this migration's to
        # drop (code review round 3). Its owner has to free the name.
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
