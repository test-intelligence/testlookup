"""W13 TCL-04 against real PostgreSQL: export -> import round trip.

8. N generated cases (a seeded generator inside the test: titles starting
   ``=`` / ``-`` / ``+`` / ``@``, unicode, tags, steps, suites) are exported by
   the REAL Excel export route of project A and imported into project B through
   the real import route: the same title, type, priority, severity, feature
   area, objective, preconditions, step actions, expected result, automated
   flag, automation status, tags, suite and description; every case a Draft at
   version 1. A re-import of the same file -> every row ``duplicate``.
"""
from __future__ import annotations

import os
import random
import uuid
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.deps import get_current_active_user, get_db
from app.core.test_library_vocab import AUTOMATION_STATUSES, CASE_PRIORITIES, CASE_SEVERITIES
from app.models.postgres import ManagedTestCase, Project, User, UserRole
from app.models.postgres import TestCaseAuditLog as AuditRow
from app.routers import test_management as tm_router

asyncpg = pytest.importorskip("asyncpg")
pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

N = 30
FIELDS = ("title", "test_type", "priority", "severity", "feature_area", "objective", "preconditions",
          "expected_result", "is_automated", "automation_status", "tags", "suite_name", "description")


def _dsn() -> str:
    value = os.getenv("TESTLOOKUP_POSTGRES_TEST_DSN", "").strip()
    if not value:
        pytest.skip("TESTLOOKUP_POSTGRES_TEST_DSN is not configured")
    return value


@pytest.fixture
async def pg_engine():
    # The app's own engine is lazily built on whichever loop first touches it
    # (a route's activity write, a service's own session); dispose it before
    # and after, or its pooled connections outlive this test's loop and the
    # next module that uses AsyncSessionLocal gets "Event loop is closed".
    from app.db import postgres as app_postgres

    dsn = _dsn()
    await app_postgres.dispose_engine_for_loop()
    engine = create_async_engine(dsn, pool_size=4, max_overflow=0)
    try:
        yield engine
    finally:
        await engine.dispose()
        await app_postgres.dispose_engine_for_loop()


def _generated(project_id) -> list[ManagedTestCase]:
    """A seeded, varied library (built inside the test, never at collection)."""
    rng = random.Random(1307)
    prefixes = ["=SUM(A{i}:A2)", "-minus {i}", "+plus {i}", "@at {i}", "Ünïcødé {i} 日本語", "Plain case {i}"]
    texts = ["=1+1 objective", "- a dash first", "Checks the basket", "Ünïcødé → ✓", "  leading spaces"]
    cases = []
    for i in range(N):
        steps = [{"step_number": n + 1, "action": f"{rng.choice(texts).strip()} step {n + 1}", "expected_result": ""}
                 for n in range(rng.randint(0, 3))]
        cases.append(ManagedTestCase(
            id=uuid.uuid4(), project_id=project_id, title=prefixes[i % len(prefixes)].format(i=i),
            status=rng.choice(["draft", "approved", "active", "review_requested"]), version=rng.randint(1, 4),
            test_type=rng.choice(["functional", "e2e", "smoke", "regression"]),
            priority=rng.choice(CASE_PRIORITIES), severity=rng.choice(CASE_SEVERITIES),
            feature_area=rng.choice([None, "Payments", "=area"]), objective=rng.choice([None, *texts]),
            preconditions=rng.choice([None, "Signed in", "@admin present"]), steps=steps or None,
            expected_result=rng.choice([None, "Paid", "=OK"]), is_automated=rng.random() < 0.5,
            automation_status=rng.choice(AUTOMATION_STATUSES),
            tags=rng.sample(["smoke", "pay", "=tag", "ünï"], rng.randint(0, 3)) or None,
            suite_name=rng.choice([None, "Checkout", "=Suite"]),
            description=rng.choice([None, "Line one\nline two", "-negative description"]),
            ai_generated=False,
        ))
    return cases


@pytest.fixture
async def world(pg_engine):
    token = uuid.uuid4().hex[:10]
    admin = User(id=uuid.uuid4(), email=f"w13i-{token}@example.invalid", username=f"w13i-{token}",
                 full_name="W13 Importer", hashed_password="not-a-real-password", role=UserRole.ADMIN, is_active=True)
    a = Project(id=uuid.uuid4(), name=f"W13 import A {token}", slug=f"w13-import-a-{token}")
    b = Project(id=uuid.uuid4(), name=f"W13 import B {token}", slug=f"w13-import-b-{token}")
    factory = async_sessionmaker(pg_engine, expire_on_commit=False)
    source = _generated(a.id)
    async with factory() as s:
        s.add_all([admin, a, b])
        await s.flush()
        s.add_all(source)
        await s.commit()
    try:
        yield SimpleNamespace(factory=factory, admin=admin, a=a, b=b, source=source)
    finally:
        async with factory() as s:
            await s.execute(delete(AuditRow).where(AuditRow.project_id.in_([a.id, b.id])))
            await s.execute(delete(Project).where(Project.id.in_([a.id, b.id])))
            await s.execute(delete(User).where(User.id == admin.id))
            await s.commit()


def _client(world):
    app = FastAPI()
    app.include_router(tm_router.router)

    async def _db():
        async with world.factory() as s:
            yield s

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_current_active_user] = lambda: world.admin
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


def _comparable(case) -> tuple:
    actions = [s["action"] for s in (case.steps or [])]
    return (*[getattr(case, f) for f in FIELDS], actions)


async def test_an_export_imports_into_another_project_unchanged_and_then_only_duplicates(world):
    async with _client(world) as client:
        export = await client.get("/api/v1/test-management/cases/export/excel", params={"project_id": str(world.a.id)})
        assert export.status_code == 200, export.text
        files = {"file": ("test-cases.xlsx", export.content,
                          "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}
        url = "/api/v1/test-management/cases/import"
        dry = await client.post(url, params={"project_id": str(world.b.id)}, files=files)
        applied = await client.post(url, params={"project_id": str(world.b.id), "dry_run": "false"}, files=files)
        again = await client.post(url, params={"project_id": str(world.b.id)}, files=files)
    assert dry.status_code == 200, dry.text
    assert dry.json()["summary"] == {"rows": N, "create": N, "duplicate": 0, "invalid": 0, "created": 0}
    assert set(dry.json()["headers"]["ignored"]) >= {"Status", "Version", "ID", "AI Generated"}
    assert dry.json()["headers"]["unknown"] == []
    assert applied.json()["summary"]["created"] == N, applied.text
    assert {r["outcome"] for r in again.json()["rows"]} == {"duplicate"}

    async with world.factory() as s:
        imported = (await s.execute(select(ManagedTestCase).where(ManagedTestCase.project_id == world.b.id))).scalars().all()
    assert len(imported) == N
    assert {(c.status, c.version) for c in imported} == {("draft", 1)}
    by_title = {c.title: c for c in imported}
    for original in world.source:
        copy = by_title[original.title]
        assert _comparable(copy) == _comparable(original), original.title
