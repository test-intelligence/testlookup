"""W23b FAIL-13 — the run context on a real PostgreSQL, end to end (contract §6.2 / §6.3 G-C).

What a fake session cannot show:

1. **Stamping on real rows.** Two runs created through ``create_run_from_payload``
   with contexts (python 3.11.8 + poetry.lock A, then python 3.11.9 + poetry.lock B)
   land in ``test_runs.run_context`` (JSONB, migration 0245) as the validated,
   REDACTED object; a retried delivery of the first never overwrites it.
2. **The acceptance drift through the ASGI app.** ``GET /api/v1/runs/{id}/regression-diff``
   with a VIEWER member's token answers ``config_drift`` against the Changes tab's own
   baseline: ``poetry.lock changed`` then ``python 3.11.8 → 3.11.9``, in that order.
3. **Secrets never stored.** ``SELECT run_context::text`` holds none of the REAL-LOOKING
   samples (and neither does the answer); ``[REDACTED]`` is there instead.
4. **Absence is not health.** A run without a context answers
   ``context_state = "run_missing"``, never "no drift".

The fixture awaits ``dispose_engine_for_loop()`` before building its engine and after
disposing it. ``AI_OFFLINE_MODE`` is patched True, so the regression-diff route's lazy
commit-range resolve returns at once and reads no feature flag; the teardown closes
Redis ONLY if the ASGI request path bound the pool, and never touches Mongo. Every
instant is built inside its test; every row lives under one fresh project removed at
the end. Requires ``TESTLOOKUP_POSTGRES_TEST_DSN`` (= ``DATABASE_URL``) at head (0245).
To be listed in the ``postgres-integration`` job of ``.github/workflows/ci.yml`` after
``test_bisect_postgres.py`` (contract §6.4).
"""
from __future__ import annotations

import hashlib
import json
import os
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytest.importorskip("asyncpg")

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]


def _dsn() -> str:
    value = os.environ.get("TESTLOOKUP_POSTGRES_TEST_DSN", "").strip()
    if not value:
        pytest.skip("TESTLOOKUP_POSTGRES_TEST_DSN is not configured")
    if not os.environ.get("DATABASE_URL", "").strip():
        pytest.skip("DATABASE_URL must point the app at the same database")
    return value


def _secret_env() -> dict[str, str]:
    """REAL-LOOKING samples, built here (never hex-only, never a real secret)."""
    return {
        "BUILD_IMAGE": "ghp_" + "Sentinel" * 5,
        "NODE_ENV": "glpat-" + "SentinelSent1234",
        "ImageVersion": "https://deploy:Hunter2Sentinel@registry.example.com",
        "RUNNER_OS": "Bearer " + "Sentinel" * 4,
        "LC_ALL": "token=SentinelValue1",
        "TZ": "Europe/Berlin",
    }


def _no_secret(blob: Any) -> None:
    text_blob = blob if isinstance(blob, str) else json.dumps(blob, default=str)
    assert "sentinel" not in text_blob.lower(), text_blob
    assert "hunter2" not in text_blob.lower(), text_blob


def _context(python: str, lock: bytes) -> dict[str, Any]:
    return {
        "v": 1,
        "runtime": {"python": python},
        "dependency_fingerprints": {"poetry.lock": hashlib.sha256(lock).hexdigest()},
        "env": _secret_env(),
    }


@pytest_asyncio.fixture(loop_scope="module")
async def env():
    from app.db import postgres as app_postgres
    from app.db import redis_client

    dsn = _dsn()
    await app_postgres.dispose_engine_for_loop()
    engine = create_async_engine(dsn, pool_size=2, max_overflow=0)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    made = SimpleNamespace(projects=[], users=[])
    try:
        yield SimpleNamespace(engine=engine, sessions=sessions, made=made)
    finally:
        async with sessions() as db:
            if made.projects:
                await db.execute(text("DELETE FROM projects WHERE id = ANY(:p)"), {"p": made.projects})
            if made.users:
                await db.execute(text("DELETE FROM users WHERE id = ANY(:u)"), {"u": made.users})
            await db.commit()
        await engine.dispose()
        if redis_client._pool is not None:  # only if the ASGI request path bound it
            await redis_client.close_redis()
        await app_postgres.dispose_engine_for_loop()


async def _world(env) -> SimpleNamespace:
    """A project and a VIEWER member with an access token."""
    from app.models.postgres import Project, ProjectMember, User, UserRole
    from app.services.auth_session_tokens import issue_access_jwt

    pid, viewer = uuid.uuid4(), uuid.uuid4()
    async with env.sessions() as db:
        db.add(Project(id=pid, name=f"gc-ctx-{pid.hex[:8]}", slug=f"gc-ctx-{pid.hex[:8]}", is_active=True))
        db.add(User(id=viewer, username=f"gc-viewer-{viewer.hex[:8]}", email=f"gc-{viewer.hex[:8]}@example.test",
                    hashed_password="!unusable", role=UserRole.VIEWER, is_active=True))
        await db.flush()
        db.add(ProjectMember(project_id=pid, user_id=viewer, role=UserRole.VIEWER))
        await db.commit()
        token = await issue_access_jwt(db, str(viewer), timedelta(minutes=10))
    env.made.projects.append(pid)
    env.made.users.append(viewer)
    return SimpleNamespace(pid=pid, headers={"Authorization": f"Bearer {token}"})


async def _create(env, pid: uuid.UUID, build: str, run_context: Any, **kwargs: Any):
    from app.services.ingestion_pipeline import create_run_from_payload

    async with env.sessions() as db:
        run = await create_run_from_payload(
            db, project_id=str(pid), build_number=build, ingestion_source="sdk", branch="main",
            run_context=run_context, **kwargs,
        )
        await db.commit()
        return run.id


async def _settle(env, run_id: uuid.UUID, *, pass_rate: float, created_at: datetime) -> None:
    """What finalize_run would have written: a pass rate, and a fixed ingest instant."""
    async with env.sessions() as db:
        await db.execute(
            text("UPDATE test_runs SET pass_rate = :rate, status = 'PASSED', created_at = :at WHERE id = :id"),
            {"rate": pass_rate, "at": created_at, "id": run_id},
        )
        await db.commit()


async def _diff(world, run_id: uuid.UUID) -> dict[str, Any]:
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://gc-ctx.test") as client:
        resp = await client.get(f"/api/v1/runs/{run_id}/regression-diff", headers=world.headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


async def test_two_stamped_runs_answer_the_acceptance_drift_and_store_no_secret(env, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "AI_OFFLINE_MODE", True)
    world = await _world(env)
    now = datetime.now(timezone.utc)
    lock_a, lock_b = b"[[package]]\nname = \"requests\"\nversion = \"2.32.2\"\n", b"[[package]]\nname = \"requests\"\nversion = \"2.32.3\"\n"
    baseline = await _create(env, world.pid, "gc-ctx-1", _context("3.11.8", lock_a))
    await _settle(env, baseline, pass_rate=100.0, created_at=now - timedelta(hours=2))
    # A retried delivery of the same run (the resume branch) never overwrites the stored context.
    assert await _create(env, world.pid, "gc-ctx-1", _context("3.12.0", lock_b), run_id=str(baseline)) == baseline
    current = await _create(env, world.pid, "gc-ctx-2", _context("3.11.9", lock_b))
    await _settle(env, current, pass_rate=50.0, created_at=now - timedelta(hours=1))

    body = await _diff(world, current)
    assert body["baseline_run_id"] == str(baseline)
    drift = body["config_drift"]
    assert drift["context_state"] == "recorded"
    assert drift["baseline_run_id"] == str(baseline) and drift["baseline_build_number"] == "gc-ctx-1"
    assert drift["entries"][:2] == [
        {"section": "dependencies", "key": "poetry.lock", "change": "changed",
         "old": hashlib.sha256(lock_a).hexdigest()[:12], "new": hashlib.sha256(lock_b).hexdigest()[:12]},
        {"section": "runtime", "key": "python", "change": "changed", "old": "3.11.8", "new": "3.11.9"},
    ]
    assert drift["entries_total"] == len(drift["entries"]) == 2  # same env, same branch: nothing else drifts
    _no_secret(body)

    async with env.sessions() as db:
        rows = (await db.execute(
            text("SELECT id, run_context::text FROM test_runs WHERE project_id = :p ORDER BY created_at"),
            {"p": world.pid},
        )).all()
    assert [row[0] for row in rows] == [baseline, current]
    for _id, stored in rows:
        _no_secret(stored)
        ctx = json.loads(stored)
        assert ctx["env"]["BUILD_IMAGE"] == "[REDACTED]" and ctx["env"]["NODE_ENV"] == "[REDACTED]"
        assert ctx["env"]["ImageVersion"] == "https://deploy:[REDACTED]@registry.example.com"
        assert ctx["env"]["RUNNER_OS"] == "Bearer [REDACTED]" and ctx["env"]["LC_ALL"] == "token=[REDACTED]"
        assert ctx["env"]["TZ"] == "Europe/Berlin"
    assert json.loads(rows[0][1])["runtime"] == {"python": "3.11.8"}  # the resume did not overwrite it


async def test_a_run_without_a_context_reads_run_missing_never_no_drift(env, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "AI_OFFLINE_MODE", True)
    world = await _world(env)
    now = datetime.now(timezone.utc)
    baseline = await _create(env, world.pid, "gc-old-1", _context("3.11.8", b"lock-a"))
    await _settle(env, baseline, pass_rate=100.0, created_at=now - timedelta(hours=2))
    bare = await _create(env, world.pid, "gc-old-2", None)
    await _settle(env, bare, pass_rate=40.0, created_at=now - timedelta(hours=1))

    async with env.sessions() as db:
        stored = (await db.execute(text("SELECT run_context FROM test_runs WHERE id = :id"), {"id": bare})).scalar()
    assert stored is None  # NULL: "not recorded by this reporter"

    drift = (await _diff(world, bare))["config_drift"]
    assert drift["context_state"] == "run_missing"
    assert drift["entries"] == [] and drift["entries_total"] == 0  # same branch, no context: nothing compared
