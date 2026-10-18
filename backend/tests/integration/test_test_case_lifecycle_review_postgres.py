"""W28 TCM-01 / TCM-02 / TCM-05 on real PostgreSQL (committed rows).

* review OFF: a tester moves a Draft case to Active and back to Draft, each
  move committed with its version;
* review ON: a QA lead approves a case they wrote straight from Review
  requested, with a reason: ONE version for the approval, and the pending
  review row decided by them;
* a project row inserted without naming the setting reads review off, and
  the two policy reads agree with the stored value;
* the edit path commits: an Active case keeps its status, the version names
  its source and owner, the "edited after approval" marker is set with review
  on, and the audit row (a JSON column) stores the owner and suite ids;
* a recorded plan execution stores the case version it ran against.

Self-owned, project-scoped fixtures, removed afterwards (the
``test_test_case_lifecycle_postgres.py`` pattern). Requires
``TESTLOOKUP_POSTGRES_TEST_DSN`` and a database migrated to head.
"""
from __future__ import annotations

import os
import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import delete, func, select, text, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.postgres import (
    ManagedTestCase,
    Project,
    ProjectMember,
    TestCaseAuditLog as AuditLogModel,
    TestCaseReview as ReviewModel,
    TestCaseVersion as VersionModel,
    TestPlan as PlanModel,
    TestPlanItem as PlanItemModel,
    TestSuite as SuiteModel,
    User,
    UserRole,
)
from app.models.schemas import ManagedTestCaseUpdate
from app.services.test_case_lifecycle_service import transition
from app.services.test_case_review_policy import (
    project_requires_case_review,
    review_required_by_project,
)
from app.services.test_management_service import (
    record_test_plan_execution,
    update_managed_test_case,
)

pytest.importorskip("asyncpg")
pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def _dsn() -> str:
    value = os.getenv("TESTLOOKUP_POSTGRES_TEST_DSN", "").strip()
    if not value:
        pytest.skip("TESTLOOKUP_POSTGRES_TEST_DSN is not configured")
    return value


@pytest.fixture
async def pg_engine():
    engine = create_async_engine(_dsn(), pool_size=2, max_overflow=0)
    try:
        yield engine
    finally:
        await engine.dispose()


def _person(role: UserRole, label: str, token: str) -> User:
    return User(
        id=uuid.uuid4(),
        email=f"w28-{label}-{token}@example.invalid",
        username=f"w28-{label}-{token}",
        full_name=f"W28 {label.title()}",
        hashed_password="not-a-real-password",
        role=role,
    )


@pytest.fixture
async def world(pg_engine):
    token = uuid.uuid4().hex[:12]
    tester = _person(UserRole.TESTER, "tester", token)
    lead = _person(UserRole.QA_LEAD, "lead", token)
    owner = _person(UserRole.TESTER, "owner", token)
    project = Project(id=uuid.uuid4(), name=f"W28 review {token}", slug=f"w28-review-{token}")
    factory = async_sessionmaker(pg_engine, expire_on_commit=False)
    async with factory() as session:
        session.add_all([tester, lead, owner, project])
        await session.flush()
        session.add(ProjectMember(id=uuid.uuid4(), user_id=owner.id, project_id=project.id, role="TESTER"))
        await session.commit()
    try:
        yield SimpleNamespace(
            factory=factory, tester=tester, lead=lead, owner=owner, project=project, token=token
        )
    finally:
        async with factory() as session:
            case_ids = select(ManagedTestCase.id).where(ManagedTestCase.project_id == project.id)
            await session.execute(delete(AuditLogModel).where(AuditLogModel.project_id == project.id))
            await session.execute(delete(ReviewModel).where(ReviewModel.test_case_id.in_(case_ids)))
            await session.execute(delete(VersionModel).where(VersionModel.test_case_id.in_(case_ids)))
            await session.execute(delete(PlanModel).where(PlanModel.project_id == project.id))
            await session.execute(delete(ManagedTestCase).where(ManagedTestCase.project_id == project.id))
            await session.execute(delete(SuiteModel).where(SuiteModel.project_id == project.id))
            await session.execute(delete(Project).where(Project.id == project.id))
            await session.execute(delete(User).where(User.id.in_([tester.id, lead.id, owner.id])))
            await session.commit()


async def _case(session, world, *, status="draft", author=None, version=1) -> ManagedTestCase:
    case = ManagedTestCase(
        id=uuid.uuid4(),
        project_id=world.project.id,
        title="Pay with a card",
        status=status,
        version=version,
        author_id=(author or world.tester).id,
        steps=[{"step_number": 1, "action": "Open checkout", "expected_result": "The cart shows"}],
    )
    session.add(case)
    await session.commit()
    return case


async def _versions(session, case_id) -> list[VersionModel]:
    return list(
        (
            await session.execute(
                select(VersionModel).where(VersionModel.test_case_id == case_id).order_by(VersionModel.version)
            )
        ).scalars()
    )


async def _status(session, case_id) -> str:
    return (
        await session.execute(select(ManagedTestCase.status).where(ManagedTestCase.id == case_id))
    ).scalar_one()


async def test_review_off_a_tester_moves_draft_to_active_and_back(world):
    async with world.factory() as session:
        case = await _case(session, world)
        await transition(session, case.id, "activate", world.tester, expected_version=1, review_required=False)
        await session.commit()
        assert await _status(session, case.id) == "active"
        await transition(session, case.id, "revise", world.tester, expected_version=2, review_required=False)
        await session.commit()
        assert await _status(session, case.id) == "draft"

        versions = await _versions(session, case.id)
        assert [(v.version, v.change_type, v.status, v.source) for v in versions] == [
            (2, "activate", "active", "person"),
            (3, "revise", "draft", "person"),
        ]
        audits = (
            await session.execute(
                select(AuditLogModel.policy_snapshot).where(AuditLogModel.entity_id == case.id)
            )
        ).scalars().all()
        assert [snapshot["review_required"] for snapshot in audits] == [False, False]


async def test_review_on_a_lead_self_approves_from_review_requested(world):
    async with world.factory() as session:
        case = await _case(session, world, author=world.lead)
        await transition(session, case.id, "request_review", world.lead, review_required=True)
        await session.commit()
        await transition(
            session, case.id, "approve", world.lead,
            reason="Walked through it with the team", expected_version=2, review_required=True,
        )
        await session.commit()

        assert await _status(session, case.id) == "approved"
        versions = await _versions(session, case.id)
        assert [(v.version, v.change_type) for v in versions] == [(2, "request_review"), (3, "approve")]
        (review,) = (
            await session.execute(select(ReviewModel).where(ReviewModel.test_case_id == case.id))
        ).scalars().all()
        assert review.status == "approved"
        assert review.reviewer_id == world.lead.id
        assert review.human_notes == "Walked through it with the team"
        assert review.reviewed_at is not None
        snapshot = (
            await session.execute(
                select(AuditLogModel.policy_snapshot).where(
                    AuditLogModel.entity_id == case.id, AuditLogModel.action == "approve"
                )
            )
        ).scalar_one()
        assert snapshot["self_approval"] is True and snapshot["review_required"] is True


async def test_a_project_without_the_setting_reads_review_off(world, pg_engine):
    async with world.factory() as session:
        other = uuid.uuid4()
        await session.execute(
            text("INSERT INTO projects (id, name, slug) VALUES (:id, :n, :s)"),
            {"id": other, "n": f"W28 bare {world.token}", "s": f"w28-bare-{world.token}"},
        )
        await session.commit()
        try:
            assert await project_requires_case_review(session, other) is False
            assert await project_requires_case_review(session, world.project.id) is False
            await session.execute(
                update(Project).where(Project.id == world.project.id).values(require_case_review=True)
            )
            await session.commit()
            assert await project_requires_case_review(session, world.project.id) is True
            assert await review_required_by_project(session, {world.project.id, other}) == {
                world.project.id: True,
                other: False,
            }
            assert await review_required_by_project(session, set()) == {}
        finally:
            await session.execute(delete(Project).where(Project.id == other))
            await session.commit()


async def test_an_edit_commits_without_a_status_change_and_with_its_marker(world):
    async with world.factory() as session:
        await session.execute(
            update(Project).where(Project.id == world.project.id).values(require_case_review=True)
        )
        await session.commit()
        case = await _case(session, world, status="active")

        await update_managed_test_case(
            session,
            case.id,
            ManagedTestCaseUpdate(
                expected_version=1,
                steps=[
                    {"step_number": 1, "action": "Open checkout", "expected_result": "The cart shows"},
                    {"step_number": 2, "action": "Pay", "expected_result": "Paid"},
                ],
                assignee_id=world.owner.id,
                suite_name=f"Checkout {world.token}",
            ),
            world.tester,
        )
        await session.commit()

        row = (
            await session.execute(select(ManagedTestCase).where(ManagedTestCase.id == case.id))
        ).scalar_one()
        assert row.status == "active" and row.version == 2
        assert row.edited_since_approval_version == 2
        assert row.assignee_id == world.owner.id and row.test_suite_id is not None
        (version,) = await _versions(session, case.id)
        assert (version.change_type, version.source, version.assignee_id) == ("updated", "person", world.owner.id)
        audit = (
            await session.execute(
                select(AuditLogModel.new_values).where(
                    AuditLogModel.entity_id == case.id, AuditLogModel.action == "updated"
                )
            )
        ).scalar_one()
        assert audit["assignee_id"] == str(world.owner.id)
        assert audit["test_suite_id"] == str(row.test_suite_id)


async def test_a_recorded_execution_stores_the_case_version(world):
    async with world.factory() as session:
        case = await _case(session, world, status="active", version=3)
        plan = PlanModel(id=uuid.uuid4(), project_id=world.project.id, name=f"W28 plan {world.token}")
        session.add(plan)
        await session.flush()
        item = PlanItemModel(id=uuid.uuid4(), plan_id=plan.id, test_case_id=case.id)
        session.add(item)
        await session.commit()

        await record_test_plan_execution(session, plan.id, item.id, "passed", None, None, world.tester)
        await session.commit()

        stored = (
            await session.execute(
                select(PlanItemModel.executed_case_version, func.count()).where(
                    PlanItemModel.id == item.id
                ).group_by(PlanItemModel.executed_case_version)
            )
        ).one()
        assert stored == (3, 1)
