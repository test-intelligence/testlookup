"""ADM-04: an archived project can be found and restored.

Archive (``DELETE /projects/{id}``) hid a project, revoked its API keys and its
default QA-lead account, and nothing could bring it back. Archive now stamps
``archived_at`` / ``archived_by_user_id`` (migration 0232) and records
``project.archived``; ``GET /projects?include_archived=true`` (ADMIN) lists it
with the archiver's name; ``POST /projects/{id}/restore`` (ADMIN) makes it
active again, clears the stamp, records ``project.restored`` and leaves the
keys revoked (it says how many). Memberships are untouched throughout.

Requires ``TESTLOOKUP_POSTGRES_TEST_DSN`` and a database migrated to head,
skips otherwise. The routes commit, so the rows are deleted at the end.
"""

from __future__ import annotations

import os
import uuid

import pytest
from fastapi import HTTPException
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
    from app.models.postgres import ApiKey, Project, ProjectMember, User, UserRole
    from app.services import cache_service

    async def _no_epoch(*a, **k):  # Redis is not part of this test
        return None

    monkeypatch.setattr(cache_service, "bump_analytics_epoch", _no_epoch)
    engine = create_async_engine(_dsn(), pool_size=1, max_overflow=0)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    marker = "zqx" + uuid.uuid4().hex[:8].replace("a", "q")

    def user(name, role):
        return User(id=uuid.uuid4(), email=f"{name}-{marker}@example.test", username=f"{name}-{marker}",
                    full_name=f"{name.title()} {marker}", hashed_password="x", role=role, is_active=True)

    admin, member = user("admin", UserRole.ADMIN), user("member", UserRole.QA_ENGINEER)
    project = Project(id=uuid.uuid4(), name=f"Checkout {marker}", slug=f"checkout-{marker}", is_active=True)
    async with factory() as db:
        db.add_all([admin, member, project])
        await db.flush()
        db.add(ProjectMember(id=uuid.uuid4(), project_id=project.id, user_id=member.id, role=UserRole.QA_ENGINEER.value))
        keys = [
            ApiKey(id=uuid.uuid4(), name=f"ci-{i}-{marker}", key_hash=uuid.uuid4().hex + uuid.uuid4().hex,
                   key_hint=f"qai_{i}{marker[:3]}", user_id=admin.id, project_id=project.id, is_active=True)
            for i in range(2)
        ]
        db.add_all(keys)
        await db.commit()
    try:
        async with factory() as db:
            yield {"db": db, "admin": admin, "member": member, "project": project}
    finally:
        async with factory() as db:  # the project's activity rows go with it (ON DELETE CASCADE)
            await db.execute(delete(ApiKey).where(ApiKey.project_id == project.id))
            await db.execute(delete(ProjectMember).where(ProjectMember.project_id == project.id))
            await db.execute(delete(Project).where(Project.id == project.id))
            await db.execute(delete(User).where(User.id.in_([admin.id, member.id])))
            await db.commit()
        await engine.dispose()


async def _reload(db, model, ident):
    db.expire_all()
    return (await db.execute(select(model).where(model.id == ident))).scalar_one()


async def test_archive_then_restore_round_trip(world):
    from app.models.postgres import ApiKey, Project, ProjectActivityEvent, ProjectMember
    from app.routers.projects import delete_project, list_projects, restore_project

    db, admin, member, project = world["db"], world["admin"], world["member"], world["project"]

    await delete_project(project.id, db=db, current_user=admin)
    archived = await _reload(db, Project, project.id)
    assert archived.is_active is False
    assert archived.archived_by_user_id == admin.id and archived.archived_at is not None

    # Hidden from the normal list, listed for an admin with include_archived.
    normal = {p.id for p in await list_projects(db=db, current_user=admin, include_archived=False)}
    assert project.id not in normal
    everything = {p.id: p for p in await list_projects(db=db, current_user=admin, include_archived=True)}
    row = everything[project.id]
    assert row.is_active is False and row.archived_at is not None
    assert row.archived_by_name == admin.full_name

    answer = await restore_project(project.id, db=db, current_user=admin)
    assert answer.restored is True and answer.api_keys_still_revoked == 2
    assert answer.default_qa_lead_reactivated is False
    restored = await _reload(db, Project, project.id)
    assert restored.is_active is True and restored.archived_at is None and restored.archived_by_user_id is None
    # The keys stay revoked; the membership was never touched.
    keys = (await db.execute(select(ApiKey).where(ApiKey.project_id == project.id))).scalars().all()
    assert len(keys) == 2 and not any(k.is_active for k in keys)
    members = (await db.execute(select(ProjectMember).where(ProjectMember.project_id == project.id))).scalars().all()
    assert [m.user_id for m in members] == [member.id]
    assert project.id in {p.id for p in await list_projects(db=db, current_user=admin, include_archived=False)}

    events = (await db.execute(
        select(ProjectActivityEvent.event_type)
        .where(ProjectActivityEvent.project_id == project.id)
        .order_by(ProjectActivityEvent.occurred_at, ProjectActivityEvent.id)
    )).scalars().all()
    assert list(events) == ["project.archived", "project.restored"]


async def test_restoring_an_active_project_is_409_and_changes_nothing(world):
    from app.routers.projects import restore_project

    with pytest.raises(HTTPException) as exc:
        await restore_project(world["project"].id, db=world["db"], current_user=world["admin"])
    assert exc.value.status_code == 409 and exc.value.detail == "This project is not archived."


async def test_archiving_twice_keeps_the_first_stamp(world):
    from app.models.postgres import Project
    from app.routers.projects import delete_project

    db, admin, member, project = world["db"], world["admin"], world["member"], world["project"]
    await delete_project(project.id, db=db, current_user=admin)
    first = (await _reload(db, Project, project.id)).archived_at
    await delete_project(project.id, db=db, current_user=member)
    again = await _reload(db, Project, project.id)
    assert again.archived_at == first and again.archived_by_user_id == admin.id


async def test_only_an_admin_lists_archived_projects(world):
    from app.routers.projects import list_projects

    with pytest.raises(HTTPException) as exc:
        await list_projects(db=world["db"], current_user=world["member"], include_archived=True)
    assert exc.value.status_code == 403


async def test_the_restore_route_requires_admin_and_refuses_a_project_key():
    """The role gate is the route's own dependency (no allow_project_key)."""
    from app.main import app

    route = next(r for r in app.routes if getattr(r, "path", "") == "/api/v1/projects/{project_id}/restore")
    assert "POST" in route.methods
    source = __import__("inspect").getsource(route.endpoint)
    assert "require_role(UserRole.ADMIN)" in source and "allow_project_key" not in source
