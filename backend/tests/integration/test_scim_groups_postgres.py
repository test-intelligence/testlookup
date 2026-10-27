"""W20 ADM-09: SCIM /Groups → project memberships, against a real database.

* POST /Groups with 3 members, then the admin maps the group to (project, QA_ENGINEER): the 3 users become
  members with ``source = scim:<group>``;
* PATCH remove one member → only that user's SCIM membership goes; a member who was ALSO granted access by a
  person keeps the manual row untouched (role and source);
* the highest mapped role wins across two groups; deleting a group removes what only it granted;
* the displayName filter, the 409 on a duplicate name, the 400 on an unknown member.

Requires ``TESTLOOKUP_POSTGRES_TEST_DSN`` and a database at head; skips otherwise.
"""
from __future__ import annotations

import os
import uuid
from types import SimpleNamespace

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


class _Req:
    def __init__(self, body=None):
        self._body = body
        self.base_url = "http://testserver/"
        self.client = SimpleNamespace(host="127.0.0.1")

    async def json(self):
        return self._body


@pytest.fixture
async def world(monkeypatch):
    from app.models.postgres import ProjectMember, Project, SCIMGroup, User, UserRole
    from app.services import scim_group_service as sgs

    async def _no_cache(user_ids):
        return None

    monkeypatch.setattr(sgs, "invalidate", _no_cache)
    engine = create_async_engine(_dsn(), pool_size=1, max_overflow=0)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    marker = "zqx" + uuid.uuid4().hex[:8].replace("a", "q")
    users = [User(id=uuid.uuid4(), email=f"u{i}-{marker}@testlookup.dev", username=f"u{i}-{marker}", full_name=f"U{i}",
                  hashed_password="x", role=UserRole.VIEWER, is_active=True) for i in range(4)]
    admin = User(id=uuid.uuid4(), email=f"a-{marker}@testlookup.dev", username=f"a-{marker}", full_name="A",
                 hashed_password="x", role=UserRole.ADMIN, is_active=True)
    projects = [Project(id=uuid.uuid4(), name=f"Payment {marker}", slug=f"pay-{marker}", is_active=True),
                Project(id=uuid.uuid4(), name=f"Gateway {marker}", slug=f"gw-{marker}", is_active=True)]
    async with factory() as db:
        db.add_all([*users, admin, *projects])
        await db.flush()
        # user 2 was granted Payment by a person, as QA_LEAD
        db.add(ProjectMember(user_id=users[2].id, project_id=projects[0].id, role="QA_LEAD"))
        await db.commit()
    names = [f"payments-qa-{marker}", f"payments-leads-{marker}"]
    try:
        async with factory() as db:
            yield SimpleNamespace(db=db, users=users, admin=admin, projects=projects, names=names, factory=factory)
    finally:
        async with factory() as db:
            await db.execute(delete(SCIMGroup).where(SCIMGroup.display_name.in_(names)))
            from app.models.postgres import ProjectActivityEvent

            await db.execute(delete(ProjectActivityEvent).where(ProjectActivityEvent.project_id.in_([p.id for p in projects])))
            await db.execute(delete(ProjectMember).where(ProjectMember.project_id.in_([p.id for p in projects])))
            await db.execute(delete(Project).where(Project.id.in_([p.id for p in projects])))
            await db.execute(delete(User).where(User.id.in_([u.id for u in [*users, admin]])))
            await db.commit()
        await engine.dispose()


async def _members(w, project_id):
    from app.models.postgres import ProjectMember

    w.db.expire_all()
    rows = (await w.db.execute(select(ProjectMember).where(ProjectMember.project_id == project_id))).scalars().all()
    return {r.user_id: (r.role, r.source) for r in rows}


async def _create(w, name, user_ids):
    from app.routers.scim import scim_create_group

    body = {"displayName": name, "members": [{"value": str(u)} for u in user_ids]}
    return await scim_create_group(_Req(body), scim_token=None, db=w.db)


async def _map(w, group_id, pairs):
    from app.routers.scim_groups_admin import GroupMappingsIn, put_scim_group_mappings

    payload = GroupMappingsIn(mappings=[{"project_id": p, "role": r} for p, r in pairs])
    return await put_scim_group_mappings(uuid.UUID(group_id), payload, _Req(), current_user=w.admin, db=w.db)


async def test_group_members_get_the_mapped_project_and_a_removal_removes_only_that(world):
    from app.routers.scim import scim_patch_group

    u, pay = world.users, world.projects[0]
    g = await _create(world, world.names[0], [u[0].id, u[1].id, u[2].id])
    assert len(g["members"]) == 3 and g["meta"]["version"] == 'W/"1"'
    out = await _map(world, g["id"], [(pay.id, "QA_ENGINEER")])
    assert out["reconciled_users"] == 3
    m = await _members(world, pay.id)
    src = f"scim:{world.names[0]}"
    assert m[u[0].id] == ("QA_ENGINEER", src) and m[u[1].id] == ("QA_ENGINEER", src)
    assert m[u[2].id] == ("QA_LEAD", None), "a person's grant is never changed"
    from app.models.postgres import ProjectActivityEvent

    events = (await world.db.execute(select(ProjectActivityEvent.event_type, ProjectActivityEvent.entity_id)
                                     .where(ProjectActivityEvent.project_id == pay.id))).all()
    assert sorted(e for e, _ in events) == ["member.added", "member.added"], "one ledger row per SCIM-granted membership"

    patch = {"Operations": [{"op": "remove", "path": f'members[value eq "{u[0].id}"]'}]}
    g2 = await scim_patch_group(uuid.UUID(g["id"]), _Req(patch), scim_token=None, db=world.db)
    assert {x["value"] for x in g2["members"]} == {str(u[1].id), str(u[2].id)}
    m = await _members(world, pay.id)
    assert u[0].id not in m and m[u[1].id][0] == "QA_ENGINEER" and m[u[2].id] == ("QA_LEAD", None)

    patch = {"Operations": [{"op": "remove", "path": "members", "value": [{"value": str(u[2].id)}]}]}
    await scim_patch_group(uuid.UUID(g["id"]), _Req(patch), scim_token=None, db=world.db)
    assert (await _members(world, pay.id))[u[2].id] == ("QA_LEAD", None), "removing a group member keeps the manual row"


async def test_the_highest_role_wins_and_deleting_a_group_removes_its_grant(world):
    from app.routers.scim import scim_delete_group

    u, pay, gw = world.users, world.projects[0], world.projects[1]
    qa = await _create(world, world.names[0], [u[3].id])
    leads = await _create(world, world.names[1], [u[3].id])
    await _map(world, qa["id"], [(pay.id, "TESTER"), (gw.id, "QA_ENGINEER")])
    await _map(world, leads["id"], [(pay.id, "QA_LEAD")])
    assert (await _members(world, pay.id))[u[3].id] == ("QA_LEAD", f"scim:{world.names[1]}")
    await scim_delete_group(uuid.UUID(leads["id"]), scim_token=None, db=world.db)
    assert (await _members(world, pay.id))[u[3].id] == ("TESTER", f"scim:{world.names[0]}")
    await _map(world, qa["id"], [])
    assert u[3].id not in await _members(world, pay.id) and u[3].id not in await _members(world, gw.id)


async def test_filter_conflict_and_unknown_member(world):
    from app.routers.scim import scim_list_groups

    await _create(world, world.names[0], [])
    listed = await scim_list_groups(_Req(), scim_token=None, db=world.db, filter=f'displayName eq "{world.names[0].upper()}"',
                                    startIndex=1, count=100)
    assert listed["totalResults"] == 1 and listed["Resources"][0]["displayName"] == world.names[0]
    with pytest.raises(HTTPException) as exc:
        await _create(world, world.names[0], [])
    assert exc.value.status_code == 409
    with pytest.raises(HTTPException) as exc:
        await _create(world, world.names[1], [uuid.uuid4()])
    assert exc.value.status_code == 400
    with pytest.raises(HTTPException) as exc:
        await scim_list_groups(_Req(), scim_token=None, db=world.db, filter='members eq "x"', startIndex=1, count=10)
    assert exc.value.status_code == 400
