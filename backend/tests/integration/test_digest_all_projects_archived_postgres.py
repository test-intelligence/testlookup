"""W32 F-W30-7 against a real database: an all-projects digest skips archived projects.

Project A (active) has 2 runs at pass rate 100 in the window; project B
(archived: ``is_active = false``, ``archived_at`` set) has 3 runs at pass rate 0.
``generate_digest(db, None, "weekly")`` counts A's runs only; un-archiving B
adds its 3 runs; and the archived-B digest equals the digest of a B with no
runs at all (B contributed nothing).

The database is the shared DSN one (CI: one database for every module), so
the assertions are DELTAS against a baseline digest taken before seeding, and
EXACT (2 runs, 100.0, 5 runs) when the window held no other run. Every seeded
row is deleted after and the touched tables are asserted back to their
pre-test counts. A direct service call on the module's own session: no app
client (Redis / Mongo) is bound.

Requires ``TESTLOOKUP_POSTGRES_TEST_DSN`` migrated to head.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tests.integration.ai_history_seed import dsn

pytest.importorskip("asyncpg")
pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def _counts(factory) -> dict[str, int]:
    from app.models.postgres import Project, TestRun

    async with factory() as db:
        return {
            "projects": int(await db.scalar(select(func.count()).select_from(Project))),
            "test_runs": int(await db.scalar(select(func.count()).select_from(TestRun))),
        }


async def _digest(factory) -> dict:
    from app.services.digest_content_service import generate_digest

    async with factory() as db:
        return await generate_digest(db, None, "weekly")


def _run(project_id, pass_rate: float, created_at: datetime):
    from app.models.postgres import LaunchStatus, TestRun

    run_id = uuid.uuid4()
    return TestRun(
        id=run_id, project_id=project_id, build_number=f"w32d-{run_id.hex[:10]}",
        status=LaunchStatus.PASSED if pass_rate else LaunchStatus.FAILED, branch="main",
        total_tests=10, failed_tests=0 if pass_rate else 10, broken_tests=0,
        pass_rate=pass_rate, created_at=created_at,
    )


@pytest.fixture
async def stores():
    from app.db import postgres as app_pg

    url = dsn()
    await app_pg.dispose_engine_for_loop()
    engine = create_async_engine(url, pool_size=2, max_overflow=0)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    projects: list[uuid.UUID] = []
    try:
        yield factory, projects
    finally:
        from app.models.postgres import Project, TestRun

        async with factory() as db:
            await db.execute(delete(TestRun).where(TestRun.project_id.in_(projects)))
            await db.execute(delete(Project).where(Project.id.in_(projects)))
            await db.commit()
        await engine.dispose()
        await app_pg.dispose_engine_for_loop()


async def test_an_archived_projects_runs_leave_the_all_projects_digest(stores):
    from app.models.postgres import Project, TestRun

    factory, projects = stores
    before = await _counts(factory)
    base = await _digest(factory)
    n0 = base["total_runs"]

    now = datetime.now(timezone.utc)
    a, b = uuid.uuid4(), uuid.uuid4()
    projects += [a, b]
    tag = "w32d" + uuid.uuid4().hex[:8]
    async with factory() as db:
        db.add(Project(id=a, name=f"{tag} active", slug=f"{tag}-active", is_active=True))
        db.add(Project(id=b, name=f"{tag} archived", slug=f"{tag}-archived", is_active=False,
                       archived_at=now - timedelta(hours=1)))
        await db.flush()
        for hours in (2, 3):
            db.add(_run(a, 100.0, now - timedelta(hours=hours)))
        for hours in (4, 5, 6):
            db.add(_run(b, 0.0, now - timedelta(hours=hours)))
        await db.commit()

    archived = await _digest(factory)
    assert archived["total_runs"] == n0 + 2
    if n0 == 0:
        assert archived["avg_pass_rate"] == 100.0

    async with factory() as db:
        await db.execute(update(Project).where(Project.id == b).values(is_active=True, archived_at=None))
        await db.commit()
    restored = await _digest(factory)
    assert restored["total_runs"] == n0 + 5
    if n0 == 0:
        assert restored["avg_pass_rate"] == 40.0

    # An active B with no runs reads exactly like the archived B with its runs.
    async with factory() as db:
        await db.execute(delete(TestRun).where(TestRun.project_id == b))
        await db.commit()
    empty_b = await _digest(factory)
    assert (empty_b["total_runs"], empty_b["avg_pass_rate"]) == (
        archived["total_runs"], archived["avg_pass_rate"],
    )

    async with factory() as db:
        await db.execute(delete(TestRun).where(TestRun.project_id.in_(projects)))
        await db.execute(delete(Project).where(Project.id.in_(projects)))
        await db.commit()
    projects.clear()
    assert await _counts(factory) == before
