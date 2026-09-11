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

# Renamed from ix_test_runs_project_suite_natural_seq (review R-B45-D-1). The
# first definition keyed the normalised suite NAME and INCLUDEd the column, so
# a String(500) name was stored twice per entry: 500 CJK characters made a
# 3,088-byte index row and 500 emoji a 4,096-byte one, past btree's 2,704-byte
# limit -- the run's INSERT failed, and on a database already holding such a
# run this CREATE INDEX failed and left an INVALID index. A database that ran
# that version has the old name; 0172 drops it and builds this one.
INDEX = "ix_test_runs_project_suite_hash_seq"
TABLE = "test_runs"

# md5 of the normalised name: 32 bytes whatever the name, equal for equal
# names, so the partitions are exactly the old ones. The widest row left is
# a worst-case build_number (String(100)) and its natural-key array, far
# under the limit. No INCLUDE: an index-only scan would need the full column
# in the index -- the very bytes that overflowed -- so each branch reads the
# heap for its rows.
SUITE_KEY = "md5(lower(trim(coalesce(primary_suite_name, ''))))"
NATURAL_KEY = (
    "CASE WHEN build_number ~ '[0-9]' "
    "THEN CAST(string_to_array(trim(regexp_replace(build_number, '[^0-9]+', ' ', 'g')), ' ') "
    "AS NUMERIC[]) END"
)
DEFINITION = (
    f"(project_id, ({SUITE_KEY}), ({NATURAL_KEY}) ASC NULLS LAST, "
    "build_number, created_at, id)"
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
