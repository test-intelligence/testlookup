"""Skipping the COUNT must never change the reported total.

For an unanchored search the predicate IS the cost — the COUNT is a second full
evaluation of it, not a cheap addendum. A long multi-word term produces enough
trigrams that the GIN index is genuinely the worse plan (measured: seq scan
17.9ms vs a forced index scan at 640ms), so the query scans, and running it
twice to return zero rows doubled the work.

The shortcut is exact rather than an estimate, so these tests compare it against
the COUNT it replaces across every boundary that matters: an exactly-full page,
a partial page, an empty first page, and an over-run page beyond the end. The
last one is the reason the shortcut is conditional — assuming offset + 0 there
would report a total larger than the number of rows that exist.
"""
from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytestmark = pytest.mark.integration

TERM = "countshortcut"
ROWS = 5


def _dsn() -> str:
    value = os.getenv("TESTLOOKUP_POSTGRES_TEST_DSN", "").strip()
    if not value:
        pytest.skip("TESTLOOKUP_POSTGRES_TEST_DSN is not configured")
    return value


class _Fixture:
    """One project with exactly ROWS matching tests, each a distinct logical
    test so DISTINCT ON keeps all of them."""

    def __init__(self) -> None:
        self.project = uuid.uuid4()
        self.run = uuid.uuid4()
        self.tag = uuid.uuid4().hex[:8]

    async def create(self, conn) -> None:
        await conn.execute(
            text(
                "INSERT INTO projects (id, name, slug, is_active) "
                "VALUES (:i, :n, :s, true)"
            ),
            {"i": self.project, "n": "count-" + self.tag, "s": "count-" + self.tag},
        )
        await conn.execute(
            text(
                "INSERT INTO test_runs (id, project_id, build_number, status) "
                "VALUES (:i, :p, 'b1', 'COMPLETED')"
            ),
            {"i": self.run, "p": self.project},
        )
        for n in range(ROWS):
            await conn.execute(
                text(
                    "INSERT INTO test_cases (id, test_run_id, test_name, status, "
                    "test_fingerprint) VALUES (:i, :r, :name, 'FAILED', :f)"
                ),
                {
                    "i": uuid.uuid4(),
                    "r": self.run,
                    "name": TERM + " case " + str(n),
                    "f": "fp-" + self.tag + "-" + str(n),
                },
            )

    async def destroy(self, conn) -> None:
        await conn.execute(
            text("DELETE FROM test_cases WHERE test_run_id = :r"), {"r": self.run}
        )
        await conn.execute(text("DELETE FROM test_runs WHERE id = :i"), {"i": self.run})
        await conn.execute(
            text("DELETE FROM projects WHERE id = :i"), {"i": self.project}
        )


# (page, size, why it matters)
CASES = [
    (1, ROWS + 2, "partial first page — shortcut applies"),
    (1, ROWS, "exactly full page — shortcut must NOT apply"),
    (1, 2, "full page with more behind it"),
    (2, 2, "middle page, still full"),
    (3, 2, "last page, partial"),
    (4, 2, "empty page past the end — shortcut must NOT apply"),
    (99, 10, "far over-run — the case that would report a bogus total"),
]


@pytest.mark.parametrize("page, size, why", CASES)
async def test_total_matches_the_count_it_replaces(page, size, why):
    """The reported total must equal what an unconditional COUNT would give."""
    from app.services import search_service as ss

    engine = create_async_engine(_dsn(), pool_size=3, max_overflow=0)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    fixture = _Fixture()
    try:
        async with engine.begin() as conn:
            await fixture.create(conn)

        async with session_factory() as db:
            _, total, pages = await ss.search_test_cases_query(
                db, q=TERM, page=page, size=size, project_id=str(fixture.project)
            )
            # The reference: run the count statement directly, unconditionally.
            shape = ss.search_shape(TERM, str(fixture.project), None, None, None)
            _, count_stmt = ss.statements_for_shape(shape)
            reference = (
                await db.execute(
                    count_stmt,
                    {
                        "pattern": "%" + TERM + "%",
                        "skip": (page - 1) * size,
                        "take": size,
                        "project_id": fixture.project,
                    },
                )
            ).scalar() or 0

        assert total == reference, (
            why + ": reported total " + str(total) + " but the COUNT says "
            + str(reference)
        )
        assert total == ROWS, (
            why + ": expected " + str(ROWS) + " rows to exist, got " + str(total)
        )
        expected_pages = -(-ROWS // size)
        assert pages == expected_pages, why + ": page count " + str(pages)
    finally:
        async with engine.begin() as conn:
            await fixture.destroy(conn)
        await engine.dispose()


async def test_a_term_that_matches_nothing_reports_zero():
    """The zero-result case is the one the shortcut exists for."""
    from app.services import search_service as ss

    engine = create_async_engine(_dsn(), pool_size=3, max_overflow=0)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    fixture = _Fixture()
    try:
        async with engine.begin() as conn:
            await fixture.create(conn)

        async with session_factory() as db:
            items, total, pages = await ss.search_test_cases_query(
                db, q="zzznothingmatchesthiszzz", page=1, size=20,
                project_id=str(fixture.project),
            )

        assert items == []
        assert total == 0
        assert pages == 0
    finally:
        async with engine.begin() as conn:
            await fixture.destroy(conn)
        await engine.dispose()
