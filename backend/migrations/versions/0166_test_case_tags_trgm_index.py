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
"""
from alembic import op

revision = "0166"
down_revision = "0165"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Installed by 0058; idempotent for a fresh database that reaches 0166.
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_test_cases_tags_trgm "
        "ON test_cases USING gin ((CAST(tags AS TEXT)) gin_trgm_ops)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_test_cases_tags_trgm")
    # Leave pg_trgm installed: 0058, 0138 and 0165 all depend on it.
