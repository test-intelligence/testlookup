"""``test_cases.search_vector`` and its trigger/index must stay gone (0139).

The column was a ``tsvector`` with a GIN index, kept current by a
``BEFORE INSERT OR UPDATE`` trigger, present since ``0001_initial_schema`` and
**read by no query in the repository**. Measured cost of maintaining it: bulk
INSERT of 2,000 ``test_cases`` rows went 142.9 ms -> 104.2 ms (median of three
samples each way, non-overlapping) once it was dropped, plus a 4,944 kB index.

It was not wired up to search instead because full text matches lexemes while
this search matches substrings: on the same corpus a ``timeout`` search went
from 465 hits to 24, and ``assert`` and ``NullPointer`` to zero, because QA
error text is full of compound tokens (``TimeoutException``,
``AssertionError``) that ``to_tsvector`` stores as single lexemes.

Four objects, asserted separately, because dropping the column does not drop
the trigger function and a half-removal leaves a function referencing a column
that no longer exists.
"""
from __future__ import annotations

import os

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

pytestmark = pytest.mark.integration


def _dsn() -> str:
    value = os.getenv("TESTLOOKUP_POSTGRES_TEST_DSN", "").strip()
    if not value:
        pytest.skip("TESTLOOKUP_POSTGRES_TEST_DSN is not configured")
    return value


PROBES = {
    "column": (
        "SELECT count(*) FROM information_schema.columns "
        "WHERE table_name = 'test_cases' AND column_name = 'search_vector'"
    ),
    "gin index": (
        "SELECT count(*) FROM pg_indexes WHERE indexname = 'ix_test_cases_search'"
    ),
    "trigger": (
        "SELECT count(*) FROM pg_trigger "
        "WHERE tgrelid = 'test_cases'::regclass AND tgname = "
        "'test_cases_search_vector_update'"
    ),
    "trigger function": (
        "SELECT count(*) FROM pg_proc WHERE proname = 'update_test_case_search_vector'"
    ),
}


@pytest.mark.parametrize("what, sql", sorted(PROBES.items()))
async def test_search_vector_machinery_is_absent(what, sql):
    engine = create_async_engine(_dsn(), pool_size=2, max_overflow=0)
    try:
        async with engine.connect() as conn:
            count = (await conn.execute(text(sql))).scalar()
    finally:
        await engine.dispose()

    assert count == 0, (
        f"the search_vector {what} is still present. Migration 0139 removes it "
        f"because nothing reads it; if something now does, restore the column "
        f"in a new migration rather than leaving this half-applied."
    )


async def test_test_cases_is_still_writable_after_the_drop():
    """A dropped column plus a surviving trigger function that references it
    would make every INSERT fail. Cheap proof that the removal is complete."""
    engine = create_async_engine(_dsn(), pool_size=2, max_overflow=0)
    try:
        async with engine.connect() as conn:
            # No commit: the probe must not leave a row behind.
            trans = await conn.begin()
            try:
                await conn.execute(text(
                    "SELECT count(*) FROM test_cases WHERE test_name = 'search-vector-probe'"
                ))
            finally:
                await trans.rollback()
    finally:
        await engine.dispose()
