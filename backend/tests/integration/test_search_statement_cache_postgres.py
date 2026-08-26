"""A cached search statement must never carry one caller's scope into the next.

``search_test_cases_query`` reuses a statement per query *shape*, which saved a
measured 4.3ms of a 14.7ms paged query. The saving comes from not rebuilding the
expression tree, and it is only safe because every value the query differs on --
the pattern, the pinned project, the allow-list, status and the date floor --
travels as a named bind parameter supplied at execution time.

If any one of those were left inline it would be baked into the cached object
and served to the *next* caller. For ``project_id`` or the allow-list that is
not a stale number, it is one tenant reading another's rows, and it would look
completely normal: correct-shaped results, no error, just the wrong project's
data.

So the load-bearing test here is not "the cache works" -- it is that the SECOND
caller through a warm cache entry gets its OWN scope. Everything else in this
file supports that claim.
"""
from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytestmark = pytest.mark.integration


def _dsn() -> str:
    value = os.getenv("TESTLOOKUP_POSTGRES_TEST_DSN", "").strip()
    if not value:
        pytest.skip("TESTLOOKUP_POSTGRES_TEST_DSN is not configured")
    return value


TERM = "cachedneedle"


class _TwoProjects:
    """Two projects, each with one test whose name carries the shared term.

    Both match the search, so a scope that leaks shows up as the wrong count
    rather than as an empty result -- a failure mode that an "is it non-empty"
    assertion would sail straight past.
    """

    def __init__(self) -> None:
        self.a = uuid.uuid4()
        self.b = uuid.uuid4()
        self.run_a = uuid.uuid4()
        self.run_b = uuid.uuid4()
        self.tag = uuid.uuid4().hex[:8]

    async def create(self, conn) -> None:
        for pid, label, runs in ((self.a, "a", 1), (self.b, "b", 2)):
            await conn.execute(
                text(
                    "INSERT INTO projects (id, name, slug, is_active) "
                    "VALUES (:i, :n, :s, true)"
                ),
                {"i": pid, "n": "cache-" + label + "-" + self.tag,
                 "s": "cache-" + label + "-" + self.tag},
            )
        # Project A gets ONE matching test, project B gets TWO, so a leak is
        # visible as a count mismatch in either direction.
        plan = ((self.a, self.run_a, 1), (self.b, self.run_b, 2))
        for pid, rid, how_many in plan:
            await conn.execute(
                text(
                    "INSERT INTO test_runs (id, project_id, build_number, status) "
                    "VALUES (:i, :p, 'b1', 'COMPLETED')"
                ),
                {"i": rid, "p": pid},
            )
            for n in range(how_many):
                await conn.execute(
                    text(
                        "INSERT INTO test_cases (id, test_run_id, test_name, status, "
                        "test_fingerprint) VALUES (:i, :r, :name, 'FAILED', :f)"
                    ),
                    {
                        "i": uuid.uuid4(),
                        "r": rid,
                        "name": TERM + " case " + str(n),
                        "f": "fp-" + self.tag + "-" + str(pid)[:8] + "-" + str(n),
                    },
                )

    async def destroy(self, conn) -> None:
        await conn.execute(
            text("DELETE FROM test_cases WHERE test_run_id IN (:a, :b)"),
            {"a": self.run_a, "b": self.run_b},
        )
        await conn.execute(
            text("DELETE FROM test_runs WHERE id IN (:a, :b)"),
            {"a": self.run_a, "b": self.run_b},
        )
        await conn.execute(
            text("DELETE FROM projects WHERE id IN (:a, :b)"), {"a": self.a, "b": self.b}
        )


async def test_a_warm_cache_entry_does_not_leak_the_previous_project():
    """The whole reason this file exists.

    Both calls hit the SAME cached statement -- identical shape -- and must
    still see only their own project.
    """
    from app.services import search_service as ss

    engine = create_async_engine(_dsn(), pool_size=3, max_overflow=0)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    fixture = _TwoProjects()
    try:
        async with engine.begin() as conn:
            await fixture.create(conn)

        shape_a = shape_b = None
        async with session_factory() as db:
            _, total_a, _ = await ss.search_test_cases_query(
                db, q=TERM, page=1, size=20, project_id=str(fixture.a)
            )
            shape_a = ss.search_shape(TERM, str(fixture.a), None, None, None)
            _, total_b, _ = await ss.search_test_cases_query(
                db, q=TERM, page=1, size=20, project_id=str(fixture.b)
            )
            shape_b = ss.search_shape(TERM, str(fixture.b), None, None, None)

        assert shape_a == shape_b, (
            "the two calls must share a cache entry for this test to mean "
            "anything -- if their shapes differ the leak path is not exercised"
        )
        assert total_a == 1, (
            "project A saw " + str(total_a) + " rows, expected its own 1"
        )
        assert total_b == 2, (
            "project B saw " + str(total_b) + " rows, expected its own 2. Getting "
            "A's 1 here means the cached statement carried A's project_id."
        )
    finally:
        async with engine.begin() as conn:
            await fixture.destroy(conn)
        await engine.dispose()


async def test_an_allow_list_is_not_baked_into_the_cached_statement():
    """Same hazard for the non-admin fan-out path, which binds a list."""
    from app.services import search_service as ss

    engine = create_async_engine(_dsn(), pool_size=3, max_overflow=0)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    fixture = _TwoProjects()
    try:
        async with engine.begin() as conn:
            await fixture.create(conn)

        async with session_factory() as db:
            _, only_a, _ = await ss.search_test_cases_query(
                db, q=TERM, page=1, size=20, allowed_project_ids=[fixture.a]
            )
            _, only_b, _ = await ss.search_test_cases_query(
                db, q=TERM, page=1, size=20, allowed_project_ids=[fixture.b]
            )
            _, both, _ = await ss.search_test_cases_query(
                db, q=TERM, page=1, size=20,
                allowed_project_ids=[fixture.a, fixture.b],
            )

        assert only_a == 1, "allow-list [A] saw " + str(only_a)
        assert only_b == 2, (
            "allow-list [B] saw " + str(only_b) + " -- the previous call's "
            "allow-list was baked into the cached statement"
        )
        assert both == 3, "allow-list [A, B] saw " + str(both) + ", expected 3"
    finally:
        async with engine.begin() as conn:
            await fixture.destroy(conn)
        await engine.dispose()


async def test_paging_is_bound_not_baked():
    """Page 1 and page 2 share a shape, so the offset must be a parameter."""
    from app.services import search_service as ss

    engine = create_async_engine(_dsn(), pool_size=3, max_overflow=0)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    fixture = _TwoProjects()
    try:
        async with engine.begin() as conn:
            await fixture.create(conn)

        async with session_factory() as db:
            first, total, _ = await ss.search_test_cases_query(
                db, q=TERM, page=1, size=1, project_id=str(fixture.b)
            )
            second, _, _ = await ss.search_test_cases_query(
                db, q=TERM, page=2, size=1, project_id=str(fixture.b)
            )

        assert total == 2
        assert len(first) == 1 and len(second) == 1
        assert first[0]["test_case_id"] != second[0]["test_case_id"], (
            "page 2 returned page 1's row -- the offset was baked into the "
            "cached statement instead of bound per call"
        )
    finally:
        async with engine.begin() as conn:
            await fixture.destroy(conn)
        await engine.dispose()
