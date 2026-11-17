"""D-0021 on real PostgreSQL, through the route: a reassignment by hand is in the project's Activity log.

``PUT /me/assigned-failures/{id}/reassign`` moved the failure and logged a structlog line only. It now writes
``failure.reassigned`` in the same transaction (owner decision Q4: this one event), which the real feed
(``activity.query.list_events``, trigram ILIKE search) returns, searchable by "reassigned", "assigned" and the test
name, and absent from another project. A same-owner submit and a refused move record nothing.

The ``world`` fixture follows ``test_activity_ledger_slice_c_postgres.py``: requires ``TESTLOOKUP_POSTGRES_TEST_DSN``
migrated to head (skips otherwise), a unique marker, cleanup by delete. Runs in CI as part of the
``postgres-integration`` job's file list in ``.github/workflows/ci.yml``.
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

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
        Project, ProjectActivityEvent, ProjectMember, TestCase, TestRun, TestStatus, User, UserRole,
    )

    engine = create_async_engine(_dsn(), pool_size=3, max_overflow=0)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(pg, "AsyncSessionLocal", factory, raising=False)

    marker = "zqx" + uuid.uuid4().hex[:8].replace("a", "q")

    def person(name, role, full):
        return User(id=uuid.uuid4(), email=f"{name}-{marker}@example.test", username=f"{name}-{marker}",
                    full_name=full, hashed_password="x", role=role, is_active=True)

    lead = person("lead", UserRole.QA_LEAD, "Priya Lead")
    dana = person("dana", UserRole.QA_ENGINEER, "Dana Whitfield")
    bart = person("bart", UserRole.QA_ENGINEER, "Bart Nair")
    viewer = person("view", UserRole.VIEWER, "Vic Viewer")
    project = Project(id=uuid.uuid4(), name=f"Reassign {marker}", slug=f"reassign-{marker}", is_active=True)
    other = Project(id=uuid.uuid4(), name=f"Other {marker}", slug=f"other-{marker}", is_active=True)
    run = TestRun(id=uuid.uuid4(), project_id=project.id, build_number=f"b-{marker}", jenkins_job="reassign",
                  ingestion_source="unknown")
    test_name = f"synthetic_total_includes_delivery_{marker}"
    case = TestCase(id=uuid.uuid4(), test_run_id=run.id, test_name=test_name, test_fingerprint=f"fp-{marker}",
                    status=TestStatus.FAILED, assigned_to_user_id=dana.id)
    async with factory() as db:
        db.add_all([lead, dana, bart, viewer, project, other])
        await db.flush()
        for user, role in ((lead, "QA_LEAD"), (dana, "QA_ENGINEER"), (bart, "QA_ENGINEER"), (viewer, "VIEWER")):
            db.add(ProjectMember(project_id=project.id, user_id=user.id, role=role))
        db.add(run)
        await db.flush()
        db.add(case)
        await db.commit()
    try:
        yield SimpleNamespace(factory=factory, lead=lead, dana=dana, bart=bart, viewer=viewer, project=project,
                              other=other, run=run, case=case, test_name=test_name)
    finally:
        async with factory() as db:
            for pid in (project.id, other.id):
                await db.execute(delete(ProjectActivityEvent).where(ProjectActivityEvent.project_id == pid))
            await db.execute(delete(TestCase).where(TestCase.id == case.id))
            await db.execute(delete(TestRun).where(TestRun.id == run.id))
            await db.execute(delete(ProjectMember).where(ProjectMember.project_id == project.id))
            await db.execute(delete(Project).where(Project.id.in_([project.id, other.id])))
            await db.commit()
        async with factory() as db:
            try:
                await db.execute(delete(User).where(User.id.in_([lead.id, dana.id, bart.id, viewer.id])))
                await db.commit()
            except Exception:  # an audit row may still reference a user
                await db.rollback()
        await engine.dispose()


async def _move(w, to) -> None:
    from app.models.postgres import User
    from app.routers.my_failures import reassign_assigned_failure

    async with w.factory() as db:
        lead = await db.get(User, w.lead.id)
        await reassign_assigned_failure(test_case_id=w.case.id, new_assignee_user_id=to.id, db=db, current_user=lead)


async def _feed(w, project_id, **kw):
    from app.services.activity.query import ActivityFilters, list_events

    async with w.factory() as db:
        page = await list_events(db, project_id, ActivityFilters(since=datetime.now(timezone.utc) - timedelta(days=7), **kw))
    return [item for item in page["items"] if item["event_type"] == "failure.reassigned"]


async def test_a_reassignment_through_the_route_is_in_the_feed(world):
    from app.models.postgres import ProjectActivityEvent, TestCase

    w = world
    await _move(w, w.bart)
    rows = await _feed(w, w.project.id)
    assert len(rows) == 1
    row = rows[0]
    assert row["actor"]["id"] == str(w.lead.id)
    assert row["entity"]["type"] == "run" and row["entity"]["href"] == f"/runs/{w.run.id}"
    assert row["summary"] == f"Failure {w.test_name} reassigned from Dana Whitfield to Bart Nair"
    async with w.factory() as db:
        stored = (await db.execute(select(ProjectActivityEvent).where(
            ProjectActivityEvent.project_id == w.project.id,
            ProjectActivityEvent.event_type == "failure.reassigned",
        ))).scalar_one()
        assert stored.category == "quality"  # CHECK-valid
        assert (await db.get(TestCase, w.case.id)).assigned_to_user_id == w.bart.id
    for q in ("reassigned", "assigned", w.test_name):
        assert len(await _feed(w, w.project.id, q=q)) == 1, q
    assert await _feed(w, w.other.id) == []


async def test_a_same_owner_submit_and_a_refused_move_add_nothing(world):
    w = world
    await _move(w, w.bart)
    await _move(w, w.bart)
    with pytest.raises(HTTPException) as refused:
        await _move(w, w.viewer)
    assert refused.value.status_code == 422
    assert len(await _feed(w, w.project.id)) == 1
