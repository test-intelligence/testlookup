"""A throwaway world for the W06 G-A defect integration tests (owner column, affects release, trend, escape rate).

Not a test module: the ``*_postgres.py`` files import :func:`defect_world`. It
creates, in the database named by ``TESTLOOKUP_POSTGRES_TEST_DSN`` (migrated to
head), three projects (``P`` and ``Q`` live, ``D`` soft-deleted), an admin, a
QA engineer who is a member of ``P`` and ``D``, an outsider who is a member of
``Q``, releases (``R`` shipped ten days ago and ``R2`` unshipped in ``P``; ``RQ``
shipped in ``Q``) and one run in ``P`` found in ``R`` (suite ``checkout-api``).
Everything is deleted afterwards: the projects cascade to their defects,
releases, runs, rules and outcomes.

The app runs through ASGI on its own engine (``get_db`` overridden), with
Redis from ``REDIS_URL`` (the analytics read layer caches through it).
"""
from __future__ import annotations

import os
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any, AsyncIterator, Optional

import pytest
from sqlalchemy import delete, event, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

NOW = datetime.now(timezone.utc)
SUITE = "checkout-api"


def env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        pytest.skip(f"{name} is not configured")
    return value


def _redis_client():
    url = os.environ.get("REDIS_URL", "").strip()
    if url:
        import redis.asyncio as redis_asyncio

        return redis_asyncio.Redis.from_url(url, decode_responses=True)
    fakeredis = pytest.importorskip("fakeredis")
    return fakeredis.aioredis.FakeRedis(decode_responses=True)


class StatementCounter:
    """Counts the statements an engine executes while active (``before_cursor_execute``)."""

    def __init__(self, engine):
        self.engine = engine.sync_engine
        self.statements: list[str] = []

    def _record(self, conn, cursor, statement, parameters, context, executemany):
        self.statements.append(statement)

    def __enter__(self):
        event.listen(self.engine, "before_cursor_execute", self._record)
        return self

    def __exit__(self, *exc):
        event.remove(self.engine, "before_cursor_execute", self._record)
        return False


@asynccontextmanager
async def defect_world() -> AsyncIterator[SimpleNamespace]:
    pytest.importorskip("asyncpg")
    from httpx import ASGITransport, AsyncClient

    from app.core.security import create_access_token
    from app.db import postgres as app_postgres
    from app.db.postgres import get_db
    from app.main import app
    from app.models.postgres import (
        Defect,
        LaunchStatus,
        Project,
        ProjectMember,
        Release,
        TestCase,
        TestRun,
        User,
        UserRole,
    )

    dsn = env("TESTLOOKUP_POSTGRES_TEST_DSN")
    await app_postgres.dispose_engine_for_loop()
    engine = create_async_engine(dsn, pool_size=4, max_overflow=0)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    redis = _redis_client()

    async def _get_db():
        async with sessions() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    patch = pytest.MonkeyPatch()
    app.dependency_overrides[get_db] = _get_db
    patch.setattr("app.db.redis_client.get_redis", lambda: redis)
    patch.setattr("app.db.postgres.AsyncSessionLocal", sessions, raising=False)

    # Starts with a letter: ten hex chars are all digits ~1% of the time, and the PDF / log
    # redaction then masks them as a phone number ([REDACTED_PHONE]), failing a name-in-text check.
    tag = "t" + uuid.uuid4().hex[:9]
    P, Q, D = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    admin, member, outsider = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    R, R2, RQ = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    run_r = uuid.uuid4()
    projects = (P, Q, D)
    users: list[uuid.UUID] = [admin, member, outsider]
    try:
        async with sessions.begin() as db:
            for pid, label, live in ((P, "p", True), (Q, "q", True), (D, "d", False)):
                db.add(Project(id=pid, name=f"w06a-{label}-{tag}", slug=f"w06a-{label}-{tag}", is_active=live))
            for uid, label, role in (
                (admin, "admin", UserRole.ADMIN),
                (member, "member", UserRole.QA_ENGINEER),
                (outsider, "outsider", UserRole.QA_ENGINEER),
            ):
                db.add(User(
                    id=uid, email=f"w06a-{label}-{tag}@example.com", username=f"w06a_{label}_{tag}",
                    full_name=f"W06A {label}", hashed_password="!unusable", role=role.value,
                ))
            await db.flush()
            db.add(ProjectMember(project_id=P, user_id=member, role=UserRole.QA_ENGINEER.value))
            db.add(ProjectMember(project_id=D, user_id=member, role=UserRole.QA_ENGINEER.value))
            db.add(ProjectMember(project_id=Q, user_id=outsider, role=UserRole.QA_ENGINEER.value))
            db.add(Release(id=R, project_id=P, name=f"w06a-r-{tag}", version="2.4.0", status="released",
                           released_at=NOW - timedelta(days=10)))
            db.add(Release(id=R2, project_id=P, name=f"w06a-r2-{tag}", version="2.5.0", status="planning"))
            db.add(Release(id=RQ, project_id=Q, name=f"w06a-rq-{tag}", version="9.0.0", status="released",
                           released_at=NOW - timedelta(days=10)))
            await db.flush()
            db.add(TestRun(
                id=run_r, project_id=P, build_number=f"w06a-{tag}", jenkins_job="w06a",
                status=LaunchStatus.FAILED, ingestion_source="unknown", total_tests=1, failed_tests=1,
                primary_release_id=R, primary_suite_name=SUITE,
            ))

        def jwt(uid):
            return {"Authorization": f"Bearer {create_access_token(str(uid))}"}

        async def case(db, run_id=run_r, **fields: Any) -> uuid.UUID:
            cid = uuid.uuid4()
            values = {"test_name": f"test_{cid.hex[:8]}", "suite_name": SUITE, "status": "FAILED",
                      "test_fingerprint": cid.hex[:16]}
            values.update(fields)
            db.add(TestCase(id=cid, test_run_id=run_id, **values))
            await db.flush()
            return cid

        async def defect(db, project_id=P, **fields: Any) -> uuid.UUID:
            did = uuid.uuid4()
            values = {"title": f"defect {did.hex[:8]}", "resolution_status": "OPEN", "approval_status": "approved"}
            values.update(fields)
            db.add(Defect(id=did, project_id=project_id, **values))
            await db.flush()
            return did

        async def user(db, **fields: Any) -> uuid.UUID:
            uid = uuid.uuid4()
            values = {"email": f"w06a-u{uid.hex[:8]}-{tag}@example.com", "username": f"w06a_u{uid.hex[:8]}_{tag}",
                      "hashed_password": "!unusable", "role": "QA_ENGINEER"}
            values.update(fields)
            db.add(User(id=uid, **values))
            users.append(uid)
            await db.flush()
            return uid

        async def project(db, *, live: bool = True, **fields: Any) -> uuid.UUID:
            pid = uuid.uuid4()
            nonlocal_projects.append(pid)
            db.add(Project(id=pid, name=f"w06a-x{pid.hex[:6]}-{tag}", slug=f"w06a-x{pid.hex[:6]}-{tag}",
                           is_active=live, **fields))
            await db.flush()
            return pid

        nonlocal_projects: list[uuid.UUID] = []
        client = AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")
        try:
            yield SimpleNamespace(
                sessions=sessions, engine=engine, client=client, tag=tag,
                P=P, Q=Q, D=D, R=R, R2=R2, RQ=RQ, run_r=run_r,
                admin=jwt(admin), member=jwt(member), outsider=jwt(outsider),
                admin_id=admin, member_id=member, outsider_id=outsider,
                case=case, defect=defect, user=user, project=project, jwt=jwt,
            )
        finally:
            await client.aclose()
            projects = (*projects, *nonlocal_projects)
    finally:
        app.dependency_overrides.pop(get_db, None)
        from app.models.postgres import User as _User

        for statement, params in (
            (text("DELETE FROM canonical_test_cases WHERE project_id = ANY(:ids)"), {"ids": list(projects)}),
            (text("DELETE FROM projects WHERE id = ANY(:ids)"), {"ids": list(projects)}),
            (text("DELETE FROM access_audit_logs WHERE actor_user_id = ANY(:ids)"), {"ids": users}),
            (delete(_User).where(_User.id.in_(users)), {}),
        ):
            try:
                async with sessions.begin() as db:
                    await db.execute(statement, params)
            except Exception as exc:  # noqa: BLE001 -- report, keep cleaning
                print(f"w06a teardown: {type(exc).__name__}: {str(exc)[:160]}")
        patch.undo()
        await redis.aclose()
        await engine.dispose()
        await app_postgres.dispose_engine_for_loop()


def scope(project_id: Optional[uuid.UUID], days: Optional[int] = 30, *, releases=(), suites=(), allowed=None):
    """An authorised ``AnalyticsScope`` for a direct service call."""
    from app.services.analytics_scope import AnalyticsScope

    return AnalyticsScope(
        project_id=project_id, allowed_project_ids=allowed,
        release_ids=tuple(str(r) for r in releases), suite_names=tuple(suites), days=days,
    )
