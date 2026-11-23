"""All Projects lists show a non-admin the rows of the projects they belong to.

S-D-4 and D-134. ``GET /test-management/plans``, ``GET /test-management/strategies``
and ``GET /release-gate-policies`` answered an empty list to every non-admin who
named no project. A QA lead with All Projects selected read "No test plans found
across all projects", and the Gate policies page read "No policies configured"
while a policy was active. The fix filters by the caller's memberships instead
of returning nothing; naming a foreign project is still refused (F-042, pinned
in ``test_project_scope_guard_placement.py``).

These run the real router handlers on a real (SQLite) database, because what
matters is what the SQL returns: a member of A gets A's rows (and, for policies,
the system default), never B's; an admin gets everything.
"""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytest.importorskip("aiosqlite")

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine  # noqa: E402
from sqlalchemy.schema import CreateTable  # noqa: E402

from app.models import postgres as m  # noqa: E402

pytestmark = pytest.mark.regression


@pytest.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        for table in (m.Project.__table__, m.TestPlan.__table__, m.TestStrategy.__table__, m.ReleaseGatePolicy.__table__):
            # CreateTable without the indexes: the policies' partial unique
            # indexes (one active policy per project) lose their WHERE on SQLite.
            await conn.execute(CreateTable(table))
    async with AsyncSession(engine, expire_on_commit=False) as session:
        yield session
    await engine.dispose()


@pytest.fixture
async def world(db):
    ids = {}
    for side in ("a", "b"):
        pid = uuid.uuid4()
        ids[side] = pid
        db.add(m.Project(id=pid, name=f"project-{side}", slug=f"project-{side}-{pid.hex[:6]}", is_active=True))
        await db.flush()
        db.add(m.TestPlan(project_id=pid, name=f"plan-{side}", status="draft"))
        db.add(m.TestStrategy(project_id=pid, name=f"strategy-{side}", version_label="v1.0", status="draft"))
        db.add(m.ReleaseGatePolicy(
            project_id=pid, version=1, name=f"policy-{side}", rules={}, is_active=True, is_draft=False, created_by=uuid.uuid4(),
        ))
    db.add(m.ReleaseGatePolicy(
        project_id=None, version=1, name="policy-system", rules={}, is_active=True, is_draft=False, created_by=uuid.uuid4(),
    ))
    await db.commit()
    return ids


def _user(role: str):
    user = MagicMock()
    user.id = uuid.uuid4()
    user.role = role
    user.username = f"zz_{role.lower()}"
    user.api_key_project_id = None
    return user


async def _plans(db, user):
    from app.routers import test_management_plans as mod

    body = await mod.list_plans(project_id=None, status=None, page=1, size=20, db=db, current_user=user)
    return {item.name for item in body["items"]}, body["total"]


async def _strategies(db, user):
    from app.routers import test_management_strategies as mod

    return {item.name for item in await mod.list_strategies(project_id=None, db=db, current_user=user)}


async def _policies(db, user):
    from app.routers import release_gate_policies as mod

    rows = await mod.list_policies(project_id=None, is_active=None, current_user=user, db=db)
    return {item.name for item in rows}


async def test_a_member_of_a_reads_a_s_rows_and_never_b_s(db, world):
    user = _user("QA_LEAD")
    with patch("app.core.deps.get_accessible_project_ids", AsyncMock(return_value={world["a"]})):
        plans, total = await _plans(db, user)
        strategies = await _strategies(db, user)
        policies = await _policies(db, user)

    assert plans == {"plan-a"} and total == 1
    assert strategies == {"strategy-a"}
    # The system default governs every project without its own policy, and a
    # single read of it is open to anyone signed in, so the list shows it too.
    assert policies == {"policy-a", "policy-system"}


async def test_no_memberships_reads_nothing_but_the_system_default(db, world):
    user = _user("VIEWER")
    with patch("app.core.deps.get_accessible_project_ids", AsyncMock(return_value=set())):
        plans, total = await _plans(db, user)
        strategies = await _strategies(db, user)
        policies = await _policies(db, user)

    assert plans == set() and total == 0
    assert strategies == set()
    assert policies == {"policy-system"}


async def test_an_admin_reads_every_project(db, world):
    user = _user("ADMIN")
    with patch("app.core.deps.get_accessible_project_ids", AsyncMock(return_value=None)):
        plans, _ = await _plans(db, user)
        strategies = await _strategies(db, user)
        policies = await _policies(db, user)

    assert plans == {"plan-a", "plan-b"}
    assert strategies == {"strategy-a", "strategy-b"}
    assert policies == {"policy-a", "policy-b", "policy-system"}
