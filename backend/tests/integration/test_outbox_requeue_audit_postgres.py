"""Re-audit N30 against a real database: an outbox requeue leaves a durable audit row.

``POST /api/v1/admin/maintenance/outbox/requeue`` re-runs failed post-ingestion
work across tenants (each ``agent_pipeline`` intent starts an AI pipeline), and
its only record was a structlog line. It now writes an ``access_audit_logs``
row on the request's session, like the other administrative actions (role and
membership changes): the actor, the operation, the filters, whether it was a
dry run, and how many intents it matched and requeued. The row commits with
the requeue; a refused request writes none.

Real app through ASGI, a real ADMIN user signed in with a JWT, real outbox
rows. The app's engine is disposed at the start and the end.
Requires ``TESTLOOKUP_POSTGRES_TEST_DSN`` and ``REDIS_URL``, migrated to head.
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytest.importorskip("asyncpg")
pytest.importorskip("httpx")
pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_PATH = "/api/v1/admin/maintenance/outbox/requeue"


def _env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        pytest.skip(f"{name} is not configured")
    return value


@pytest.fixture
async def world(monkeypatch):
    from types import SimpleNamespace

    redis_asyncio = pytest.importorskip("redis.asyncio")
    from httpx import ASGITransport, AsyncClient

    from app.core.security import create_access_token
    from app.db import postgres as app_postgres
    from app.db.postgres import get_db
    from app.main import app
    from app.models.postgres import (
        AccessAuditLog,
        Project,
        RunDownstreamOutbox,
        TestRun,
        User,
        UserRole,
    )

    await app_postgres.dispose_engine_for_loop()
    engine = create_async_engine(_env("TESTLOOKUP_POSTGRES_TEST_DSN"), pool_size=4, max_overflow=0)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    redis = redis_asyncio.Redis.from_url(_env("REDIS_URL"), decode_responses=True)

    async def _get_db():
        async with sessions() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[get_db] = _get_db
    monkeypatch.setattr("app.db.redis_client.get_redis", lambda: redis)
    monkeypatch.setattr("app.db.postgres.AsyncSessionLocal", sessions, raising=False)

    tag = uuid.uuid4().hex[:10]
    project_id, run_id, admin = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    # A last_error nobody else's rows carry, so a requeue without run_id
    # still touches only this test's rows in a shared database.
    last_error = f"n30_{tag}"
    base = datetime.now(timezone.utc) - timedelta(days=3)
    outbox_ids = [uuid.uuid4(), uuid.uuid4()]

    async with sessions() as db:
        db.add(Project(id=project_id, name=f"n30-{tag}", slug=f"n30-{tag}"))
        db.add(User(
            id=admin, email=f"n30-admin-{tag}@example.com", username=f"n30_admin_{tag}",
            full_name="N30 admin", hashed_password="!unusable", role=UserRole.ADMIN.value,
        ))
        await db.flush()
        db.add(TestRun(
            id=run_id, project_id=project_id, build_number=f"n30-{tag}", status="PASSED",
            total_tests=0, passed_tests=0, failed_tests=0, skipped_tests=0,
            broken_tests=0, unknown_tests=0,
        ))
        await db.flush()
        for minutes, oid in enumerate(outbox_ids):
            db.add(RunDownstreamOutbox(
                id=oid, run_id=run_id, project_id=project_id, operation="agent_pipeline",
                input_version=f"n30-{minutes}",
                payload={"run_id": str(run_id), "project_id": str(project_id),
                         "build_number": "b-1", "workflow_type": "offline"},
                queue="ai_analysis", priority=6, status="failed", attempts=8,
                dispatch_failures=8, execution_attempts=0, dispatch_token=uuid.uuid4(),
                last_error=last_error, created_at=base + timedelta(minutes=minutes),
            ))
        await db.commit()

    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")
    world = SimpleNamespace(
        client=client, sessions=sessions, admin=admin, project_id=project_id, run_id=run_id,
        last_error=last_error, outbox_ids=outbox_ids,
        headers={"Authorization": f"Bearer {create_access_token(str(admin))}"},
        models=SimpleNamespace(AccessAuditLog=AccessAuditLog, RunDownstreamOutbox=RunDownstreamOutbox),
    )
    try:
        yield world
    finally:
        await client.aclose()
        app.dependency_overrides.pop(get_db, None)
        for statement in (
            delete(AccessAuditLog).where(AccessAuditLog.actor_user_id == admin),
            delete(RunDownstreamOutbox).where(RunDownstreamOutbox.id.in_(outbox_ids)),
            delete(TestRun).where(TestRun.id == run_id),
            delete(Project).where(Project.id == project_id),
            delete(User).where(User.id == admin),
        ):
            try:
                async with sessions.begin() as db:
                    await db.execute(statement)
            except Exception as exc:  # noqa: BLE001 -- report, keep cleaning
                print(f"n30 teardown: {type(exc).__name__}: {str(exc)[:160]}")
        await redis.aclose()
        await engine.dispose()
        await app_postgres.dispose_engine_for_loop()


async def _audit_rows(world):
    AccessAuditLog = world.models.AccessAuditLog
    async with world.sessions() as db:
        return (await db.execute(
            select(AccessAuditLog)
            .where(AccessAuditLog.actor_user_id == world.admin)
            .order_by(AccessAuditLog.created_at)
        )).scalars().all()


async def _statuses(world):
    Outbox = world.models.RunDownstreamOutbox
    async with world.sessions() as db:
        rows = (await db.execute(
            select(Outbox.id, Outbox.status).where(Outbox.id.in_(world.outbox_ids))
        )).all()
    return {row.id: row.status for row in rows}


def _body(world, **overrides):
    return {"operation": "agent_pipeline", "last_error": world.last_error, **overrides}


async def test_a_dry_run_is_recorded_with_what_it_matched(world):
    resp = await world.client.post(_PATH, headers=world.headers, json=_body(world))

    assert resp.status_code == 200, resp.text
    assert resp.json()["matched"] == 2
    rows = await _audit_rows(world)
    assert len(rows) == 1
    row = rows[0]
    assert row.action == "admin.outbox_requeue"
    assert row.actor_name == f"n30_admin_{world.last_error[4:]}"
    assert row.project_id is None
    assert row.after_value == {
        "operation": "agent_pipeline",
        "last_error": world.last_error,
        "run_id": None,
        "limit": 100,
        "dry_run": True,
        "matched": 2,
        "requeued": 0,
        "outbox_ids": [str(oid) for oid in world.outbox_ids],
    }
    # ...and a dry run still changes nothing else.
    assert set((await _statuses(world)).values()) == {"failed"}


async def test_a_requeue_is_recorded_with_its_run_and_count(world):
    resp = await world.client.post(
        _PATH, headers=world.headers,
        json=_body(world, run_id=str(world.run_id), limit=5, dry_run=False),
    )

    assert resp.status_code == 200, resp.text
    assert resp.json()["requeued"] == 2
    rows = await _audit_rows(world)
    assert len(rows) == 1
    row = rows[0]
    assert row.project_id == world.project_id
    assert row.after_value["dry_run"] is False
    assert row.after_value["run_id"] == str(world.run_id)
    assert row.after_value["limit"] == 5
    assert (row.after_value["matched"], row.after_value["requeued"]) == (2, 2)
    # The audit row and the requeue committed together.
    assert "failed" not in set((await _statuses(world)).values())


async def test_a_refused_requeue_writes_no_audit_row(world):
    resp = await world.client.post(
        _PATH, headers=world.headers, json=_body(world, operation="no_such_operation", dry_run=False),
    )

    assert resp.status_code == 422, resp.text
    assert await _audit_rows(world) == []
