"""W21 ACT-01 slice (a): every run / analysis / investigation / release event has a producer that WRITES a row.

"Declaration is not emission": an event in the registry that nothing records is a filter option that matches
nothing, and an OUTCOME event recorded without a session is dropped and only counted. So each test here drives the
real route handler or service against a real database and reads ``project_activity_events`` back:

* release phases: added, updated (only on a real difference), advanced (into ``in_progress``), deleted;
* a manual run link: ``release.linked_run`` AND ``run.release_attached``; an unlink: ``release.unlinked_run``;
* a release entering a terminal status: ``release.closed`` (beside ``release.updated``);
* an external sync: one ``release.synced_external`` per release the sync touched;
* a run delete: ``run.deleted`` survives (attempt mode) and keeps the build label;
* a live session: ``run.live_started`` on open, ``run.live_finalized`` on close;
* the worker helper: ``analysis.failed``, ``investigation.*`` and ``run.ingest_failed`` land as rows.

Requires ``TESTLOOKUP_POSTGRES_TEST_DSN`` and a database at head; skips otherwise.
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
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
    import app.db.postgres as pg
    from app.models.postgres import (
        Project,
        ProjectActivityEvent,
        Release,
        ReleasePhase,
        ReleaseTestRunLink,
        TestRun,
        User,
        UserRole,
    )

    engine = create_async_engine(_dsn(), pool_size=2, max_overflow=0)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    # Attempt-mode rows and the worker helper open their own session.
    monkeypatch.setattr(pg, "AsyncSessionLocal", factory, raising=False)

    marker = "zqx" + uuid.uuid4().hex[:8].replace("a", "q")
    lead = User(id=uuid.uuid4(), email=f"lead-{marker}@example.test", username=f"lead-{marker}",
                full_name="Priya Lead", hashed_password="x", role=UserRole.ADMIN, is_active=True)
    project = Project(id=uuid.uuid4(), name=f"Ledger {marker}", slug=f"ledger-{marker}", is_active=True)
    release = Release(id=uuid.uuid4(), project_id=project.id, name=f"2.14.0-{marker}", status="in_progress")
    run = TestRun(id=uuid.uuid4(), project_id=project.id, build_number=f"b-{marker}", jenkins_job="ledger",
                  ingestion_source="unknown")
    async with factory() as db:
        db.add_all([lead, project])
        await db.flush()
        db.add_all([release, run])
        await db.commit()
    try:
        yield SimpleNamespace(factory=factory, lead=lead, project=project, release=release, run=run, marker=marker)
    finally:
        async with factory() as db:
            await db.execute(delete(ProjectActivityEvent).where(ProjectActivityEvent.project_id == project.id))
            await db.execute(delete(ReleaseTestRunLink).where(ReleaseTestRunLink.project_id == project.id))
            await db.execute(delete(ReleasePhase).where(
                ReleasePhase.release_id.in_(select(Release.id).where(Release.project_id == project.id))))
            await db.execute(delete(TestRun).where(TestRun.project_id == project.id))
            await db.execute(delete(Release).where(Release.project_id == project.id))
            await db.execute(delete(Project).where(Project.id == project.id))
            await db.execute(delete(User).where(User.id == lead.id))
            await db.commit()
        await engine.dispose()


async def _rows(w, event_type: str | None = None):
    from app.models.postgres import ProjectActivityEvent

    async with w.factory() as db:
        stmt = select(ProjectActivityEvent).where(ProjectActivityEvent.project_id == w.project.id)
        if event_type:
            stmt = stmt.where(ProjectActivityEvent.event_type == event_type)
        rows = (await db.execute(stmt.order_by(ProjectActivityEvent.occurred_at))).scalars().all()
    for row in rows:
        # A template whose context key no producer supplies renders an em dash.
        assert "—" not in row.summary, f"unfilled placeholder in {row.event_type}: {row.summary}"
    return rows


async def test_phase_add_update_advance_delete_each_write_a_row(world):
    w = world
    from app.routers import releases as r

    async with w.factory() as db:
        phase = await r.add_phase(str(w.release.id), r.PhaseIn(name="Soak"), db=db, current_user=w.lead, __=w.lead)
    phase_id = str(phase["id"])
    async with w.factory() as db:
        await r.update_phase(str(w.release.id), phase_id, r.PhaseUpdate(status="in_progress"), db=db,
                             current_user=w.lead, __=w.lead)
    async with w.factory() as db:
        # Re-sending the current value is not a change and must write nothing.
        await r.update_phase(str(w.release.id), phase_id, r.PhaseUpdate(status="in_progress"), db=db,
                             current_user=w.lead, __=w.lead)
    async with w.factory() as db:
        await r.delete_phase(str(w.release.id), phase_id, db=db, current_user=w.lead, __=w.lead)

    rows = await _rows(w)
    assert [x.event_type for x in rows] == [
        "release.phase_added", "release.phase_updated", "release.phase_advanced", "release.phase_deleted",
    ]
    assert all(x.actor_id == w.lead.id and x.release_id == w.release.id for x in rows)
    assert rows[0].summary == f"Phase Soak was added to release {w.release.name}"
    assert rows[1].diff["changed_fields"] == ["actual_start", "status"]
    assert rows[1].diff["after"] == {"status": "in_progress"}
    assert rows[2].summary == f"Release {w.release.name} advanced to Soak"
    assert rows[3].summary == f"Phase Soak was removed from release {w.release.name}"


async def test_link_writes_both_ends_and_unlink_writes_one(world):
    w = world
    from app.routers import releases as r

    async def _noop(*_a, **_k):
        return None

    import app.services.cache_service as cache

    original = cache.bump_analytics_epoch
    cache.bump_analytics_epoch = _noop
    try:
        async with w.factory() as db:
            await r.link_test_run(str(w.release.id), r.LinkRunRequest(test_run_id=str(w.run.id)), db=db,
                                  current_user=w.lead, __=w.lead)
        async with w.factory() as db:
            await r.unlink_test_run(str(w.release.id), str(w.run.id), db=db, current_user=w.lead, __=w.lead,
                                    ___=w.lead)
    finally:
        cache.bump_analytics_epoch = original

    linked = await _rows(w, "release.linked_run")
    attached = await _rows(w, "run.release_attached")
    unlinked = await _rows(w, "release.unlinked_run")
    assert len(linked) == len(attached) == len(unlinked) == 1
    assert linked[0].target_id == str(w.run.id)
    assert attached[0].entity_id == str(w.run.id)
    assert attached[0].summary == f"Build b-{w.marker} was attached to release {w.release.name}"
    assert unlinked[0].target_id == str(w.run.id)


async def test_a_release_entering_a_terminal_status_is_closed(world):
    w = world
    from app.routers import releases as r

    async with w.factory() as db:
        await r.update_release(str(w.release.id), r.ReleaseUpdate(status="released"), db=db,
                               current_user=w.lead, __=w.lead)
    async with w.factory() as db:
        # Already terminal: a second terminal status is not a second close.
        await r.update_release(str(w.release.id), r.ReleaseUpdate(status="archived"), db=db,
                               current_user=w.lead, __=w.lead)

    closed = await _rows(w, "release.closed")
    assert len(closed) == 1
    assert closed[0].diff["before"] == {"status": "in_progress"}
    assert closed[0].diff["after"] == {"status": "released"}
    assert len(await _rows(w, "release.updated")) == 2


async def test_an_external_sync_records_each_release_it_touched(world, monkeypatch):
    w = world
    from app.models.postgres import Release
    from app.routers import releases as r
    from app.services import github_release_sync

    async def _fake_sync(db, project_id):
        db.add(Release(id=uuid.uuid4(), project_id=project_id, name=f"M1-{w.marker}", status="planning",
                       source_system="github", external_id="1", last_synced_at=datetime.now(timezone.utc)))
        return {"created": 1, "updated": 0, "skipped": 0}

    monkeypatch.setattr(github_release_sync, "sync_milestones", _fake_sync)
    async with w.factory() as db:
        await r.sync_releases_from_external(r.ReleaseSyncIn(project_id=str(w.project.id), source="github"),
                                            db=db, current_user=w.lead)

    rows = await _rows(w, "release.synced_external")
    assert [x.entity_label for x in rows] == [f"M1-{w.marker}"]
    assert rows[0].summary == f"Release M1-{w.marker} synced from GitHub"


async def test_a_run_delete_survives_and_keeps_the_build_label(world, monkeypatch):
    w = world
    import app.db.mongo as mongo
    import app.worker.tasks as tasks
    from app.models.schemas import DeleteRunRequest
    from app.routers import runs as r
    from app.services import deletion_job_service, run_deletion_service

    async def _none(*_a, **_k):
        return []

    async def _job(**_k):
        return uuid.uuid4()

    monkeypatch.setattr(run_deletion_service, "citation_blockers", _none)
    monkeypatch.setattr(run_deletion_service, "status_blocks_deletion", lambda _s: False)
    monkeypatch.setattr(deletion_job_service, "open_job", _job)
    monkeypatch.setattr(mongo, "get_mongo_db", lambda: None)
    monkeypatch.setattr(tasks.delete_run_everywhere, "delay", lambda *a, **k: None)

    async with w.factory() as db:
        await r.delete_run(w.run.id, DeleteRunRequest(confirm=True, reason="duplicate upload"), db=db,
                           current_user=w.lead, _=w.lead)
        await db.rollback()  # the handler never commits; the row must not depend on it

    rows = await _rows(w, "run.deleted")
    assert len(rows) == 1
    assert rows[0].summary == f"Build b-{w.marker} was deleted"
    assert rows[0].context["reason"] == "duplicate upload"


async def test_a_live_session_records_start_and_finalize(world, monkeypatch):
    w = world
    from app.routers import stream as r
    from app.services import stream_service

    async def _create(db, payload, bound_project_id=None, user=None):
        return SimpleNamespace(project_id=str(w.project.id), run_id=str(w.run.id))

    async def _close(db, session_id, bound_project_id=None, user=None):
        return w.project.id

    async def _noop(*_a, **_k):
        return None

    import app.services.cache_service as cache

    monkeypatch.setattr(stream_service, "create_session", _create)
    monkeypatch.setattr(stream_service, "close_session", _close)
    monkeypatch.setattr(stream_service, "finalize_closed_session_redis", _noop)
    monkeypatch.setattr(cache, "bump_analytics_epoch", _noop)
    monkeypatch.setattr(r, "ensure_ingest_role", lambda _u: None)

    payload = SimpleNamespace(build_number=f"b-{w.marker}", framework="pytest")
    async with w.factory() as db:
        await r.create_session(payload, db=db, auth=(w.lead, None))
    async with w.factory() as db:
        await r.close_session(str(w.run.id), db=db, auth=(w.lead, None))

    started, finalized = await _rows(w, "run.live_started"), await _rows(w, "run.live_finalized")
    assert started[0].summary == f"Live run Build b-{w.marker} started"
    assert finalized[0].summary == f"Live run Build b-{w.marker} finalized"
    assert started[0].entity_id == finalized[0].entity_id == str(w.run.id)


@pytest.mark.parametrize("event_type,context", [
    ("analysis.failed", {"error": "TimeoutError", "workflow_type": "offline"}),
    ("investigation.started", None),
    ("investigation.completed", {"stages": 5, "errors": 0}),
    ("run.ingest_failed", {"error": "empty_report", "file_name": "junit.xml", "format": "junit"}),
])
async def test_the_worker_helper_commits_its_own_row(world, event_type, context):
    """Outcome events from a Celery task: no request session exists, so the helper opens and commits its own."""
    w = world
    from app.worker.tasks import _record_worker_activity

    await _record_worker_activity(event_type, project_id=str(w.project.id), run_id=str(w.run.id),
                                  build_number=f"b-{w.marker}", actor_ref="ai_pipeline", context=context)

    rows = await _rows(w, event_type)
    assert len(rows) == 1
    assert rows[0].actor_type == "system"
    assert f"Build b-{w.marker}" in rows[0].summary
