"""Index the "Run #N" window, so a page stops sorting each suite's whole history.

Re-audit N17. ``runs_service.fetch_run_seq_map`` numbers runs 1..N within
each (project, normalised primary suite) partition, in natural build order,
over the partition's ENTIRE history -- by design, so a run's number is stable
across pages and filters. Every /runs, /live and /my-failures page pays it.

0167's index cannot serve it: its second column is the natural key, not the
suite, and the window reached its rows through a BitmapOr over the page's
(project, suite) pairs, which returns rows unordered. So every page sorted
every run of every suite on it (measured: 40,000 runs quicksorted for a
20-row page).

This indexes (project_id, the normalised suite, the natural key ASC NULLS
LAST, build_number, created_at, id) -- the window's partition then its
order. ``fetch_run_seq_map`` now issues one UNION ALL branch per pair with
both partition keys pinned by equalities, so each branch reads its rows
already in window order: no Sort.

Not persisted at ingest: the order is natural, so a run ingested late with a
smaller build number belongs in the middle and would renumber every later
run -- a stored sequence is wrong the moment that happens.

Expressions must stay identical to ``runs_service._SUITE_NORM`` and
``runs_service.natural_build_number_key()``, constants as literals, or the
planner will not match them under a generic plan.

Built CONCURRENTLY (every ingest writes test_runs). An INVALID leftover of
this name from a failed concurrent build is dropped first; the name held by
something else stops the migration.
"""
import sqlalchemy as sa
from alembic import op

revision = "0169"
down_revision = "0168"
branch_labels = None
depends_on = None

INDEX = "ix_test_runs_project_suite_natural_seq"
TABLE = "test_runs"

SUITE_KEY = "lower(trim(coalesce(primary_suite_name, '')))"
NATURAL_KEY = (
    "CASE WHEN build_number ~ '[0-9]' "
    "THEN CAST(string_to_array(trim(regexp_replace(build_number, '[^0-9]+', ' ', 'g')), ' ') "
    "AS NUMERIC[]) END"
)
# INCLUDE (primary_suite_name): an index-only scan is possible only when every
# base column the query touches is stored in the index -- an expression over a
# column does not count as the column. Without it the planner fetched every
# heap row through a Bitmap Heap Scan (unordered) and sorted again: measured
# 52.9 ms and four Sorts, against 22.1 ms, an Index Only Scan with no heap
# fetches and no Sort, over the same 40,000 runs.
DEFINITION = (
    f"(project_id, ({SUITE_KEY}), ({NATURAL_KEY}) ASC NULLS LAST, "
    "build_number, created_at, id) INCLUDE (primary_suite_name)"
)


def upgrade() -> None:
    stale = INDEX if op.get_context().as_sql else _invalid_leftover(op.get_bind())
    with op.get_context().autocommit_block():
        if stale:
            op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {stale}")
        op.execute(f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {INDEX} ON {TABLE} {DEFINITION}")


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {INDEX}")


def _invalid_leftover(bind) -> str | None:
    """An INVALID index of this name on test_runs (a failed CONCURRENTLY build),
    which ``IF NOT EXISTS`` would otherwise keep for ever."""
    found = bind.execute(
        sa.text(
            "SELECT format('%I.%I', n.nspname, c.relname) AS name, c.relkind = 'i' AS is_index, "
            "i.indisvalid AS valid, i.indrelid = CAST(:table AS regclass) AS on_table "
            "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            "LEFT JOIN pg_index i ON i.indexrelid = c.oid "
            "WHERE c.relname = :index AND n.nspname = current_schema()"
        ),
        {"index": INDEX, "table": TABLE},
    ).first()
    if found is None:
        return None
    if not found.is_index or not found.on_table:
        raise RuntimeError(
            f"{found.name} exists and is not an index on {TABLE}, so CREATE INDEX IF "
            f"NOT EXISTS would skip building {INDEX}; rename it, then rerun the migration"
        )
    return None if found.valid else found.name
