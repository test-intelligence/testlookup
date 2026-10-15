"""W05 REL-04 on real PostgreSQL: ``GET /api/v1/compliance-packs``, the rail's pack list.

* Scope: a non-admin naming a project they are not in gets 403; naming none
  gets only their own projects' packs (another tenant's pack never appears);
  an ADMIN sees every ACTIVE project; a soft-deleted project's packs are
  excluded even for a member of it.
* ``total`` counts beyond ``limit`` (12 packs, ``limit=10`` -> 10 items, total 12).
* An item carries no ``minio_key`` / ``metadata_snapshot``; ``retired`` is
  ``retention_expires_at <= now()``; a pack whose release was deleted reads
  ``release_name: null`` (FK ``SET NULL``).

The handler is called directly with a real session, so ``resolve_project_scope``
and ``get_accessible_project_ids`` run against real ``project_members`` rows.
The HTTP-level checks (422 for ``limit``, 400 for a malformed id) live in
``tests/integration/test_compliance_packs_api.py``.

Requires ``TESTLOOKUP_POSTGRES_TEST_DSN``; skipped without it.
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import text

pytest.importorskip("asyncpg")

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def _dsn() -> str:
    value = os.getenv("TESTLOOKUP_POSTGRES_TEST_DSN", "").strip()
    if not value:
        pytest.skip("TESTLOOKUP_POSTGRES_TEST_DSN is not configured")
    return value


@pytest.fixture
async def world():
    import app.db.postgres as app_postgres
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

    from app.models.postgres import CompliancePack, Project, ProjectMember, Release, User, UserRole

    await app_postgres.dispose_engine_for_loop()
    engine = create_async_engine(_dsn(), pool_size=2, max_overflow=0)
    tag = uuid.uuid4().hex[:10]
    mine, foreign, deleted = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    member_id = uuid.uuid4()
    # In the future so these are the newest packs in the database, whatever
    # else it holds: the ADMIN read is "every active project, newest first".
    base = datetime.now(timezone.utc) + timedelta(days=30)
    async with AsyncSession(engine, expire_on_commit=False) as db:
        db.add_all([
            Project(id=mine, name=f"cp-{tag}", slug=f"cp-{tag}", is_active=True),
            Project(id=foreign, name=f"cp-f-{tag}", slug=f"cp-f-{tag}", is_active=True),
            Project(id=deleted, name=f"cp-d-{tag}", slug=f"cp-d-{tag}", is_active=False),
            User(id=member_id, username=f"cp-{tag}", email=f"cp-{tag}@example.test",
                 hashed_password="x", role=UserRole.QA_ENGINEER, is_active=True),
        ])
        await db.flush()
        db.add_all([
            ProjectMember(project_id=mine, user_id=member_id, role=UserRole.QA_ENGINEER),
            ProjectMember(project_id=deleted, user_id=member_id, role=UserRole.QA_ENGINEER),
        ])
        kept = Release(id=uuid.uuid4(), project_id=mine, name=f"2026.11-{tag}", version="2026.11.0",
                       status="released")
        doomed = Release(id=uuid.uuid4(), project_id=mine, name=f"gone-{tag}", status="released")
        db.add_all([kept, doomed])
        await db.flush()

        def _pack(project_id, release_id, minutes, *, expires=None, sha="ab12cd34" + "0" * 56):
            return CompliancePack(
                id=uuid.uuid4(), project_id=project_id, release_id=release_id,
                minio_key=f"compliance/{tag}/{uuid.uuid4().hex}.zip", manifest_sha256=sha,
                file_count=11, bytes=422_195, generated_at=base + timedelta(minutes=minutes),
                retention_expires_at=expires or (base + timedelta(days=2557)),
                metadata_snapshot={"secret": "do not ship"}, notes="internal",
            )

        packs = [_pack(mine, kept.id, i) for i in range(10)]
        packs.append(_pack(mine, doomed.id, 20))                                    # release deleted below
        packs.append(_pack(mine, kept.id, 30, expires=datetime.now(timezone.utc) - timedelta(days=1)))
        packs.append(_pack(foreign, None, 40))
        packs.append(_pack(deleted, None, 50))
        db.add_all(packs)
        await db.commit()
        await db.execute(text("DELETE FROM releases WHERE id = :r"), {"r": doomed.id})
        await db.commit()
    member = SimpleNamespace(id=member_id, role="QA_ENGINEER", username=f"cp-{tag}", is_active=True)
    admin = SimpleNamespace(id=uuid.uuid4(), role="ADMIN", username="admin", is_active=True)
    try:
        yield SimpleNamespace(engine=engine, mine=mine, foreign=foreign, deleted=deleted,
                              member=member, admin=admin, kept=kept.id, tag=tag)
    finally:
        async with engine.begin() as conn:
            await conn.execute(text("DELETE FROM projects WHERE id IN (:a, :b, :c)"),
                               {"a": mine, "b": foreign, "c": deleted})
            await conn.execute(text("DELETE FROM users WHERE id = :u"), {"u": member_id})
        await engine.dispose()
        await app_postgres.dispose_engine_for_loop()


async def _list(world, user, *, project_id=None, limit=10):
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.routers.compliance_packs import list_project_compliance_packs

    async with AsyncSession(world.engine) as db:
        return await list_project_compliance_packs(
            project_id=project_id, limit=limit, current_user=user, db=db
        )


async def test_a_member_sees_only_their_active_projects_and_total_counts_past_the_limit(world):
    result = await _list(world, world.member)
    assert {item.project_id for item in result.items} == {world.mine}, (
        "no foreign tenant's pack, no deleted project's pack"
    )
    assert len(result.items) == 10
    assert result.total == 12, "total is a COUNT over the scope, not len(items)"
    generated = [item.generated_at for item in result.items]
    assert generated == sorted(generated, reverse=True), "newest first"


async def test_naming_a_project_the_caller_is_not_in_is_403(world):
    with pytest.raises(HTTPException) as exc:
        await _list(world, world.member, project_id=str(world.foreign))
    assert exc.value.status_code == 403


async def test_naming_a_deleted_project_returns_nothing(world):
    result = await _list(world, world.member, project_id=str(world.deleted))
    assert result.items == [] and result.total == 0


async def test_an_admin_sees_every_active_project(world):
    result = await _list(world, world.admin, limit=50)
    projects = {item.project_id for item in result.items}
    assert {world.mine, world.foreign} <= projects
    assert world.deleted not in projects


async def test_the_item_shape_retired_and_a_deleted_release(world):
    result = await _list(world, world.member, project_id=str(world.mine), limit=50)
    assert result.total == 12 and len(result.items) == 12
    dumped = [item.model_dump() for item in result.items]
    for item in dumped:
        assert "minio_key" not in item and "metadata_snapshot" not in item and "notes" not in item
        assert item["bytes"] == 422_195 and item["manifest_sha256"].startswith("ab12cd34")
        assert item["project_name"] == f"cp-{world.tag}"
    by_release = {}
    for item in dumped:
        by_release.setdefault(item["release_id"], []).append(item)
    (orphan,) = by_release[None]
    assert orphan["release_name"] is None and orphan["release_version"] is None
    named = by_release[world.kept]
    assert {i["release_name"] for i in named} == {f"2026.11-{world.tag}"}
    assert {i["release_version"] for i in named} == {"2026.11.0"}
    assert sorted(i["retired"] for i in dumped) == [False] * 11 + [True]


async def test_a_user_in_no_project_gets_an_empty_list_without_a_query(world, monkeypatch):
    from app.services import compliance_pack_service as svc

    loner = SimpleNamespace(id=uuid.uuid4(), role="VIEWER", username="loner", is_active=True)
    calls = []
    real = svc.list_packs_for_projects

    async def _spy(db, project_ids, *, limit):
        calls.append(project_ids)
        return await real(db, project_ids, limit=limit)

    monkeypatch.setattr(svc, "list_packs_for_projects", _spy)
    result = await _list(world, loner)
    assert result.items == [] and result.total == 0
    assert calls == [set()]
