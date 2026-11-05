"""W32 F-7 on real PostgreSQL + real MongoDB: a project reset deletes the
project's run-scoped Mongo documents, and only that project's.

Two projects × two runs; a document in EVERY ``retention_service._MONGO_PLANS``
collection for each project (case-keyed for ``ai_analysis_payloads``,
pipeline-keyed for the event log, plus a slug-keyed live event for project A).
Project A is reset in ``runs`` mode through the service with the real Mongo:
every A document is gone, every B document is still there, and A's audit row
carries ``documents``.

Stores: Postgres = ``TESTLOOKUP_POSTGRES_TEST_DSN`` (the group / CI database —
every seeded row and the reset's audit row are deleted after, and the touched
tables are asserted back to their pre-test counts). Mongo = a THROWAWAY
database on its own Motor client (dropped after), so the module binds neither
the app's Mongo client nor, with the epoch bump recorded, its Redis client.
"""
from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tests.integration.ai_history_seed import drop_project, dsn, seed_pipeline, seed_project, seed_run

pytest.importorskip("asyncpg")
pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_TABLES = ("projects", "test_runs", "test_cases", "agent_pipeline_runs", "live_sessions", "settings_audit_log")


async def _counts(factory) -> dict[str, int]:
    async with factory() as db:
        return {t: int(await db.scalar(text(f"SELECT count(*) FROM {t}"))) for t in _TABLES}


async def _seed_side(db, label: str, *, slug: str | None) -> SimpleNamespace:
    from app.models.postgres import LaunchStatus, LiveSession, Project, TestRun

    project_id = await seed_project(db, label)
    runs, cases = [], []
    for _ in range(2):
        run_id, case_ids = await seed_run(db, project_id, 1)
        runs.append(run_id)
        cases.extend(case_ids)
    pipeline = await seed_pipeline(db, runs[0])
    if slug is not None:
        # The slug's canonical run: its live events are keyed by the CLIENT slug.
        slug_run = uuid.uuid5(uuid.NAMESPACE_DNS, slug)
        db.add(TestRun(
            id=slug_run, project_id=project_id, build_number=f"w32-{slug_run.hex[:10]}",
            status=LaunchStatus.PASSED, branch="main", total_tests=0, failed_tests=0,
            broken_tests=0, pass_rate=100.0,
        ))
        await db.flush()
        runs.append(slug_run)
        db.add(LiveSession(
            project_id=project_id, run_id=slug, client_name="pytest",
            session_token_hash=hashlib.sha256(slug.encode()).hexdigest(),
            started_at=datetime.now(timezone.utc), status="completed",
        ))
        await db.flush()
    name = (await db.get(Project, project_id)).name
    return SimpleNamespace(project_id=project_id, name=name, runs=runs, cases=cases,
                           pipeline=pipeline, slug=slug)


def _docs_for(side) -> dict[str, list[dict]]:
    from app.services.retention_service import _MONGO_PLANS

    keys = {
        "raw_run_strs": [str(r) for r in side.runs],
        "purge_run_strs": [str(r) for r in side.runs],
        "audit_run_strs": [str(r) for r in side.runs],
        "raw_live_run_keys": [str(r) for r in side.runs] + ([side.slug] if side.slug else []),
        "audit_pipeline_strs": [str(side.pipeline)],
        "purge_tc_strs": [str(c) for c in side.cases],
    }
    return {
        collection: [{field: value, "side": str(side.project_id)} for value in keys[attr]]
        for collection, field, attr in _MONGO_PLANS
    }


async def _cleanup(env) -> None:
    from app.models.postgres import SettingsAuditLog

    for side in env.sides:
        await drop_project(env.factory, side.project_id)
    env.sides.clear()
    if env.audit_ids:
        async with env.factory() as db:
            await db.execute(delete(SettingsAuditLog).where(SettingsAuditLog.id.in_(env.audit_ids)))
            await db.commit()
        env.audit_ids.clear()


@pytest.fixture
async def env(monkeypatch):
    from motor.motor_asyncio import AsyncIOMotorClient

    from app.core.config import settings
    from app.db import mongo as mongo_module
    from app.db import postgres as app_pg
    from app.db import redis_client

    url = dsn()
    await app_pg.dispose_engine_for_loop()
    engine = create_async_engine(url, pool_size=2, max_overflow=0)
    client = AsyncIOMotorClient(settings.MONGO_URI, serverSelectionTimeoutMS=5000)
    mongo = client[f"w32reset_{uuid.uuid4().hex[:10]}"]
    try:
        await mongo.command("ping")
    except Exception as exc:  # noqa: BLE001
        client.close()
        await engine.dispose()
        pytest.skip(f"MongoDB is not reachable: {exc}")
    bumps: list[str] = []

    async def _bump(project_id):
        bumps.append(str(project_id))

    monkeypatch.setattr("app.services.cache_service.bump_analytics_epoch", _bump)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    state = SimpleNamespace(
        factory=factory, mongo=mongo, bumps=bumps, sides=[], audit_ids=[],
        before=await _counts(factory), clients=(redis_client._client, mongo_module._client),
    )
    try:
        yield state
    finally:
        await _cleanup(state)
        await client.drop_database(mongo.name)
        client.close()
        await engine.dispose()
        await app_pg.dispose_engine_for_loop()


async def test_a_reset_deletes_its_projects_documents_and_only_those(env):
    from app.db import mongo as mongo_module
    from app.db import redis_client
    from app.models.postgres import SettingsAuditLog, TestRun
    from app.services.project_reset_service import reset_project

    async with env.factory() as db:
        a = await _seed_side(db, "w32 reset a", slug=f"local-{uuid.uuid4().hex[:10]}")
        b = await _seed_side(db, "w32 reset b", slug=None)
        await db.commit()
    env.sides += [a, b]
    docs_a, docs_b = _docs_for(a), _docs_for(b)
    for collection in docs_a:
        await env.mongo[collection].insert_many(docs_a[collection] + docs_b[collection])

    async with env.factory() as db:
        result = await reset_project(
            db, project_id=a.project_id, mode="runs", confirmation_name=a.name, mongo=env.mongo,
        )

    assert result["deleted"] == {"test_runs": 3}
    assert result["documents"] == {c: len(docs) for c, docs in docs_a.items()}
    for collection in docs_a:
        assert await env.mongo[collection].count_documents({"side": str(a.project_id)}) == 0, collection
        left = await env.mongo[collection].count_documents({"side": str(b.project_id)})
        assert left == len(docs_b[collection]), collection
    async with env.factory() as db:
        rows = (await db.execute(
            select(SettingsAuditLog).where(SettingsAuditLog.setting_key == "project_reset.runs")
        )).scalars().all()
        mine = [r for r in rows if (r.changed_fields or {}).get("project_id") == str(a.project_id)]
        env.audit_ids.extend(r.id for r in mine)
        assert len(mine) == 1 and mine[0].changed_fields["documents"] == result["documents"]
        kept = await db.scalar(
            select(func.count()).select_from(TestRun).where(TestRun.project_id == b.project_id)
        )
        assert kept == 2
    assert env.bumps == [str(a.project_id)]

    await _cleanup(env)
    assert await _counts(env.factory) == env.before
    # the path bound neither app client: the next CI module inherits nothing
    assert (redis_client._client, mongo_module._client) == env.clients
