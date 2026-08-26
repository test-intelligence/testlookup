"""Trigram indexes on test_steps text so keyword search stops scanning them.

Keyword search ORs five predicates, one of which is a correlated EXISTS over
``test_steps`` (a failing step name or assertion message must make its test
findable). ``test_steps.name`` and ``test_steps.assertion_message`` had no
index, so Postgres hashed that subplan by reading **every** step row — a cost
every single search paid, whether or not the term had anything to do with steps.

Measured on a 15,780-test-case / 60,360-step corpus, EXPLAIN ANALYZE of the
search count query:

    Seq Scan on test_steps ......... 46.2 ms   <- before
    Bitmap Index Scan (BitmapOr) ....  0.8 ms  <- after

End-to-end, ``GET /api/v1/search`` at concurrency 1, three runs each:

    before   p50 101.8 / 127.1 / 123.6 ms   rps 6.0 / 7.0 / 7.4
    after    p50  46.9 /  69.3 /  60.3 ms   rps 17.2 / 13.6 / 13.8

That is roughly **2x throughput and half the median latency** on every keyword
search once an install has step data.

Write cost, measured: ``test_steps`` is a delete+reinsert snapshot per canonical
test per ingest, so these GIN indexes are not free. Ten delete+insert cycles of
1,000 steps averaged 118.1 ms without them and 142.1 ms with (+20%). 1,000 steps
for one test is a deliberately harsh fixture — real tests carry tens — and the
cost is paid once per canonical test per ingest against a 2x win on every search.

This does NOT close the ``search_concurrent`` budget (20/s) on its own; the
remaining cost is a sequential scan of ``test_cases`` forced by the cross-table
OR, which no rewrite tried so far improves without regressing broad terms.
"""
from alembic import op

revision = "0138"
down_revision = "0137"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # pg_trgm is already installed by 0058; keep this idempotent for fresh DBs
    # that somehow reach 0138 without it.
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_test_steps_name_trgm "
        "ON test_steps USING gin (name gin_trgm_ops)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_test_steps_assertion_trgm "
        "ON test_steps USING gin (assertion_message gin_trgm_ops)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_test_steps_assertion_trgm")
    op.execute("DROP INDEX IF EXISTS ix_test_steps_name_trgm")
    # Leave pg_trgm installed — 0058's indexes and other features depend on it.
