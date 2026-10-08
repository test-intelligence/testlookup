"""The New Test Case form's Assignee, and the user list that fills it.

The UX redesign's browser E2E pass (2026-10-08) found:

* the form's Assignee was dropped silently: ``ManagedTestCaseCreate`` had no
  ``assignee_id``, and nothing else in the backend ever wrote the column;
* ``GET /api/v1/auth/users``, which filled that picker, returned every active
  user, emails included, to anyone signed in, members of other tenants'
  projects too.

An assignee is now an active member of the case's project (or an admin), and
the list holds the caller's co-members (an admin's holds everyone).

Same fixture pattern as ``test_viz_seed_postgres.py``: requires
``TESTLOOKUP_POSTGRES_TEST_DSN`` and a database migrated to head, skips
otherwise. Nothing is committed: the session is rolled back.

Runs in CI as part of the ``postgres-integration`` job's file list in
``.github/workflows/ci.yml``.
"""

from __future__ import annotations

import os
import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytest.importorskip("asyncpg")

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def _dsn() -> str:
    value = os.getenv("TESTLOOKUP_POSTGRES_TEST_DSN", "").strip()
    if not value:
        pytest.skip("TESTLOOKUP_POSTGRES_TEST_DSN is not configured")
    return value


@pytest.fixture
async def session():
    engine = create_async_engine(_dsn(), pool_size=1, max_overflow=0)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        try:
            yield db
        finally:
            await db.rollback()
    await engine.dispose()


@pytest.fixture
async def world(session):
    """Two projects. Lead and Eng work on A; Outsider on B; Admin on neither."""
    from app.models.postgres import Project, ProjectMember, User, UserRole

    token = uuid.uuid4().hex[:10]

    def user(name: str, role: UserRole, active: bool = True) -> User:
        return User(
            id=uuid.uuid4(),
            email=f"{name}-{token}@example.test",
            username=f"{name}-{token}",
            full_name=f"{name.title()} {token}",
            hashed_password="x",
            role=role,
            is_active=active,
        )

    lead = user("lead", UserRole.QA_LEAD)
    eng = user("eng", UserRole.QA_ENGINEER)
    gone = user("gone", UserRole.QA_ENGINEER, active=False)
    outsider = user("outsider", UserRole.QA_ENGINEER)
    admin = user("admin", UserRole.ADMIN)
    a = Project(id=uuid.uuid4(), name=f"A {token}", slug=f"a-{token}")
    b = Project(id=uuid.uuid4(), name=f"B {token}", slug=f"b-{token}")
    session.add_all([lead, eng, gone, outsider, admin, a, b])
    await session.flush()
    session.add_all([
        ProjectMember(id=uuid.uuid4(), project_id=a.id, user_id=lead.id, role=UserRole.QA_LEAD.value),
        ProjectMember(id=uuid.uuid4(), project_id=a.id, user_id=eng.id, role=UserRole.QA_ENGINEER.value),
        ProjectMember(id=uuid.uuid4(), project_id=a.id, user_id=gone.id, role=UserRole.QA_ENGINEER.value),
        ProjectMember(id=uuid.uuid4(), project_id=b.id, user_id=outsider.id, role=UserRole.QA_ENGINEER.value),
    ])
    await session.flush()
    return {"lead": lead, "eng": eng, "gone": gone, "outsider": outsider, "admin": admin, "a": a, "b": b}


async def _listed(session, caller) -> set:
    from app.routers.auth import list_users

    return {u.id for u in await list_users(limit=500, db=session, current_user=caller)}


async def test_a_member_lists_their_co_members_and_no_one_else(session, world) -> None:
    listed = await _listed(session, world["eng"])
    assert world["lead"].id in listed and world["eng"].id in listed
    assert world["outsider"].id not in listed  # another project's member
    assert world["admin"].id not in listed  # shares no project
    assert world["gone"].id not in listed  # inactive


async def test_someone_on_no_project_lists_only_themselves(session, world) -> None:
    from app.models.postgres import User, UserRole

    loner = User(
        id=uuid.uuid4(), email=f"loner-{uuid.uuid4().hex[:8]}@example.test",
        username=f"loner-{uuid.uuid4().hex[:8]}", hashed_password="x",
        role=UserRole.VIEWER, is_active=True,
    )
    session.add(loner)
    await session.flush()
    assert await _listed(session, loner) == {loner.id}


async def test_an_admin_lists_everyone_active(session, world) -> None:
    listed = await _listed(session, world["admin"])
    for name in ("lead", "eng", "outsider", "admin"):
        assert world[name].id in listed, name
    assert world["gone"].id not in listed


async def _create(session, world, assignee):
    from app.models.schemas import ManagedTestCaseCreate
    from app.services.test_management_service import create_managed_test_case

    payload = ManagedTestCaseCreate(
        project_id=world["a"].id,
        title=f"assignee check {uuid.uuid4().hex[:6]}",
        assignee_id=assignee.id if assignee is not None else None,
    )
    return await create_managed_test_case(session, payload, world["lead"])


async def test_the_create_form_assignee_is_saved(session, world) -> None:
    case = await _create(session, world, world["eng"])
    assert case.assignee_id == world["eng"].id
    assert case.author_id == world["lead"].id


async def test_an_admin_on_no_project_may_be_assigned(session, world) -> None:
    case = await _create(session, world, world["admin"])
    assert case.assignee_id == world["admin"].id


@pytest.mark.parametrize("who", ["outsider", "gone"])
async def test_a_non_member_or_inactive_assignee_is_refused(session, world, who) -> None:
    with pytest.raises(HTTPException) as refused:
        await _create(session, world, world[who])
    assert refused.value.status_code == 422
    assert refused.value.detail == "The assignee must be an active member of this project."


async def test_no_assignee_stays_unassigned(session, world) -> None:
    case = await _create(session, world, None)
    assert case.assignee_id is None
