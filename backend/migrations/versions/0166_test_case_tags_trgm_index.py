"""Trigram index on test_cases.tags, so tag search stops scanning every run.

Re-audit finding H9 said global search "casts ``tags`` JSON->text, defeating the
trigram indexes". Measured, that was half right, and the missing half is what
decides the fix.

Keyword search over test cases ORed five predicates. EXPLAIN against the homelab
deployment (50,510 test_cases / 1,810 test_runs), term ``%zqxjneedle%``:

    A  current query, all five branches .......... no trigram index used
       (Seq Scan test_runs -> per-run index scan -> filter)
    B  same query with the TAGS branch removed .... IDENTICAL plan to A
    C  test_cases columns only (name/suite/error) . BitmapOr over all three
                                                    trigram indexes
    D  C plus the unindexed tags cast ............. back to plan A
    E1 D with this index in place ................. BitmapOr over all FOUR

So two things were each sufficient to defeat the three existing indexes:

1. ``TestRun.primary_suite_name`` sat inside the same OR. A bitmap OR can only
   combine indexes on one table, so one branch on another table forces the
   whole OR to be evaluated row by row. B proves it: remove the tags branch and
   nothing improves. This is fixed in the query (``global_search_service``
   splits the run-level branch into its own half of a UNION).
2. ``CAST(tags AS ...) ILIKE`` had no index, so even a single-table OR collapsed
   (D). This migration fixes that half.

Neither fix works without the other, which is why the finding's own suggested
fix -- an index on tags -- would have been pure write cost on its own.

The index is on ``CAST(tags AS TEXT)`` and the query casts to TEXT to match.
``CAST(x AS VARCHAR)`` is a different expression, and an expression index is
only considered for a predicate written identically; the query previously cast
to VARCHAR (SQLAlchemy ``String``). E1 was produced with exactly this
definition, inside a transaction that was rolled back.

Write cost is not measured here. ``test_cases`` is insert-heavy, and 0138
measured +20% on its own step-snapshot path for two GIN trigram indexes. This is
one index over a short JSON list, and GIN's pending-list (``fastupdate``)
amortises inserts, but that is reasoning, not a measurement.

Built CONCURRENTLY. A plain ``CREATE INDEX`` holds a SHARE lock on
``test_cases`` for the whole build, and every ingest inserts into it: on a
table in the millions a GIN trigram build takes minutes, and every worker's
insert would queue behind it until tasks hit their soft time limits. 0167
builds the same way for the same reason.

``IF NOT EXISTS`` skips the build whenever the name is taken, whatever holds
it: the INVALID index a failed concurrent build leaves behind, or an index of
that name with another definition. Either would be kept, unusable, forever. So
the upgrade reads the catalog first and drops such an index with ``DROP INDEX
CONCURRENTLY`` before it builds. A plain ``DROP INDEX`` -- the only kind a
``DO`` block can run -- takes ACCESS EXCLUSIVE on ``test_cases``: brief, but it
queues behind every open transaction, and every ingest queues behind it.
Whether a valid index is "this index" is decided by the server, which renders
an index back in its own words (casts, parentheses): the definition is built
once on an empty copy of the table, in a savepoint that is rolled back, and the
two renderings are compared.
"""
import sqlalchemy as sa
from alembic import op

revision = "0166"
down_revision = "0165"
branch_labels = None
depends_on = None

INDEX = "ix_test_cases_tags_trgm"
TABLE = "test_cases"
# The index, after "ON test_cases".
DEFINITION = "USING gin ((CAST(tags AS TEXT)) gin_trgm_ops)"
_PROBE = "_alembic_0166_probe"


def upgrade() -> None:
    # Installed by 0058; idempotent for a fresh database that reaches 0166.
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
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
    # Leave pg_trgm installed: 0058, 0138 and 0165 all depend on it.


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
