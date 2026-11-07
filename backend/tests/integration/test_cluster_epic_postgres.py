"""W36 STRAT-03 — a cluster's Jira epic against the contract fake, on real PostgreSQL.

The REAL path end to end: ``systemic_cluster_service.store_clusters`` writes the clusters (as the nightly
task does), ``cluster_epic_service`` reads them, files through ``defect_jira_service._file_or_reuse``
(advisory lock, the E7.6 ledger on its own sessions, ``_jira_post`` / ``_jira_get`` over the shared client)
into ``tests/fakes/fake_integrations.py`` — a Jira project whose issue types include "Epic" — and stores the
link in ``systemic_cluster_epics``. Proves: one Epic in the fake + one link row, no Defect row; a second POST
reuses the link (still ONE issue); a nightly re-cluster with the same members keeps the link visible; a
changed member set shows no link; a project that has not opted in gets 403 and the fake receives nothing.
Everything seeded is deleted at the end (CI shares ONE database across modules).
"""
from __future__ import annotations

import os
import uuid
from types import SimpleNamespace

import httpx
import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tests.fakes.fake_integrations import FakeState, adf_problems, create_app

pytest.importorskip("asyncpg")

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]

DOMAIN = "fake-jira-epics.example.test"


def _dsn() -> str:
    value = os.environ.get("TESTLOOKUP_POSTGRES_TEST_DSN", "").strip()
    if not value:
        pytest.skip("TESTLOOKUP_POSTGRES_TEST_DSN is not configured")
    return value


def _cluster(members: list[str], *, rank: str = "sfc_001", co_failure_runs: int = 4):
    from app.services.systemic_cluster_service import SystemicCluster

    return SystemicCluster(
        cluster_key=rank, members=tuple(members), cohesion=0.81, co_failure_runs=co_failure_runs,
        cause_family="networking", label="Gateway timeouts on checkout",
        member_names={m: f"test_{m.split('-')[-1]}" for m in members},
    )


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def world():
    from app.core.config import settings
    from app.models.postgres import Project, User
    from app.services import defect_jira_service as svc
    from app.services.systemic_cluster_service import store_clusters

    engine = create_async_engine(_dsn(), pool_size=6, max_overflow=0)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    tag = uuid.uuid4().hex[:10]
    pid, off_pid, uid = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    members = [f"{tag}-a", f"{tag}-b", f"{tag}-c"]
    async with sessions.begin() as db:
        db.add(User(id=uid, email=f"epic-{tag}@example.com", username=f"epic_{tag}", full_name="Epic Filer",
                    hashed_password="x", role="QA_ENGINEER", is_active=True))
        db.add(Project(id=pid, name=f"epics-{tag}", slug=f"epics-{tag}", is_active=True,
                       cluster_epic_suggestions=True, jira_project_key="EPX"))
        db.add(Project(id=off_pid, name=f"epics-off-{tag}", slug=f"epics-off-{tag}", is_active=True))
        await db.flush()
        await store_clusters(db, pid, [_cluster(members)])
        await store_clusters(db, off_pid, [_cluster(members)])

    state = FakeState(base_url=f"https://{DOMAIN}")
    state.add_project("EPX", "Epics", issue_types=["Bug", "Task", "Story", "Epic"])
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(state)))

    async def resolved(db):
        return {"enabled": True, "domain": DOMAIN, "email": state.jira_email,
                "api_token": state.jira_token, "default_project_key": "QA"}

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr("app.db.postgres.AsyncSessionLocal", sessions)
    monkeypatch.setattr(settings, "AI_OFFLINE_MODE", False)
    monkeypatch.setattr(svc, "resolve_jira_config", resolved)
    monkeypatch.setattr(svc, "get_http_client", lambda: client)
    svc._metadata_cache_clear()

    try:
        yield SimpleNamespace(sessions=sessions, pid=pid, off_pid=off_pid, uid=uid, state=state, members=members,
                              tag=tag, store_clusters=store_clusters)
    finally:
        from app.db import postgres as app_postgres
        from app.db.redis_client import close_redis

        monkeypatch.undo()
        svc._metadata_cache_clear()
        async with engine.begin() as db:
            for project in (pid, off_pid):
                await db.execute(text("DELETE FROM agent_action_ledger WHERE project_id = :p"), {"p": project})
                await db.execute(text("DELETE FROM projects WHERE id = :p"), {"p": project})
            await db.execute(text("DELETE FROM users WHERE id = :u"), {"u": uid})
        await client.aclose()
        # The feature-flag read inside get_metadata opened the app's Redis client on THIS module's loop:
        # close it (and the app engine) so the next module does not inherit a client of a closed loop.
        await close_redis()
        await app_postgres.dispose_engine_for_loop()
        await engine.dispose()


async def _create(world, project_id, key: str, **kwargs):
    from app.models.postgres import User
    from app.services import cluster_epic_service as ces

    async with world.sessions() as db:
        actor = await db.get(User, world.uid)
        out = await ces.create_epic_for_cluster(db, project_id, actor, key, **kwargs)
        await db.commit()                       # the router's commit
        return out


async def _status(world, project_id):
    from app.services import cluster_epic_service as ces

    async with world.sessions() as db:
        return await ces.get_epic_status(db, project_id)


def _key(members: list[str]) -> str:
    from app.services.systemic_cluster_service import membership_key

    return membership_key(members)


async def test_create_files_one_epic_and_stores_the_link_without_a_defect(world):
    from app.models.postgres import AgentActionLedger, Defect, ProjectActivityEvent, SystemicClusterEpic

    key = _key(world.members)
    out = await _create(world, world.pid, key)

    assert out == {"membership_key": key, "jira_key": "EPX-1", "jira_url": f"https://{DOMAIN}/browse/EPX-1",
                   "created": True}
    assert list(world.state.issues) == ["EPX-1"]
    fields = world.state.issues["EPX-1"]["fields"]
    assert fields["issuetype"] == {"name": "Epic"} and fields["project"] == {"key": "EPX"}   # the project's key
    assert fields["summary"] == "[TestLookup] Recurring failure cluster: Gateway timeouts on checkout"
    assert fields["labels"][:2] == ["testlookup", "systemic-cluster-epic"]
    assert len(fields["labels"]) == 3 and fields["labels"][2].startswith("testlookup-sig-")
    assert adf_problems(fields["description"]) == []
    description = str(fields["description"])
    assert "Automated defect" not in description and "Failure message" not in description
    assert "Recurring failure cluster detected by TestLookup: Gateway timeouts on checkout" in description
    assert "failed together in 4 runs" in description and "test_b" in description
    assert f"Filed from TestLookup by epic_{world.tag} — nothing is filed automatically." in description
    async with world.sessions() as db:
        (link,) = (await db.execute(select(SystemicClusterEpic).where(
            SystemicClusterEpic.project_id == world.pid))).scalars().all()
        defects = await db.scalar(select(func.count()).select_from(Defect).where(Defect.project_id == world.pid))
        events = (await db.execute(select(ProjectActivityEvent.event_type, ProjectActivityEvent.entity_id).where(
            ProjectActivityEvent.project_id == world.pid))).all()
        claims = (await db.execute(select(AgentActionLedger.target_type, AgentActionLedger.status).where(
            AgentActionLedger.project_id == world.pid))).all()
    assert (link.membership_key, link.jira_key, link.created_by) == (key, "EPX-1", world.uid)
    assert link.cluster_label == "Gateway timeouts on checkout" and link.member_fingerprints == sorted(world.members)
    assert defects == 0
    assert events == [("cluster.epic_created", key)]
    assert claims == [("systemic_cluster", "executed")]


async def test_a_second_post_reuses_the_link_and_files_nothing(world):
    out = await _create(world, world.pid, _key(world.members))
    assert out["created"] is False and out["jira_key"] == "EPX-1"
    assert list(world.state.issues) == ["EPX-1"]


async def test_the_status_lists_the_link_with_the_filers_name(world):
    status = await _status(world, world.pid)
    assert (status["enabled"], status["available"], status["reason"]) == (True, True, None)
    assert status["epic_type_available"] is True and status["min_co_failure_runs"] == 3
    (link,) = status["links"]
    assert (link["membership_key"], link["jira_key"], link["created_by_name"]) == (
        _key(world.members), "EPX-1", "Epic Filer")


async def test_a_nightly_recluster_with_the_same_members_keeps_the_link(world):
    from app.models.postgres import SystemicFlakeCluster

    async with world.sessions() as db:
        before = await db.scalar(select(SystemicFlakeCluster.id).where(SystemicFlakeCluster.project_id == world.pid))
    async with world.sessions.begin() as db:                 # the nightly full delete + re-insert
        await world.store_clusters(db, world.pid, [_cluster(list(reversed(world.members)), rank="sfc_002")])
    async with world.sessions() as db:
        after = await db.scalar(select(SystemicFlakeCluster.id).where(SystemicFlakeCluster.project_id == world.pid))
    assert after != before                                   # a new row: only the membership_key survived
    (link,) = (await _status(world, world.pid))["links"]
    assert link["jira_key"] == "EPX-1"


async def test_an_erased_filer_reads_as_erased_user(world):
    async with world.sessions.begin() as db:
        await db.execute(text("UPDATE users SET erased_at = now() WHERE id = :u"), {"u": world.uid})
    try:
        (link,) = (await _status(world, world.pid))["links"]
        assert link["created_by_name"] == "Erased user"
    finally:
        async with world.sessions.begin() as db:
            await db.execute(text("UPDATE users SET erased_at = NULL WHERE id = :u"), {"u": world.uid})


async def test_a_changed_member_set_shows_no_link(world):
    from app.services import cluster_epic_service as ces

    grown = [*world.members, f"{world.tag}-d"]
    async with world.sessions.begin() as db:
        await world.store_clusters(db, world.pid, [_cluster(grown)])
    status = await _status(world, world.pid)
    assert status["links"] == []                             # the old key is no longer a cluster: not listed
    async with world.sessions() as db:
        with pytest.raises(HTTPException) as gone:
            await ces.preview_epic(db, world.pid, _key(world.members))
        preview = await ces.preview_epic(db, world.pid, _key(grown))
    assert gone.value.status_code == 404
    assert preview["member_count"] == 4 and preview["jira_project_key"] == "EPX"
    assert list(world.state.issues) == ["EPX-1"]


async def test_a_project_without_the_opt_in_gets_403_and_jira_receives_nothing(world):
    from app.models.postgres import AgentActionLedger, SystemicClusterEpic

    requests_before = len(world.state.requests)
    with pytest.raises(HTTPException) as refused:
        await _create(world, world.off_pid, _key(world.members))
    assert (refused.value.status_code, refused.value.detail) == (403, "Epic suggestions are off for this project")
    assert list(world.state.issues) == ["EPX-1"] and len(world.state.requests) == requests_before
    async with world.sessions() as db:
        links = await db.scalar(select(func.count()).select_from(SystemicClusterEpic).where(
            SystemicClusterEpic.project_id == world.off_pid))
        claims = await db.scalar(select(func.count()).select_from(AgentActionLedger).where(
            AgentActionLedger.project_id == world.off_pid))
    assert (links, claims) == (0, 0)
    status = await _status(world, world.off_pid)
    assert status["enabled"] is False and status["epic_type_available"] is None and status["links"] == []
