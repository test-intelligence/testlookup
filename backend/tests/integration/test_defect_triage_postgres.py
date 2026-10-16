"""Real PostgreSQL proof of the DEF-01 triage PATCH.

``PATCH /api/v1/projects/{project_id}/defects/{defect_id}`` through the real
router, guards, service, ``analytics_service.defect_row`` and the activity
ledger, on a database migrated to head (0213: ``defects.assignee_user_id``).
What a mocked session cannot show:

* the project filter on the path id, the membership check on the body
  assignee id, and the 404 / 422 texts, against real rows;
* the partial unique index ``ix_defects_test_case_open_unique`` refusing a
  concurrent second OPEN that slipped past the service's check (two sessions),
  answered with the same 409 as the check, the ledger row dying with the
  rollback;
* the row the response carries is the list row the page renders.

Requires ``TESTLOOKUP_POSTGRES_TEST_DSN``; skips otherwise. Rows are created
under a fresh project per test module and deleted afterwards.
"""
from __future__ import annotations

import asyncio
import os
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytest.importorskip("asyncpg")

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]


def _dsn() -> str:
    value = os.environ.get("TESTLOOKUP_POSTGRES_TEST_DSN", "").strip()
    if not value:
        pytest.skip("TESTLOOKUP_POSTGRES_TEST_DSN is not configured")
    return value


def _user(role, tag, name, **flags):
    from app.models.postgres import User

    uid = uuid.uuid4()
    return User(id=uid, email=f"{uid.hex[:12]}-{tag}@example.test", username=f"{name.split()[0].lower()}-{uid.hex[:8]}",
                full_name=name, hashed_password="x", role=role, is_active=flags.get("active", True),
                is_service_account=flags.get("service", False), is_synthetic=flags.get("synthetic", False))


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def world():
    from app.core.config import settings
    from app.core.deps import get_current_active_user
    from app.db.postgres import get_db
    from app.models.postgres import Defect, Project, ProjectMember, TestCase, TestRun, UserRole
    from app.routers import defect_jira as defect_jira_router

    engine = create_async_engine(_dsn(), pool_size=5, max_overflow=0)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    tag = uuid.uuid4().hex[:8]
    pid, qid = uuid.uuid4(), uuid.uuid4()
    run_id, case_a, case_b = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    now = datetime.now(timezone.utc)
    people = SimpleNamespace(
        engineer=_user(UserRole.QA_ENGINEER, tag, "Quinn Engineer"),
        viewer=_user(UserRole.VIEWER, tag, "Vic Viewer"),
        priya=_user(UserRole.TESTER, tag, "Priya Nair"),
        stranger=_user(UserRole.QA_ENGINEER, tag, "Sam Elsewhere"),
        admin=_user(UserRole.ADMIN, tag, "Ada Admin"),
        inactive=_user(UserRole.QA_ENGINEER, tag, "Ina Gone", active=False),
        service=_user(UserRole.QA_ENGINEER, tag, "Robo Service", service=True),
        synthetic=_user(UserRole.QA_LEAD, tag, "Default Lead", synthetic=True),
    )
    async with sessions.begin() as db:
        db.add_all([Project(id=pid, name=f"triage-{tag}", slug=f"triage-{tag}", is_active=True),
                    Project(id=qid, name=f"triage-q-{tag}", slug=f"triage-q-{tag}", is_active=True)])
        db.add_all(list(vars(people).values()))
        await db.flush()
        for person in (people.engineer, people.viewer, people.priya, people.inactive, people.service,
                       people.synthetic):
            db.add(ProjectMember(user_id=person.id, project_id=pid, role=person.role))
        db.add(ProjectMember(user_id=people.stranger.id, project_id=qid, role="QA_ENGINEER"))
        db.add(TestRun(id=run_id, project_id=pid, build_number=f"b-{tag}", branch="main", status="FAILED",
                       total_tests=2, failed_tests=2, created_at=now - timedelta(hours=1),
                       start_time=now - timedelta(hours=1)))
        await db.flush()
        for case_id, name in ((case_a, "test_checkout_total"), (case_b, "test_refund_twice")):
            db.add(TestCase(id=case_id, test_run_id=run_id, test_fingerprint=f"{tag}-{name}", test_name=name,
                            suite_name="Checkout", status="FAILED", created_at=now - timedelta(minutes=30)))

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(settings, "AI_OFFLINE_MODE", True)

    async def resolved(db):
        return {"enabled": True, "domain": "acme.atlassian.net", "email": "bot@example.test",
                "api_token": "tok", "default_project_key": "QA"}

    monkeypatch.setattr("app.services.defect_jira_service.resolve_jira_config", resolved)
    bumps: list = []

    async def _bump(project_id):
        bumps.append(str(project_id))

    monkeypatch.setattr("app.services.cache_service.bump_analytics_epoch", _bump)

    async def _get_db():
        async with sessions() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    async def patch(defect_id, body, *, principal=None, project_id=None):
        app = FastAPI()
        app.include_router(defect_jira_router.router)
        app.dependency_overrides[get_db] = _get_db
        app.dependency_overrides[get_current_active_user] = lambda: principal or people.engineer
        async with AsyncClient(transport=ASGITransport(app=app, raise_app_exceptions=False),
                               base_url="http://t") as client:
            return await client.patch(f"/api/v1/projects/{project_id or pid}/defects/{defect_id}", json=body)

    async def new_defect(**fields):
        row = Defect(id=uuid.uuid4(), project_id=fields.pop("project_id", pid), title=fields.pop("title", "Total off"),
                     resolution_status=fields.pop("resolution_status", "OPEN"), approval_status="approved",
                     severity=fields.pop("severity", "HIGH"), **fields)
        async with sessions.begin() as db:
            db.add(row)
        return row.id

    async def load(defect_id):
        async with sessions() as db:
            return (await db.execute(select(Defect).where(Defect.id == defect_id))).scalar_one()

    async def ledger(defect_id):
        async with sessions() as db:
            rows = await db.execute(text(
                "SELECT event_type, summary, context, diff FROM project_activity_events "
                "WHERE entity_type = 'defect' AND entity_id = :e ORDER BY occurred_at"), {"e": str(defect_id)})
            return [dict(r._mapping) for r in rows]

    try:
        yield SimpleNamespace(engine=engine, sessions=sessions, pid=pid, qid=qid, case_a=case_a, case_b=case_b,
                              people=people, patch=patch, new_defect=new_defect, load=load, ledger=ledger,
                              bumps=bumps)
    finally:
        monkeypatch.undo()
        async with engine.begin() as db:
            await db.execute(text("DELETE FROM project_activity_events WHERE project_id IN (:p, :q)"),
                             {"p": pid, "q": qid})
            await db.execute(text("DELETE FROM defects WHERE project_id IN (:p, :q)"), {"p": pid, "q": qid})
            await db.execute(text("DELETE FROM projects WHERE id IN (:p, :q)"), {"p": pid, "q": qid})
            await db.execute(text("DELETE FROM users WHERE id = ANY(:ids)"),
                             {"ids": [u.id for u in vars(people).values()]})
        await engine.dispose()


async def test_a_foreign_defect_and_a_random_id_are_one_404(world):
    foreign = await world.new_defect(project_id=world.qid)

    a = await world.patch(foreign, {"resolution_status": "RESOLVED"})
    b = await world.patch(uuid.uuid4(), {"resolution_status": "RESOLVED"})

    assert a.status_code == b.status_code == 404
    assert a.json() == b.json() == {"detail": "Defect not found in this project."}
    assert (await world.load(foreign)).resolution_status == "OPEN"


async def test_a_viewer_member_and_a_non_member_engineer_are_refused(world):
    target = await world.new_defect()

    assert (await world.patch(target, {"owner_team": "x"}, principal=world.people.viewer)).status_code == 403
    assert (await world.patch(target, {"owner_team": "x"}, principal=world.people.stranger)).status_code == 403
    assert (await world.load(target)).owner_team is None


@pytest.mark.parametrize("who", ["stranger", "inactive", "service", "synthetic", "unknown", "malformed"])
async def test_ineligible_assignees_share_one_422(world, who):
    target = await world.new_defect()
    value = {"unknown": str(uuid.uuid4()), "malformed": "42"}.get(who) or str(getattr(world.people, who).id)

    response = await world.patch(target, {"assignee_user_id": value})

    assert response.status_code == 422
    assert response.json()["detail"] == "That user cannot be assigned defects in this project."
    assert (await world.load(target)).assignee_user_id is None


async def test_assigning_a_member_lands_on_the_row_the_page_renders(world):
    target = await world.new_defect()

    response = await world.patch(target, {"assignee_user_id": str(world.people.priya.id)})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["changed_fields"] == ["assignee"]
    assert body["defect"]["id"] == str(target)
    assert body["defect"]["assignee_user_id"] == str(world.people.priya.id)
    assert body["defect"]["assignee_name"] == "Priya Nair"
    assert (await world.load(target)).assignee_user_id == world.people.priya.id
    (row,) = await world.ledger(target)
    assert row["event_type"] == "defect.updated"
    assert row["summary"] == "Defect Total off changed: assignee → Priya Nair"
    assert str(world.people.priya.id) not in str(row["diff"])
    assert world.bumps and world.bumps[-1] == str(world.pid)


async def test_an_admin_who_is_not_a_member_can_be_assigned(world):
    target = await world.new_defect()

    response = await world.patch(target, {"assignee_user_id": str(world.people.admin.id)})

    assert response.status_code == 200, response.text
    assert (await world.load(target)).assignee_user_id == world.people.admin.id


async def test_resolving_stamps_resolved_at_and_reopening_clears_it(world):
    target = await world.new_defect(test_case_id=world.case_b)

    resolved = await world.patch(target, {"resolution_status": "RESOLVED"})
    assert resolved.status_code == 200, resolved.text
    assert (await world.load(target)).resolved_at is not None

    reopened = await world.patch(target, {"resolution_status": "OPEN"})
    assert reopened.status_code == 200, reopened.text
    row = await world.load(target)
    assert row.resolution_status == "OPEN" and row.resolved_at is None
    await world.patch(target, {"resolution_status": "INVALID"})


async def test_reopening_onto_an_open_twin_is_409_with_the_twin(world):
    twin = await world.new_defect(test_case_id=world.case_a, jira_ticket_id="QA-501")
    closed = await world.new_defect(test_case_id=world.case_a, resolution_status="RESOLVED")

    response = await world.patch(closed, {"resolution_status": "OPEN"})

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["code"] == "defect_already_open" and detail["defect_id"] == str(twin)
    assert "QA-501" in detail["message"]
    assert (await world.load(closed)).resolution_status == "RESOLVED"
    assert await world.ledger(closed) == []
    async with world.sessions.begin() as db:
        await db.execute(text("UPDATE defects SET resolution_status = 'RESOLVED' WHERE id = :i"), {"i": twin})


async def test_a_concurrent_open_that_slipped_past_the_check_is_the_same_409(world):
    """Session A re-opens defect X and holds its transaction. The PATCH re-opens
    Y for the same test case: its check cannot see A's uncommitted OPEN, so it
    passes; its flush then waits on the unique index, and when A commits it
    raises. The route rolls back (taking the ledger row with it) and answers
    the check's 409."""
    from app.models.postgres import Defect
    from app.services import defect_status

    x = await world.new_defect(test_case_id=world.case_a, resolution_status="RESOLVED")
    y = await world.new_defect(test_case_id=world.case_a, resolution_status="RESOLVED")

    async with world.sessions() as holder:
        row = (await holder.execute(select(Defect).where(Defect.id == x))).scalar_one()
        defect_status.apply_status(row, "OPEN", now=datetime.now(timezone.utc))
        await holder.flush()  # X is OPEN in A's open transaction

        task = asyncio.create_task(world.patch(y, {"resolution_status": "OPEN", "note": "race"}))
        for _ in range(100):
            async with world.engine.connect() as probe:
                waiting = (await probe.execute(text(
                    "SELECT count(*) FROM pg_locks WHERE NOT granted AND locktype = 'transactionid'"))).scalar()
            if waiting:
                break
            await asyncio.sleep(0.05)
        assert waiting, "the PATCH never reached the unique index"
        await holder.commit()

    response = await asyncio.wait_for(task, timeout=10)

    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "defect_already_open"
    assert response.json()["detail"]["defect_id"] == str(x)
    assert (await world.load(y)).resolution_status == "RESOLVED"
    assert await world.ledger(y) == []
    async with world.sessions.begin() as db:
        await db.execute(text("UPDATE defects SET resolution_status = 'RESOLVED' WHERE id = :i"), {"i": x})


async def test_offline_a_key_is_saved_locally_and_ledgered(world, monkeypatch):
    import httpx

    calls: list = []
    original = httpx.AsyncClient.send

    async def _send(client, request, *args, **kwargs):
        if not isinstance(getattr(client, "_transport", None), httpx.ASGITransport):
            calls.append(request)
            raise AssertionError(f"offline PATCH called {request.url}")
        return await original(client, request, *args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "send", _send)
    target = await world.new_defect()

    response = await world.patch(target, {"jira_key": "abc-123"})

    assert response.status_code == 200, response.text
    assert response.json()["jira"]["status"] == "saved_locally"
    assert response.json()["defect"]["jira_ticket_url"] == "https://acme.atlassian.net/browse/ABC-123"
    row = await world.load(target)
    assert (row.jira_ticket_id, row.jira_ticket_url) == ("ABC-123", "https://acme.atlassian.net/browse/ABC-123")
    (entry,) = await world.ledger(target)
    assert entry["event_type"] == "defect.ticket_linked"
    assert entry["context"]["validated"] is False
    assert calls == []
