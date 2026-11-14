"""A test plan's counts include the change that triggered the recount (real PostgreSQL).

The app's sessions do not autoflush, and ``recompute_plan_counts`` counts the
plan's items with a SQL aggregate. Without a flush first the aggregate read the
rows as they were before the caller's change: recording a passed execution left
the plan at executed 0 / passed 0, and a removed item was still counted. The
release journey TL-TCM-001 caught it (``(1, 0, 0)`` for ``(1, 1, 1)``) on
2026-11-11 and again on the v0.1.0 candidate.

Self-owned, project-scoped fixtures in the configured database; the module
skips only when the explicit opt-in DSN is absent.
"""
from __future__ import annotations

import os
import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.postgres import (
    Project,
    ProjectMember,
    TestCaseAuditLog as AuditRow,
    TestPlan as PlanRow,
    User,
    UserRole,
)
from app.models.schemas import ManagedTestCaseCreate, TestPlanItemCreate
from app.services.test_management_service import (
    add_test_plan_item,
    create_managed_test_case,
    record_test_plan_execution,
    remove_test_plan_item,
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
        from app.db.postgres import dispose_engine_for_loop

        await dispose_engine_for_loop()


@pytest.fixture
async def world(pg_engine):
    token = uuid.uuid4().hex[:12]
    tester = User(
        id=uuid.uuid4(),
        email=f"plan-counts-{token}@example.invalid",
        username=f"plan-counts-{token}",
        full_name="Plan Counts Tester",
        hashed_password="not-a-real-password",
        role=UserRole.TESTER,
        is_active=True,
    )
    project = Project(id=uuid.uuid4(), name=f"Plan counts {token}", slug=f"plan-counts-{token}")
    # The app's own session settings (app.db.postgres.get_session_factory): with
    # SQLAlchemy's default autoflush=True the bug does not reproduce.
    factory = async_sessionmaker(pg_engine, expire_on_commit=False, autoflush=False)
    async with factory() as session:
        session.add_all([tester, project])
        await session.flush()
        session.add(ProjectMember(user_id=tester.id, project_id=project.id, role=UserRole.TESTER.value))
        await session.commit()
    try:
        yield SimpleNamespace(factory=factory, tester=tester, project=project)
    finally:
        async with factory() as session:
            await session.execute(delete(AuditRow).where(AuditRow.project_id == project.id))
            await session.execute(delete(Project).where(Project.id == project.id))
            await session.execute(delete(User).where(User.id == tester.id))
            await session.commit()


async def _plan_with_items(world, titles):
    async with world.factory() as session:
        tester = await session.get(User, world.tester.id)
        plan = PlanRow(project_id=world.project.id, name="Plan counts", created_by_id=tester.id)
        session.add(plan)
        await session.flush()
        items = []
        for title in titles:
            case = await create_managed_test_case(
                session, ManagedTestCaseCreate(project_id=world.project.id, title=title), tester
            )
            items.append(await add_test_plan_item(session, plan.id, TestPlanItemCreate(test_case_id=case.id), tester))
        await session.commit()
        return plan.id, [item.id for item in items]


async def _stored_counts(world, plan_id):
    async with world.factory() as session:
        plan = await session.get(PlanRow, plan_id)
        return (plan.total_cases, plan.executed_cases, plan.passed_cases, plan.failed_cases, plan.blocked_cases)


async def test_recording_executions_counts_each_one_as_it_is_recorded(world):
    plan_id, (first, second) = await _plan_with_items(world, ["Refund a payment", "Void a payment"])
    assert await _stored_counts(world, plan_id) == (2, 0, 0, 0, 0)

    async with world.factory() as session:
        tester = await session.get(User, world.tester.id)
        await record_test_plan_execution(session, plan_id, first, "passed", None, None, tester)
        await session.commit()
    assert await _stored_counts(world, plan_id) == (2, 1, 1, 0, 0)

    async with world.factory() as session:
        tester = await session.get(User, world.tester.id)
        await record_test_plan_execution(session, plan_id, second, "failed", "declined", None, tester)
        await session.commit()
    assert await _stored_counts(world, plan_id) == (2, 2, 1, 1, 0)


async def test_a_removed_item_leaves_the_counts(world):
    plan_id, (first, second) = await _plan_with_items(world, ["Refund a payment", "Void a payment"])
    async with world.factory() as session:
        tester = await session.get(User, world.tester.id)
        await record_test_plan_execution(session, plan_id, first, "blocked", None, None, tester)
        await session.commit()
    assert await _stored_counts(world, plan_id) == (2, 1, 0, 0, 1)

    async with world.factory() as session:
        tester = await session.get(User, world.tester.id)
        await remove_test_plan_item(session, plan_id, first, tester)
        await session.commit()
    assert await _stored_counts(world, plan_id) == (1, 0, 0, 0, 0)
