"""W28 TCM-03 / TCM-04 / TCM-05 against real PostgreSQL.

* Bulk (``POST /cases/bulk``, through the real router and a real session):
  another project's case id is ``not_found`` and its row is untouched; 50 cases
  get 50 versions and ONE ledger row, a case left out of the ids is untouched;
  a database refusal inside one case's SAVEPOINT rolls back only that case and
  the request still commits; 200 cases in one request (the wall time is
  printed for the wave report).
* Restore: after two edits, restoring v1 saves v4 with v1's content; the
  history lists four versions with the names of who saved them.
* Executions remember their version: executed at v1, the case edited to v2, the
  item still says v1, the plan's item list says ``case_version`` 2, and the
  history's v1 lists the execution.

Self-owned, project-scoped fixtures in the configured database; the module
skips only when the explicit opt-in DSN is absent.
"""
from __future__ import annotations

import os
import time
import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.postgres import (
    ManagedTestCase,
    Project,
    ProjectActivityEvent,
    ProjectMember,
    TestCaseAuditLog as AuditRow,
    TestCaseVersion as VersionRow,
    TestPlan as PlanRow,
    User,
    UserRole,
)
from app.models.schemas import (
    ManagedTestCaseCreate,
    ManagedTestCaseUpdate,
    TestCaseBulkRequest as BulkRequest,
    TestCaseRestoreRequest as RestoreRequest,
    TestPlanItemCreate as PlanItemCreate,
)
from app.services.test_case_editing_service import (
    apply_bulk_action,
    case_history,
    restore_case_version,
)
from app.services.test_management_service import (
    add_test_plan_item,
    create_managed_test_case,
    record_test_plan_execution,
    update_managed_test_case,
)

asyncpg = pytest.importorskip("asyncpg")
pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def _dsn() -> str:
    value = os.getenv("TESTLOOKUP_POSTGRES_TEST_DSN", "").strip()
    if not value:
        pytest.skip("TESTLOOKUP_POSTGRES_TEST_DSN is not configured")
    return value


@pytest.fixture
async def pg_engine():
    engine = create_async_engine(_dsn(), pool_size=4, max_overflow=0)
    try:
        yield engine
    finally:
        await engine.dispose()
        # The routes' post-commit gauge refresh (emit_staged_test_management_metrics)
        # reads through the app's own cached engine, built on THIS test's loop:
        # dispose it here, or a later module's loop inherits connections bound to
        # a closed one ("Event loop is closed" in its fixture set-up).
        from app.db.postgres import dispose_engine_for_loop

        await dispose_engine_for_loop()


def _user(token: str, name: str, role: UserRole) -> User:
    return User(
        id=uuid.uuid4(),
        email=f"w28-{name}-{token}@example.invalid",
        username=f"w28-{name}-{token}",
        full_name=f"W28 {name.title()}",
        hashed_password="not-a-real-password",
        role=role,
        is_active=True,
    )


@pytest.fixture
async def world(pg_engine):
    token = uuid.uuid4().hex[:12]
    tester = _user(token, "tester", UserRole.TESTER)
    lead = _user(token, "lead", UserRole.QA_LEAD)
    project = Project(id=uuid.uuid4(), name=f"W28 editing {token}", slug=f"w28-editing-{token}")
    other = Project(id=uuid.uuid4(), name=f"W28 other {token}", slug=f"w28-other-{token}")
    factory = async_sessionmaker(pg_engine, expire_on_commit=False)
    async with factory() as session:
        session.add_all([tester, lead, project, other])
        await session.flush()
        session.add_all([
            ProjectMember(user_id=tester.id, project_id=project.id, role=UserRole.TESTER.value),
            ProjectMember(user_id=lead.id, project_id=project.id, role=UserRole.QA_LEAD.value),
        ])
        await session.commit()
    try:
        yield SimpleNamespace(factory=factory, tester=tester, lead=lead, project=project, other=other)
    finally:
        async with factory() as session:
            await session.execute(delete(AuditRow).where(AuditRow.project_id.in_([project.id, other.id])))
            await session.execute(delete(Project).where(Project.id.in_([project.id, other.id])))
            await session.execute(delete(User).where(User.id.in_([tester.id, lead.id])))
            await session.commit()


async def _insert_cases(factory, project_id, n: int, *, status: str = "draft", priority: str = "medium"):
    """Authored cases at v1, each with its v1 snapshot (what create leaves)."""
    ids = []
    async with factory() as session:
        for index in range(n):
            case = ManagedTestCase(
                id=uuid.uuid4(), project_id=project_id, title=f"W28 case {index:03d}", status=status,
                version=1, priority=priority, severity="major", test_type="functional", tags=["smoke"],
                is_automated=False, automation_status="not_automated", ai_generated=False,
            )
            session.add(case)
            ids.append(case.id)
        await session.flush()
        for case_id in ids:
            session.add(VersionRow(
                test_case_id=case_id, version=1, title="W28 case", status=status,
                change_type="created", source="person",
            ))
        await session.commit()
    return ids


def _client(world, user):
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient

    from app.core.deps import get_current_active_user
    from app.db.postgres import get_db
    from app.routers import test_management

    app = FastAPI()
    app.include_router(test_management.router)

    async def _db():
        async with world.factory() as session:
            yield session

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_current_active_user] = lambda: user
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://w28.test")


async def _counts(factory, project_id, case_ids):
    async with factory() as session:
        versions = (await session.execute(
            select(func.count()).select_from(VersionRow).where(VersionRow.test_case_id.in_(case_ids))
        )).scalar_one()
        ledger = (await session.execute(
            select(ProjectActivityEvent).where(
                ProjectActivityEvent.project_id == project_id,
                ProjectActivityEvent.event_type == "test_case.bulk_changed",
            )
        )).scalars().all()
    return versions, ledger


# ═══════════════════════ bulk ═══════════════════════


async def test_a_foreign_case_id_is_not_found_and_its_row_is_untouched(world):
    mine = await _insert_cases(world.factory, world.project.id, 2)
    foreign = await _insert_cases(world.factory, world.other.id, 1)
    async with _client(world, world.tester) as client:
        resp = await client.post("/api/v1/test-management/cases/bulk", json={
            "project_id": str(world.project.id),
            "case_ids": [str(mine[0]), str(foreign[0]), str(mine[1])],
            "action": "set_priority", "priority": "critical",
        })
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert [r["outcome"] for r in body["results"]] == ["changed", "not_found", "changed"]
    assert body["results"][1] == {"id": str(foreign[0]), "outcome": "not_found", "reason": None, "version": None, "status": None}
    async with world.factory() as session:
        row = await session.get(ManagedTestCase, foreign[0])
        assert (row.priority, row.version) == ("medium", 1)
        versions = (await session.execute(
            select(func.count()).select_from(VersionRow).where(VersionRow.test_case_id == foreign[0])
        )).scalar_one()
        assert versions == 1


async def test_naming_a_project_the_caller_is_not_in_is_403_before_any_load(world):
    foreign = await _insert_cases(world.factory, world.other.id, 1)
    async with _client(world, world.tester) as client:
        resp = await client.post("/api/v1/test-management/cases/bulk", json={
            "project_id": str(world.other.id), "case_ids": [str(foreign[0])],
            "action": "set_priority", "priority": "critical",
        })
    assert resp.status_code == 403
    async with world.factory() as session:
        assert (await session.get(ManagedTestCase, foreign[0])).priority == "medium"


async def test_fifty_cases_get_fifty_versions_and_one_ledger_row_and_a_deselected_case_is_untouched(world):
    ids = await _insert_cases(world.factory, world.project.id, 51)
    chosen, left_out = ids[:50], ids[50]
    async with _client(world, world.tester) as client:
        resp = await client.post("/api/v1/test-management/cases/bulk", json={
            "project_id": str(world.project.id),
            "case_ids": [str(i) for i in chosen],
            "expected_versions": {str(i): 1 for i in chosen},
            "action": "add_tags", "tags": ["regression"],
        })
    assert resp.status_code == 200, resp.text
    assert resp.json()["changed"] == 50
    versions, ledger = await _counts(world.factory, world.project.id, chosen)
    assert versions == 50 + 50  # each case's v1 plus its bulk version
    assert len(ledger) == 1
    assert ledger[0].context == {"count": 50, "change": "tags + regression", "skipped": 0, "not_found": 0}
    assert ledger[0].entity_type == "test_library" and ledger[0].actor_id == world.tester.id
    async with world.factory() as session:
        untouched = await session.get(ManagedTestCase, left_out)
        assert (untouched.tags, untouched.version) == (["smoke"], 1)
        changed = await session.get(ManagedTestCase, chosen[0])
        assert (changed.tags, changed.version) == (["smoke", "regression"], 2)
        snapshot = (await session.execute(
            select(VersionRow).where(VersionRow.test_case_id == chosen[0], VersionRow.version == 2)
        )).scalar_one()
        assert (snapshot.change_type, snapshot.source, snapshot.changed_by_id) == ("bulk_updated", "person", world.tester.id)
        audits = (await session.execute(
            select(func.count()).select_from(AuditRow).where(
                AuditRow.entity_id.in_(chosen), AuditRow.action == "updated",
            )
        )).scalar_one()
        assert audits == 50


async def test_a_database_refusal_in_one_case_rolls_back_only_its_savepoint(world):
    ids = await _insert_cases(world.factory, world.project.id, 3)
    # A version-2 row already exists for the middle case: its activate snapshot
    # (version 2) violates uq_test_case_versions_case_version INSIDE its savepoint.
    async with world.factory() as session:
        session.add(VersionRow(test_case_id=ids[1], version=2, title="squatter", status="draft", change_type="updated"))
        await session.commit()
    async with world.factory() as session:
        actor = await session.get(User, world.tester.id)
        response = await apply_bulk_action(
            session,
            BulkRequest(project_id=world.project.id, case_ids=ids, action="transition", transition="activate"),
            actor,
            review_required=False,
        )
        await session.commit()
    assert [r.outcome for r in response.results] == ["changed", "skipped", "changed"]
    assert response.results[1].reason == "This case could not be changed"
    async with world.factory() as session:
        rows = {i: await session.get(ManagedTestCase, i) for i in ids}
        assert [(rows[i].status, rows[i].version) for i in ids] == [("active", 2), ("draft", 1), ("active", 2)]
    versions, ledger = await _counts(world.factory, world.project.id, ids)
    assert versions == 3 + 1 + 2  # three v1s, the squatter, two activations
    assert len(ledger) == 1 and ledger[0].context["count"] == 2


async def test_two_hundred_cases_in_one_request(world):
    ids = await _insert_cases(world.factory, world.project.id, 200, priority="low")
    async with _client(world, world.tester) as client:
        started = time.perf_counter()
        resp = await client.post("/api/v1/test-management/cases/bulk", json={
            "project_id": str(world.project.id),
            "case_ids": [str(i) for i in ids],
            "expected_versions": {str(i): 1 for i in ids},
            "action": "set_priority", "priority": "high",
        })
        elapsed = time.perf_counter() - started
    assert resp.status_code == 200, resp.text
    assert resp.json()["changed"] == 200
    print(f"W28_BULK_200_WALL_SECONDS={elapsed:.2f}")
    versions, ledger = await _counts(world.factory, world.project.id, ids)
    assert versions == 400 and len(ledger) == 1


# ═══════════════════════ restore + history ═══════════════════════


async def test_restore_after_two_edits_and_the_history_lists_four_versions_with_names(world):
    async with world.factory() as session:
        tester = await session.get(User, world.tester.id)
        created = await create_managed_test_case(session, ManagedTestCaseCreate(
            project_id=world.project.id, title="Pay by card",
            steps=[{"step_number": 1, "action": "Open checkout", "expected_result": "Checkout shows"}],
        ), tester)
        await session.commit()
        case_id = created.id
    async with world.factory() as session:
        lead = await session.get(User, world.lead.id)
        await update_managed_test_case(session, case_id, ManagedTestCaseUpdate(
            expected_version=1, title="Pay by card (3-D Secure)",
            steps=[{"step_number": 1, "action": "Open checkout and choose card", "expected_result": None}],
        ), lead)
        await session.commit()
    async with world.factory() as session:
        tester = await session.get(User, world.tester.id)
        await update_managed_test_case(session, case_id, ManagedTestCaseUpdate(
            expected_version=2, priority="high",
        ), tester)
        await session.commit()
    async with world.factory() as session:
        lead = await session.get(User, world.lead.id)
        case = await session.get(ManagedTestCase, case_id)
        restored = await restore_case_version(session, case, RestoreRequest(version=1, expected_version=3), lead)
        await session.commit()
        assert restored.version == 4
    async with world.factory() as session:
        case = await session.get(ManagedTestCase, case_id)
        assert (case.title, case.priority, case.status, case.version) == ("Pay by card", "medium", "draft", 4)
        assert case.steps == [{"step_number": 1, "action": "Open checkout", "expected_result": "Checkout shows"}]
        history = await case_history(session, case)
        ledger = (await session.execute(
            select(ProjectActivityEvent).where(
                ProjectActivityEvent.project_id == world.project.id,
                ProjectActivityEvent.event_type == "test_case.restored",
            )
        )).scalars().all()
    assert [h.version for h in history] == [4, 3, 2, 1]
    assert [h.change_type for h in history] == ["restored", "updated", "updated", "created"]
    assert [h.changed_by_name for h in history] == ["W28 Lead", "W28 Tester", "W28 Lead", "W28 Tester"]
    assert [h.source for h in history] == ["person"] * 4
    assert history[0].change_summary == "Restored from v1"
    assert len(ledger) == 1 and ledger[0].context == {"from_version": 1, "to_version": 4}
    assert ledger[0].entity_id == str(case_id)


async def test_an_execution_keeps_the_version_it_ran_against(world):
    from app.routers.test_management_plans import list_plan_items

    async with world.factory() as session:
        tester = await session.get(User, world.tester.id)
        case = await create_managed_test_case(session, ManagedTestCaseCreate(
            project_id=world.project.id, title="Refund a payment",
            steps=[{"step_number": 1, "action": "Refund v1", "expected_result": None}],
        ), tester)
        plan = PlanRow(project_id=world.project.id, name="W28 plan", created_by_id=tester.id)
        session.add(plan)
        await session.flush()
        item = await add_test_plan_item(session, plan.id, PlanItemCreate(test_case_id=case.id), tester)
        await session.commit()
        case_id, plan_id, item_id = case.id, plan.id, item.id
    async with world.factory() as session:
        tester = await session.get(User, world.tester.id)
        executed = await record_test_plan_execution(session, plan_id, item_id, "passed", None, None, tester)
        await session.commit()
        assert executed.executed_case_version == 1
    async with world.factory() as session:
        tester = await session.get(User, world.tester.id)
        await update_managed_test_case(session, case_id, ManagedTestCaseUpdate(
            expected_version=1, steps=[{"step_number": 1, "action": "Refund v2", "expected_result": None}],
        ), tester)
        await session.commit()
    async with world.factory() as session:
        items = await list_plan_items(plan_id=plan_id, db=session, current_user=world.tester, _plan=None)
        case = await session.get(ManagedTestCase, case_id)
        history = await case_history(session, case)
    assert [(i.id, i.executed_case_version, i.case_version) for i in items] == [(item_id, 1, 2)]
    by_version = {h.version: h for h in history}
    assert [e.item_id for e in by_version[1].executions] == [item_id]
    assert by_version[1].executions[0].plan_name == "W28 plan"
    assert by_version[1].executions[0].execution_status == "passed"
    assert by_version[2].executions == []
    assert by_version[1].steps[0].action == "Refund v1"
