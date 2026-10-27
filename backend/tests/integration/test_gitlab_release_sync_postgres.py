"""W19 INT-05: GitLab milestones -> releases, against the fake GitLab and a real database.

``POST /releases/sync {source: "gitlab"}`` creates one release per milestone
(``source_system="gitlab"``, keyed on the milestone ``iid``, linking its web URL);
renaming a milestone and syncing again renames the release — no duplicate; with
``AI_OFFLINE_MODE`` on, or the ``gitlab`` flag off, the route answers 503 with the
gate's words and creates nothing.

Requires ``TESTLOOKUP_POSTGRES_TEST_DSN`` and a database at head; skips otherwise.
"""
from __future__ import annotations

import os
import uuid

import httpx
import pytest
from fastapi import HTTPException
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytest.importorskip("asyncpg")
pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def _dsn() -> str:
    value = os.getenv("TESTLOOKUP_POSTGRES_TEST_DSN", "").strip()
    if not value:
        pytest.skip("TESTLOOKUP_POSTGRES_TEST_DSN is not configured")
    return value


@pytest.fixture
async def world(monkeypatch):
    from app.models.postgres import GitLabIntegration, Project, Release, User, UserRole
    from app.services import gitlab_integration_service as gl
    from app.services import gitlab_release_sync as sync
    from tests.fakes.fake_integrations import FakeState, create_app

    state = FakeState()
    transport = httpx.ASGITransport(app=create_app(state))
    real = httpx.AsyncClient

    class Routed(real):  # type: ignore[misc, valid-type]
        def __init__(self, *a, **k):
            k["transport"] = transport
            super().__init__(*a, **k)

    async def _no_block(url):
        return None

    async def _open(db=None, *, project_id=None, **k):
        return not sync.settings.AI_OFFLINE_MODE and flag["on"]

    async def _pat(db, scope, key):
        return "fake-gitlab-pat"

    flag = {"on": True}
    monkeypatch.setattr(httpx, "AsyncClient", Routed)
    import app.services.url_safety as url_safety
    from app.services import secret_service

    monkeypatch.setattr(url_safety, "egress_block_reason", _no_block)
    monkeypatch.setattr(gl, "_post_allowed", _open)
    monkeypatch.setattr(secret_service, "read_secret", _pat)
    monkeypatch.setattr(sync.settings, "AI_OFFLINE_MODE", False)

    engine = create_async_engine(_dsn(), pool_size=1, max_overflow=0)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    marker = "zqx" + uuid.uuid4().hex[:8].replace("a", "q")
    lead = User(id=uuid.uuid4(), email=f"l-{marker}@testlookup.dev", username=f"l-{marker}", full_name="Lead",
                hashed_password="x", role=UserRole.ADMIN, is_active=True)
    project = Project(id=uuid.uuid4(), name=f"API Gateway {marker}", slug=f"apigw-{marker}", is_active=True)
    async with factory() as db:
        db.add_all([lead, project])
        await db.flush()
        db.add(GitLabIntegration(project_id=project.id, base_url="http://gitlab.fake", project_path="group/api-gateway",
                                 enabled=True))
        await db.commit()
    try:
        async with factory() as db:
            yield {"db": db, "lead": lead, "project": project, "state": state, "flag": flag}
    finally:
        async with factory() as db:
            await db.execute(delete(Release).where(Release.project_id == project.id))
            await db.execute(delete(GitLabIntegration).where(GitLabIntegration.project_id == project.id))
            await db.execute(delete(Project).where(Project.id == project.id))
            await db.execute(delete(User).where(User.id == lead.id))
            await db.commit()
        await engine.dispose()


async def _sync(world):
    from app.routers.releases import ReleaseSyncIn, sync_releases_from_external

    return await sync_releases_from_external(ReleaseSyncIn(project_id=str(world["project"].id), source="gitlab"),
                                             db=world["db"], current_user=world["lead"])


async def _releases(world):
    from app.models.postgres import Release

    world["db"].expire_all()
    rows = (await world["db"].execute(select(Release).where(Release.project_id == world["project"].id,
                                                            Release.source_system == "gitlab"))).scalars().all()
    return sorted((r.external_id, r.name, r.external_url) for r in rows)


async def test_milestones_become_releases_and_a_rename_renames(world):
    first = await _sync(world)
    assert first == {"source": "gitlab", "created": 2, "updated": 0, "skipped": 0}
    rows = await _releases(world)
    assert [(r[0], r[1]) for r in rows] == [("1", "Gateway 2.4"), ("2", "Gateway 2.5")]
    assert rows[0][2].endswith("/group/api-gateway/-/milestones/1")

    ms = world["state"].gitlab_milestones["group/api-gateway"]
    ms[0] = {**ms[0], "title": "Gateway 2.4 (rate limits)"}
    await _sync(world)
    rows = await _releases(world)
    assert [(r[0], r[1]) for r in rows] == [("1", "Gateway 2.4 (rate limits)"), ("2", "Gateway 2.5")]


async def test_offline_or_flag_off_is_503_with_the_reason(world, monkeypatch):
    from app.services import gitlab_release_sync as sync

    world["flag"]["on"] = False
    with pytest.raises(HTTPException) as exc:
        await _sync(world)
    assert exc.value.status_code == 503 and "gitlab feature flag is off" in exc.value.detail
    monkeypatch.setattr(sync.settings, "AI_OFFLINE_MODE", True)
    with pytest.raises(HTTPException) as exc:
        await _sync(world)
    assert exc.value.status_code == 503 and "AI_OFFLINE_MODE is enabled" in exc.value.detail
    assert await _releases(world) == []
