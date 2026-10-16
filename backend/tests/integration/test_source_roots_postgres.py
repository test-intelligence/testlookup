"""FAIL-08 against a real database: Java source roots, end to end.

``projects.source_roots`` (migration 0215, JSON) round-trips through the PUT
route with its ``project.updated`` ledger row; the coverage badge's
``coverage_pct`` of an all-Java project goes from ``None`` to a number once a
root is set; the preview reads the same sample and writes nothing; the
ingest-time assignment routes a Java failure to its CODEOWNERS member (and,
without roots, to the QA-lead pool as before); the inbox reason names the rule.

Requires ``TESTLOOKUP_POSTGRES_TEST_DSN`` migrated to head (0215+).
"""
from __future__ import annotations

import os
import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytest.importorskip("asyncpg")
pytest.importorskip("httpx")
pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

JAVA_TRACE = (
    "java.lang.AssertionError: expected:<200> but was:<500>\n"
    "\tat org.junit.Assert.fail(Assert.java:89)\n"
    "\tat com.acme.pay.CheckoutTest.pays(CheckoutTest.java:42)\n"
)
ROOTS = [
    {"prefix": "com.acme.pay", "root": "payments/src/test/java"},
    {"prefix": "", "root": "src/test/java"},
]


def _dsn() -> str:
    value = os.getenv("TESTLOOKUP_POSTGRES_TEST_DSN", "").strip()
    if not value:
        pytest.skip("TESTLOOKUP_POSTGRES_TEST_DSN is not configured")
    return value


@pytest.fixture
async def world():
    from app.models.postgres import (
        LaunchStatus,
        Project,
        ProjectActivityEvent,
        ProjectMember,
        ServiceOwnershipRule,
        TestCase,
        TestRun,
        TestStatus,
        User,
        UserRole,
    )

    engine = create_async_engine(_dsn(), pool_size=3, max_overflow=0)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    tag = uuid.uuid4().hex[:10]
    project_id, lead, alice = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    run_id = uuid.uuid4()

    async with sessions() as db:
        name = f"fail08-{tag}"
        db.add(Project(id=project_id, name=name, slug=name, is_active=True))
        for uid, label, role in ((lead, "lead", UserRole.QA_LEAD), (alice, "alice", UserRole.QA_ENGINEER)):
            db.add(User(
                id=uid, email=f"f08-{label}-{tag}@example.com", username=f"f08{label}{tag}",
                full_name=f"F08 {label}", hashed_password="!unusable", role=role.value,
            ))
        await db.flush()
        db.add(ProjectMember(project_id=project_id, user_id=lead, role=UserRole.QA_LEAD.value))
        db.add(ProjectMember(project_id=project_id, user_id=alice, role=UserRole.QA_ENGINEER.value))
        db.add(ServiceOwnershipRule(
            project_id=project_id, match_type="path", match_pattern="payments/**",
            service_name="CODEOWNERS", team_name=f"@f08alice{tag}", team_contact=f"@f08alice{tag}",
            priority=0, created_by=lead,
        ))
        db.add(TestRun(
            id=run_id, project_id=project_id, build_number=f"f08-{tag}", jenkins_job="fail08",
            status=LaunchStatus.FAILED, ingestion_source="unknown", total_tests=2, failed_tests=2,
        ))
        await db.flush()
        for i in range(2):
            db.add(TestCase(
                id=uuid.uuid4(), test_run_id=run_id, test_name=f"pays_{i}", suite_name="Payments",
                test_fingerprint=f"f08-{tag}-{i}", status=TestStatus.FAILED,
                stack_trace=JAVA_TRACE, error_message="expected:<200> but was:<500>",
            ))
        await db.commit()

    try:
        yield SimpleNamespace(
            sessions=sessions, project_id=project_id, lead=lead, alice=alice, run_id=run_id, tag=tag,
        )
    finally:
        async with sessions() as db:
            await db.execute(delete(ProjectActivityEvent).where(ProjectActivityEvent.project_id == project_id))
            await db.execute(delete(TestCase).where(TestCase.test_run_id == run_id))
            await db.execute(delete(TestRun).where(TestRun.id == run_id))
            await db.execute(delete(ServiceOwnershipRule).where(ServiceOwnershipRule.project_id == project_id))
            await db.execute(delete(ProjectMember).where(ProjectMember.project_id == project_id))
            await db.execute(delete(Project).where(Project.id == project_id))
            await db.execute(delete(User).where(User.id.in_([lead, alice])))
            await db.commit()
        await engine.dispose()


def _client(world, role="QA_LEAD"):
    """The ownership router on the test's own loop (an ASGI transport: the
    engine is bound to this loop, so no TestClient thread)."""
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient

    from app.core.deps import get_current_active_user
    from app.db.postgres import get_db
    from app.routers import ownership

    user = SimpleNamespace(id=world.lead, role=role, is_active=True, username=f"f08lead{world.tag}",
                           full_name="F08 lead", email="lead@example.com")

    async def _get_db():
        async with world.sessions() as session:
            yield session

    app = FastAPI()
    app.include_router(ownership.router)
    app.dependency_overrides[get_db] = _get_db
    app.dependency_overrides[get_current_active_user] = lambda: user
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def test_put_round_trips_the_json_column_and_records_one_ledger_row(world):
    from app.models.postgres import Project, ProjectActivityEvent

    base = f"/api/v1/projects/{world.project_id}/ownership/source-roots"
    async with _client(world) as http:
        assert (await http.get(base)).json() == {"roots": [], "max_roots": 20}
        res = await http.put(base, json={"roots": [
            {"prefix": "com.acme.pay", "root": "payments/src/test/java/"}, {"root": " src/test/java "},
        ]})
        assert res.status_code == 200, res.text
        assert (await http.get(base)).json()["roots"] == ROOTS
        assert (await http.put(base, json={"roots": ROOTS})).status_code == 200   # same list: no row
        bad = await http.put(base, json={"roots": [{"prefix": "", "root": "../etc"}]})
        assert bad.status_code == 422
    async with _client(world, role="TESTER") as tester:
        assert (await tester.put(base, json={"roots": []})).status_code == 403

    async with world.sessions() as db:
        stored = (await db.execute(select(Project.source_roots).where(Project.id == world.project_id))).scalar_one()
        rows = (await db.execute(
            select(ProjectActivityEvent).where(
                ProjectActivityEvent.project_id == world.project_id,
                ProjectActivityEvent.event_type == "project.updated",
            )
        )).scalars().all()
    assert stored == ROOTS
    assert len(rows) == 1
    assert rows[0].context["changed"] == "Java source roots"
    assert rows[0].diff["before"] == {"source_roots": 0} and rows[0].diff["after"] == {"source_roots": 2}

    async with _client(world) as http:
        assert (await http.put(base, json={"roots": []})).status_code == 200
    async with world.sessions() as db:
        assert (await db.execute(select(Project.source_roots).where(Project.id == world.project_id))).scalar_one() is None


async def test_coverage_goes_from_not_measurable_to_a_number_and_preview_writes_nothing(world):
    from app.models.postgres import Project, ProjectActivityEvent
    from app.services import codeowners_service as cs

    async with world.sessions() as db:
        before = await cs.compute_coverage(db, world.project_id)
    assert (before["sampled"], before["located"], before["coverage_pct"]) == (2, 0, None)

    async with world.sessions() as db:
        preview = await cs.preview_source_roots(db, world.project_id, ROOTS)
        assert not db.new and not db.dirty and not db.deleted
    assert (preview["sampled"], preview["java_frames"], preview["located"], preview["matched"]) == (2, 2, 2, 2)
    assert preview["examples"][0]["path"] == "payments/src/test/java/com/acme/pay/CheckoutTest.java"

    async with world.sessions() as db:
        assert (await db.execute(select(Project.source_roots).where(Project.id == world.project_id))).scalar_one() is None
        assert (await db.execute(select(ProjectActivityEvent.id).where(
            ProjectActivityEvent.project_id == world.project_id))).first() is None

        project = (await db.execute(select(Project).where(Project.id == world.project_id))).scalar_one()
        project.source_roots = ROOTS
        await db.commit()

    async with world.sessions() as db:
        after = await cs.compute_coverage(db, world.project_id)
    assert (after["located"], after["matched"], after["coverage_pct"], after["source_roots"]) == (2, 2, 100.0, 2)


async def test_ingest_assigns_the_java_failure_to_its_codeowners_member(world):
    from app.models.postgres import Project, TestCase
    from app.services.codeowners_service import codeowners_reasons_for_rows
    from app.services.failed_test_assignment_service import assign_failed_tests_to_suite_owners

    async with world.sessions() as db:
        counts = await assign_failed_tests_to_suite_owners(db, world.project_id, world.run_id)
        await db.rollback()   # no roots: measured, then undone
    assert counts["path_owner"] == 0 and counts["assigned"] == 2   # the QA-lead pool, as today

    async with world.sessions() as db:
        project = (await db.execute(select(Project).where(Project.id == world.project_id))).scalar_one()
        project.source_roots = ROOTS
        await db.commit()

    async with world.sessions() as db:
        counts = await assign_failed_tests_to_suite_owners(db, world.project_id, world.run_id)
        await db.commit()
    assert counts["path_owner"] == 2 and counts["assigned"] == 2

    async with world.sessions() as db:
        rows = (await db.execute(
            select(TestCase.id, TestCase.assigned_to_user_id, TestCase.stack_trace, TestCase.error_message)
            .where(TestCase.test_run_id == world.run_id)
        )).all()
        assert {r.assigned_to_user_id for r in rows} == {world.alice}
        inbox_rows = [SimpleNamespace(id=r.id, project_id=world.project_id, assigned_to_user_id=r.assigned_to_user_id,
                                      stack_trace=r.stack_trace, error_message=r.error_message) for r in rows]
        reasons = await codeowners_reasons_for_rows(db, inbox_rows)
    assert set(reasons.values()) == {"via CODEOWNERS: payments/**"}
