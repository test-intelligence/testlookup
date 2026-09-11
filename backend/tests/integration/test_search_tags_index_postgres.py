"""``test_cases.tags`` must be trigram-indexed, on exactly the expression search uses.

Re-audit H9. Keyword search over test cases ORed five predicates, and two of
them each defeated the three trigram indexes on ``test_cases``: a run-level
column from another table inside the same OR, and an unindexed
``CAST(tags AS VARCHAR) ILIKE``. The query fix splits the cross-table branch
into its own half of a UNION; migration 0166 fixes the other half with an
expression index on ``CAST(tags AS TEXT)``.

The expression matters. An expression index is only considered for a
predicate written identically, and ``CAST(x AS VARCHAR)`` is a different
expression from ``CAST(x AS TEXT)``. So these tests pin both the index
definition and that the planner can use it for the predicate search actually
emits.

Like ``test_search_step_index_postgres.py``, the plan assertion runs with
``enable_seqscan = off``. Whether the planner *chooses* the index is a
data-volume decision -- on a small CI table a sequential scan is honestly
cheaper. What must be pinned is that the index *can serve* the predicate,
which breaks if the opclass, the expression or the query's cast changes.

Requires ``TESTLOOKUP_POSTGRES_TEST_DSN`` and a disposable database migrated to
at least 0166.
"""
from __future__ import annotations

import os

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

pytestmark = pytest.mark.integration

INDEX = "ix_test_cases_tags_trgm"


def _dsn() -> str:
    value = os.getenv("TESTLOOKUP_POSTGRES_TEST_DSN", "").strip()
    if not value:
        pytest.skip("TESTLOOKUP_POSTGRES_TEST_DSN is not configured")
    return value


async def test_tags_are_trigram_indexed_on_the_text_cast():
    engine = create_async_engine(_dsn(), pool_size=2, max_overflow=0)
    try:
        async with engine.connect() as conn:
            row = (await conn.execute(text(
                "SELECT indexdef FROM pg_indexes "
                "WHERE tablename = 'test_cases' AND indexname = :name"
            ), {"name": INDEX})).first()
        assert row is not None, (
            f"{INDEX} is missing — migration 0166 did not run or was reverted. "
            "Without it the tags branch forces keyword search to check every "
            "test case row by row."
        )
        definition = row.indexdef.lower()
        assert "using gin" in definition, f"{INDEX} is not a GIN index: {row.indexdef}"
        assert "gin_trgm_ops" in definition, (
            f"{INDEX} lacks gin_trgm_ops, so it cannot serve an ILIKE: {row.indexdef}"
        )
        assert "::text" in definition, (
            f"{INDEX} is not on the TEXT cast of tags, so it will never match the "
            f"predicate search emits: {row.indexdef}"
        )
    finally:
        await engine.dispose()


async def test_the_planner_can_use_it_for_the_predicate_search_emits():
    """Pins that the index serves ``CAST(tags AS TEXT) ILIKE``, not just exists."""
    engine = create_async_engine(_dsn(), pool_size=2, max_overflow=0)
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SET enable_seqscan = off"))
            plan_rows = (await conn.execute(text(
                "EXPLAIN (COSTS OFF) SELECT id FROM test_cases "
                "WHERE CAST(tags AS TEXT) ILIKE '%zqxjneedle%'"
            ))).all()
        plan = "\n".join(r[0] for r in plan_rows)
        assert INDEX in plan, (
            f"the planner cannot use {INDEX} for the tags predicate even with "
            f"sequential scans penalised — the expression or opclass no longer "
            f"matches:\n{plan}"
        )
    finally:
        await engine.dispose()


async def test_a_varchar_cast_would_not_have_matched():
    """Why the query's cast had to change too.

    The previous query cast tags to VARCHAR. Pin that an index on the TEXT cast
    does not serve that spelling, so nobody 'simplifies' the query back.
    """
    engine = create_async_engine(_dsn(), pool_size=2, max_overflow=0)
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SET enable_seqscan = off"))
            plan_rows = (await conn.execute(text(
                "EXPLAIN (COSTS OFF) SELECT id FROM test_cases "
                "WHERE CAST(tags AS VARCHAR) ILIKE '%zqxjneedle%'"
            ))).all()
        plan = "\n".join(r[0] for r in plan_rows)
        assert INDEX not in plan, (
            "the TEXT-cast index now serves a VARCHAR-cast predicate; if "
            "Postgres started treating them as equivalent, this test's premise "
            "is gone and the note in global_search_service can be relaxed"
        )
    finally:
        await engine.dispose()
