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
writes ``test_runs``.
"""
from alembic import op

revision = "0167"
down_revision = "0166"
branch_labels = None
depends_on = None

INDEX = "ix_test_runs_project_natural_build"

# Must stay identical to runs_service.natural_build_number_key(), or the
# planner will not match it. tests/integration/test_run_list_natural_order_postgres.py
# proves it does, for the statement the run list executes, under a forced
# generic plan.
_KEY = (
    "CASE WHEN build_number ~ '[0-9]' "
    "THEN CAST(string_to_array(trim(regexp_replace(build_number, '[^0-9]+', ' ', 'g')), ' ') "
    "AS NUMERIC[]) END"
)


def upgrade() -> None:
    with op.get_context().autocommit_block():
        # A CONCURRENTLY build that fails leaves an INVALID index behind, and
        # IF NOT EXISTS below would then keep it, unusable, forever. Drop such
        # a leftover first.
        op.execute(
            "DO $$ BEGIN "
            "IF EXISTS (SELECT 1 FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid "
            f"WHERE c.relname = '{INDEX}' AND NOT i.indisvalid) THEN "
            f"EXECUTE 'DROP INDEX {INDEX}'; "
            "END IF; END $$"
        )
    with op.get_context().autocommit_block():
        op.execute(
            f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {INDEX} ON test_runs "
            f"(project_id, ({_KEY}) DESC NULLS FIRST, build_number DESC, "
            "created_at DESC, id DESC)"
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {INDEX}")
