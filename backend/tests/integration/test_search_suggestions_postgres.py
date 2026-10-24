"""W12 SRCH-03 against real Postgres: ``GET /search/suggested``'s three reads.

* failing tests: the top five by failures in the last 7 days -- an 8-day-old
  failure, another project's failure and a PASSED result do not count;
* open defects: the newest with a Jira key, OPEN / IN_PROGRESS only;
* the active release only;
* an inactive project: three empty groups.

The handler is called directly on a real session as an ADMIN (the
authorisation rule is pinned in ``tests/test_search_suggestions.py``).

Requires ``TESTLOOKUP_POSTGRES_TEST_DSN`` and a disposable database migrated to head.
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.postgres import User, UserRole

pytestmark = pytest.mark.integration


def _dsn() -> str:
    value = os.getenv("TESTLOOKUP_POSTGRES_TEST_DSN", "").strip()
    if not value:
        pytest.skip("TESTLOOKUP_POSTGRES_TEST_DSN is not configured")
    return value


ADMIN = User(id=uuid.uuid4(), username="w12gb_admin", email="w12gb-admin@example.com", role=UserRole.ADMIN, is_active=True)


class _World:
    def __init__(self) -> None:
        self.tag = uuid.uuid4().hex[:8]
        self.project, self.other, self.inactive = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        self.runs: list[uuid.UUID] = []

    async def _run(self, conn, project, created) -> uuid.UUID:
        run = uuid.uuid4()
        self.runs.append(run)
        await conn.execute(text(
            "INSERT INTO test_runs (id, project_id, build_number, status, created_at) "
            "VALUES (:i, :p, :b, 'FAILED', :c)"
        ), {"i": run, "p": project, "b": f"b-{self.tag}", "c": created})
        return run

    async def _case(self, conn, run, name, status, created) -> None:
        await conn.execute(text(
            "INSERT INTO test_cases (id, test_run_id, test_name, status, test_fingerprint, created_at) "
            "VALUES (:i, :r, :n, :s, :f, :c)"
        ), {"i": uuid.uuid4(), "r": run, "n": name, "s": status, "f": f"fp-{uuid.uuid4().hex}", "c": created})

    async def create(self, conn) -> None:
        now = datetime.now(timezone.utc)
        old = now - timedelta(days=8)
        for pid, active in ((self.project, True), (self.other, True), (self.inactive, False)):
            await conn.execute(text(
                "INSERT INTO projects (id, name, slug, is_active) VALUES (:i, :n, :n, :a)"
            ), {"i": pid, "n": f"w12gb-sugg-{pid.hex[:6]}-{self.tag}", "a": active})

        recent = await self._run(conn, self.project, now)
        stale = await self._run(conn, self.project, old)
        foreign = await self._run(conn, self.other, now)
        inactive = await self._run(conn, self.inactive, now)
        # failures in the window: t1 x6, t2 x5 (one BROKEN), t3 x4, t4 x3, t5 x2, t6 x1 (6th: cut by the top five)
        for name, count in (("t1", 6), ("t2", 5), ("t3", 4), ("t4", 3), ("t5", 2), ("t6", 1)):
            for index in range(count):
                status = "BROKEN" if (name == "t2" and index == 0) else "FAILED"
                await self._case(conn, recent, f"{name}-{self.tag}", status, now)
        for _ in range(10):
            await self._case(conn, recent, f"passing-{self.tag}", "PASSED", now)
            await self._case(conn, stale, f"stale-{self.tag}", "FAILED", old)
            await self._case(conn, foreign, f"foreign-{self.tag}", "FAILED", now)
            await self._case(conn, inactive, f"inactive-{self.tag}", "FAILED", now)

        defects = (
            (self.project, f"PAY-1-{self.tag}", "OPEN", "HIGH", now - timedelta(hours=3)),
            (self.project, f"PAY-2-{self.tag}", "IN_PROGRESS", None, now - timedelta(hours=1)),
            (self.project, f"PAY-3-{self.tag}", "RESOLVED", "LOW", now),
            (self.project, None, "OPEN", "HIGH", now),
            (self.other, f"OTH-1-{self.tag}", "OPEN", "HIGH", now),
            (self.inactive, f"INA-1-{self.tag}", "OPEN", "HIGH", now),
        )
        for project, key, status, severity, created in defects:
            await conn.execute(text(
                "INSERT INTO defects (id, project_id, jira_ticket_id, resolution_status, severity, created_at) "
                "VALUES (:i, :p, :k, :s, :sev, :c)"
            ), {"i": uuid.uuid4(), "p": project, "k": key, "s": status, "sev": severity, "c": created})

        releases = (
            (self.project, f"2026.10-{self.tag}", False),
            (self.project, f"2026.11 RC-{self.tag}", True),
            (self.inactive, f"2026.12-{self.tag}", True),
        )
        for project, name, active in releases:
            await conn.execute(text(
                "INSERT INTO releases (id, project_id, name, status, is_active) VALUES (:i, :p, :n, 'planning', :a)"
            ), {"i": uuid.uuid4(), "p": project, "n": name, "a": active})

    async def destroy(self, conn) -> None:
        projects = [self.project, self.other, self.inactive]
        await conn.execute(text("DELETE FROM test_cases WHERE test_run_id = ANY(:r)"), {"r": self.runs})
        await conn.execute(text("DELETE FROM test_runs WHERE id = ANY(:r)"), {"r": self.runs})
        await conn.execute(text("DELETE FROM defects WHERE project_id = ANY(:p)"), {"p": projects})
        await conn.execute(text("DELETE FROM releases WHERE project_id = ANY(:p)"), {"p": projects})
        await conn.execute(text("DELETE FROM projects WHERE id = ANY(:p)"), {"p": projects})


@pytest.fixture
async def world():
    engine = create_async_engine(_dsn(), pool_size=3, max_overflow=0)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    w = _World()
    async with engine.begin() as conn:
        await w.create(conn)
    try:
        yield w, factory
    finally:
        async with engine.begin() as conn:
            await w.destroy(conn)
        await engine.dispose()


async def _suggested(factory, project):
    from app.models.schemas import SearchSuggestionsResponse
    from app.routers.search import suggested_searches

    async with factory() as db:
        body = await suggested_searches(project_id=str(project), db=db, current_user=ADMIN)
    SearchSuggestionsResponse.model_validate(body)  # what the route's response_model reads
    return body


async def test_the_three_groups(world):
    w, factory = world
    body = await _suggested(factory, w.project)
    assert [(s["label"], s["detail"]) for s in body["failing_tests"]] == [
        (f"t1-{w.tag}", "6 failures in 7 days"),
        (f"t2-{w.tag}", "5 failures in 7 days"),
        (f"t3-{w.tag}", "4 failures in 7 days"),
        (f"t4-{w.tag}", "3 failures in 7 days"),
        (f"t5-{w.tag}", "2 failures in 7 days"),
    ], "the 8-day-old, the other project's and the passing results must not count"
    assert all(s["q"] == s["label"] and s["scope"] == "tests" for s in body["failing_tests"])
    assert [(s["label"], s["detail"]) for s in body["open_defects"]] == [
        (f"PAY-2-{w.tag}", "In progress"),
        (f"PAY-1-{w.tag}", "Open · HIGH"),
    ]
    assert body["active_release"]["label"] == f"2026.11 RC-{w.tag}"
    assert body["active_release"]["q"] == f'release:"2026.11 RC-{w.tag}" status:failed'


async def test_an_inactive_project_has_nothing_to_suggest(world):
    w, factory = world
    body = await _suggested(factory, w.inactive)
    assert body["failing_tests"] == [] and body["open_defects"] == [] and body["active_release"] is None
