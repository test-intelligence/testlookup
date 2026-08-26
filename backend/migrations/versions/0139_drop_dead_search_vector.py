"""Drop ``test_cases.search_vector`` — maintained on every write, read by nothing.

``search_vector`` is a ``tsvector`` column with a GIN index
(``ix_test_cases_search``) kept up to date by a ``BEFORE INSERT OR UPDATE``
trigger over test_name, suite_name, class_name and error_message. All three
have been in place since ``0001_initial_schema``.

No query in the repository ever reads it. The only references outside the
migrations are the column declaration and the ``Index(...)`` in
``app/models/postgres.py``; a grep across ``backend/app``, ``cli``, ``mcp``,
``client`` and ``frontend/src`` finds nothing else.

It is not free. Measured with the shape ingestion actually uses — bulk INSERT of
2,000 ``test_cases`` rows, three samples each way, on a 15,780-row table:

    with trigger + GIN index ....... 142.9 / 145.1 / 123.6 ms
    without ........................ 104.2 / 114.4 /  93.0 ms

Median 142.9 -> 104.2 ms, about **27% off bulk insert**, with the two sample
sets not overlapping (slowest "without" run still beat the fastest "with" run).
The GIN index is also 4,944 kB against a 30 MB table.

An earlier probe using UPDATEs over a single run's rows showed no difference at
all: ~54 rows is too small a batch for the trigger and index maintenance to
surface above run-to-run noise. Measure the operation the system actually
performs, at the size it performs it.

Why it is not wired up to search instead — measured on the same corpus, against
the current ILIKE search:

    term            ILIKE hits   tsvector hits   lost
    timeout                465              24    441  (95%)
    assert                 660               0    660  (100%)
    NullPointer            382               0    382  (100%)

Full text matches lexemes; this search matches substrings. QA error text is
full of compound tokens — ``TimeoutException``, ``AssertionError``,
``NullPointerException`` — which ``to_tsvector`` stores as single lexemes, so a
search for "timeout" stops finding them. Swapping also does NOT remove the
sequential scan it was hoped to fix: the OR spans ``test_runs`` and a correlated
EXISTS, so Postgres still applies a join filter and never uses the GIN index
(18.8 ms vs 14.0 ms for the ILIKE form — slower).

pg_trgm, already installed and already used by ``ix_test_cases_name_trgm`` and
friends, is the right index for substring search here.

The downgrade restores the column, index, function and trigger, and backfills
existing rows — a trigger alone would only populate rows written after it.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import TSVECTOR

revision = "0139"
down_revision = "0138"
branch_labels = None
depends_on = None


_FUNCTION_SQL = """
CREATE OR REPLACE FUNCTION update_test_case_search_vector()
RETURNS TRIGGER AS $$
BEGIN
    NEW.search_vector := to_tsvector('english',
        COALESCE(NEW.test_name, '') || ' ' ||
        COALESCE(NEW.suite_name, '') || ' ' ||
        COALESCE(NEW.class_name, '') || ' ' ||
        COALESCE(NEW.error_message, '')
    );
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""


def upgrade() -> None:
    # Trigger first: it references the column.
    op.execute("DROP TRIGGER IF EXISTS test_cases_search_vector_update ON test_cases")
    op.execute("DROP FUNCTION IF EXISTS update_test_case_search_vector()")
    op.execute("DROP INDEX IF EXISTS ix_test_cases_search")
    op.drop_column("test_cases", "search_vector")


def downgrade() -> None:
    op.add_column("test_cases", sa.Column("search_vector", TSVECTOR, nullable=True))
    op.execute(_FUNCTION_SQL)
    op.execute(
        "CREATE TRIGGER test_cases_search_vector_update "
        "BEFORE INSERT OR UPDATE ON test_cases "
        "FOR EACH ROW EXECUTE FUNCTION update_test_case_search_vector()"
    )
    # Backfill: the trigger only fires on rows written after it exists, so
    # without this the restored column is empty for every existing row and the
    # GIN index below indexes nothing.
    op.execute(
        "UPDATE test_cases SET search_vector = to_tsvector('english', "
        "COALESCE(test_name, '') || ' ' || COALESCE(suite_name, '') || ' ' || "
        "COALESCE(class_name, '') || ' ' || COALESCE(error_message, ''))"
    )
    op.execute(
        "CREATE INDEX ix_test_cases_search ON test_cases USING gin (search_vector)"
    )
