"""Index the all-projects run list's natural order (re-audit N18).

0167 indexes (project_id, natural key DESC NULLS FIRST, build_number DESC,
created_at DESC, id DESC). That serves a list pinned to ONE project. The
admin "all projects" list and the multi-project list (a member of several
projects) filter ``project_id IN (...)`` -- or only by the live-projects
subquery -- and a btree led by project_id cannot supply an order that
starts with the natural key across projects. So those pages were a Seq Scan
and a Sort of every run (measured: 996 ms and an external merge sort for 20
rows across 156,000 runs), and ``/runs/failed-ids`` did the same.

This indexes the same order without project_id in front. The listing then
walks the index in order, filters project membership per row, and stops at
the LIMIT. For a caller whose projects are a small share of a large instance
the walk can pass many other projects' rows before it fills a page; it never
sorts, and the planner still has 0167's index for the single-project case.

The expression must stay identical to ``runs_service.natural_build_number_key``,
constants as literals (see 0167). Built CONCURRENTLY; an INVALID leftover of
this name is dropped first.
"""
import sqlalchemy as sa
from alembic import op

revision = "0170"
down_revision = "0169"
branch_labels = None
depends_on = None

INDEX = "ix_test_runs_natural_build"
TABLE = "test_runs"

NATURAL_KEY = (
    "CASE WHEN build_number ~ '[0-9]' "
    "THEN CAST(string_to_array(trim(regexp_replace(build_number, '[^0-9]+', ' ', 'g')), ' ') "
    "AS NUMERIC[]) END"
)
DEFINITION = (
    f"(({NATURAL_KEY}) DESC NULLS FIRST, build_number DESC, created_at DESC, id DESC)"
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
