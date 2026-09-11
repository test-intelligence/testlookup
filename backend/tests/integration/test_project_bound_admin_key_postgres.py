"""Re-audit N20 against a real database: an admin's project-bound key is not an instance admin.

The scenario: an ADMIN mints an API key bound to one project for that team's
CI, and the key leaks. Before N20 it could ``POST /api/v1/users`` with
``"role": "ADMIN"``, receive the new admin's temporary password, and log in as
an instance administrator. It could also mint itself an UNBOUND key.

Every request here goes through the real app with REAL credentials, so no
auth dependency is overridden:

* the project-bound key is an ``api_keys`` row holding the sha256 of the raw
  key, so ``_validate_api_key`` binds the project on the user exactly as in
  production;
* the unbound administrator sends a signed JWT, whose revocation is checked in
  Redis and Postgres like any other.

Only ``get_db`` is pointed at this test's engine, and the three things CI
cannot host (MongoDB for the citation check, the Celery broker, and the
request-scoped Redis client) are replaced with doubles that keep the real
contract: the Celery double binds its arguments to the task's real signature.

Requires ``TESTLOOKUP_POSTGRES_TEST_DSN`` and ``REDIS_URL``, database migrated
to head.
"""
from __future__ import annotations

import hashlib
import inspect
import os
import secrets
import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytest.importorskip("asyncpg")
pytest.importorskip("httpx")
pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def _env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        pytest.skip(f"{name} is not configured")
    return value


class _Mongo:
    """The citation check counts decision reports; there are none."""

    class _Collection:
        async def count_documents(self, _query):
            return 0

    def __getitem__(self, _name):
        return self._Collection()


@pytest.fixture
async def world(monkeypatch):
    redis_asyncio = pytest.importorskip("redis.asyncio")
    from httpx import ASGITransport, AsyncClient

    from app.core.security import create_access_token
    from app.db.postgres import get_db
    from app.main import app
    from app.models.postgres import (
        AccessAuditLog,
        ApiKey,
        DeletionJob,
        FeatureFlag,
        LaunchStatus,
        Project,
        ProjectActivityEvent,
        ProjectLlmQuota,
        ReleaseGatePolicy,
        SettingsAuditLog,
        TestRun,
        User,
        UserRole,
    )
    from app.models.schemas import PolicyDocument
    from app.worker.tasks import delete_run_everywhere

    engine = create_async_engine(
        _env("TESTLOOKUP_POSTGRES_TEST_DSN"), pool_size=4, max_overflow=0
    )
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    redis = redis_asyncio.Redis.from_url(_env("REDIS_URL"), decode_responses=True)

    async def _get_db():
        # What app.db.postgres.get_db does, on this test's engine.
        async with sessions() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[get_db] = _get_db
    monkeypatch.setattr("app.db.redis_client.get_redis", lambda: redis)
    monkeypatch.setattr("app.db.postgres.AsyncSessionLocal", sessions, raising=False)
    monkeypatch.setattr("app.db.mongo.get_mongo_db", lambda: _Mongo())

    queued: list[tuple] = []
    task = delete_run_everywhere._get_current_object()
    signature = inspect.signature(task.run)

    def _delay(*args, **kwargs):
        signature.bind(*args, **kwargs)  # the argument check a real broker hop runs
        queued.append(args)

    monkeypatch.setattr(task, "delay", _delay)

    tag = uuid.uuid4().hex[:10]
    project_a, project_b = uuid.uuid4(), uuid.uuid4()
    key_owner, instance_admin, target = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    run_a, run_b = uuid.uuid4(), uuid.uuid4()
    policy_b = uuid.uuid4()
    raw_key = f"qai_{secrets.token_urlsafe(32)}"

    async with sessions() as db:
        for pid, letter in ((project_a, "a"), (project_b, "b")):
            db.add(Project(id=pid, name=f"n20-{letter}-{tag}", slug=f"n20-{letter}-{tag}", is_active=True))
        for uid, role, name in (
            (key_owner, UserRole.ADMIN, "owner"),
            (instance_admin, UserRole.ADMIN, "admin"),
            (target, UserRole.QA_ENGINEER, "target"),
        ):
            db.add(User(
                id=uid, email=f"n20-{name}-{tag}@example.com", username=f"n20_{name}_{tag}",
                full_name=f"N20 {name}", hashed_password="!unusable", role=role.value,
            ))
        await db.flush()
        for rid, pid in ((run_a, project_a), (run_b, project_b)):
            db.add(TestRun(
                id=rid, project_id=pid, build_number=f"n20-{rid.hex[:8]}",
                jenkins_job="n20", status=LaunchStatus.FAILED, ingestion_source="unknown",
                total_tests=1, failed_tests=1,
            ))
        db.add(ApiKey(
            id=uuid.uuid4(), user_id=key_owner, name="team-a ci",
            key_hash=hashlib.sha256(raw_key.encode()).hexdigest(), key_hint=raw_key[:8] + "...",
            scopes=[], project_id=project_a,
        ))
        db.add(ReleaseGatePolicy(
            id=policy_b, project_id=project_b, version=1, name="team-b gate",
            rules=PolicyDocument().model_dump(), is_draft=True, is_active=False,
            created_by=instance_admin,
        ))
        await db.commit()

    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")
    world = SimpleNamespace(
        client=client, sessions=sessions, tag=tag, queued=queued,
        project_a=project_a, project_b=project_b,
        key_owner=key_owner, instance_admin=instance_admin, target=target,
        run_a=run_a, run_b=run_b, policy_b=policy_b,
        key={"X-API-Key": raw_key},
        admin={"Authorization": f"Bearer {create_access_token(str(instance_admin))}"},
        flag_key=f"n20_flag_{tag}",
        created_emails=[],
        models=SimpleNamespace(
            ApiKey=ApiKey, User=User, TestRun=TestRun, ReleaseGatePolicy=ReleaseGatePolicy,
            ProjectLlmQuota=ProjectLlmQuota, FeatureFlag=FeatureFlag, UserRole=UserRole,
        ),
    )
    try:
        yield world
    finally:
        await client.aclose()
        app.dependency_overrides.pop(get_db, None)
        projects = [project_a, project_b]
        users = [key_owner, instance_admin, target]
        async with sessions() as db:
            created = (await db.execute(
                select(User.id).where(User.email.in_(world.created_emails))
            )).scalars().all() if world.created_emails else []
        users += list(created)
        # Teardown of a disposable database: each delete in its own transaction,
        # so one table this route set happened not to touch cannot strand the rest.
        for statement in (
            delete(ApiKey).where(ApiKey.user_id.in_(users)),
            delete(ReleaseGatePolicy).where(or_(
                ReleaseGatePolicy.project_id.in_(projects), ReleaseGatePolicy.created_by.in_(users),
            )),
            delete(ProjectLlmQuota).where(ProjectLlmQuota.project_id.in_(projects)),
            delete(FeatureFlag).where(FeatureFlag.key == world.flag_key),
            delete(DeletionJob).where(DeletionJob.project_id.in_(projects)),
            delete(ProjectActivityEvent).where(ProjectActivityEvent.project_id.in_(projects)),
            delete(AccessAuditLog).where(AccessAuditLog.actor_user_id.in_(users)),
            delete(SettingsAuditLog).where(SettingsAuditLog.actor_id.in_(users)),
            delete(TestRun).where(TestRun.project_id.in_(projects)),
            delete(User).where(User.id.in_(users)),
            delete(Project).where(Project.id.in_(projects)),
        ):
            try:
                async with sessions.begin() as db:
                    await db.execute(statement)
            except Exception as exc:  # noqa: BLE001 -- report, keep cleaning
                print(f"n20 teardown: {type(exc).__name__}: {str(exc)[:160]}")
        await redis.aclose()
        await engine.dispose()


async def _count(world, statement) -> int:
    async with world.sessions() as db:
        return (await db.execute(statement)).scalar_one()


# ── the leaked key cannot reach the instance ────────────────────────────────


async def test_the_bound_key_cannot_create_an_instance_admin(world):
    email = f"n20-evil-{world.tag}@example.com"
    world.created_emails.append(email)

    resp = await world.client.post("/api/v1/users", headers=world.key, json={
        "email": email, "username": f"n20_evil_{world.tag}", "full_name": "Evil", "role": "ADMIN",
    })

    assert resp.status_code == 403, resp.text
    assert "temp_password" not in resp.text
    User = world.models.User
    assert await _count(world, select(func.count()).select_from(User).where(User.email == email)) == 0


async def test_the_bound_key_cannot_promote_a_user(world):
    resp = await world.client.patch(
        f"/api/v1/users/{world.target}/role", headers=world.key, json={"role": "ADMIN"}
    )

    assert resp.status_code == 403, resp.text
    User = world.models.User
    async with world.sessions() as db:
        role = (await db.execute(select(User.role).where(User.id == world.target))).scalar_one()
    assert str(role) == world.models.UserRole.QA_ENGINEER.value


async def test_the_bound_key_cannot_flip_a_feature_flag(world):
    created = await world.client.post(
        "/api/v1/feature-flags", headers=world.admin, json={"key": world.flag_key}
    )
    assert created.status_code == 201, created.text

    resp = await world.client.patch(
        f"/api/v1/feature-flags/{world.flag_key}", headers=world.key, json={"enabled_global": True}
    )

    assert resp.status_code == 403, resp.text
    FeatureFlag = world.models.FeatureFlag
    async with world.sessions() as db:
        enabled = (await db.execute(
            select(FeatureFlag.enabled_global).where(FeatureFlag.key == world.flag_key)
        )).scalar_one()
    assert enabled is False


async def test_the_bound_key_cannot_configure_sso(world):
    resp = await world.client.post("/api/v1/sso/configs", headers=world.key, json={
        "display_name": "Evil IdP", "idp_entity_id": "https://idp.example.com",
        "idp_sso_url": "https://idp.example.com/sso", "idp_certificate": "not checked",
        "sp_entity_id": "testlookup", "sp_acs_url": "https://testlookup.example.com/acs",
        "role_mapping": {"everyone": "ADMIN"},
    })

    assert resp.status_code == 403, resp.text


async def test_the_bound_key_cannot_raise_its_own_projects_llm_budget(world):
    """Its OWN project: the quota is the one project-scoped route that stays closed."""
    resp = await world.client.put(
        f"/api/v1/projects/{world.project_a}/llm-quota", headers=world.key,
        json={"hard_cap_usd": 1_000_000, "at_cap_action": "SOFT_WARN"},
    )

    assert resp.status_code == 403, resp.text
    Quota = world.models.ProjectLlmQuota
    assert await _count(
        world, select(func.count()).select_from(Quota).where(Quota.project_id == world.project_a)
    ) == 0


@pytest.mark.parametrize("extra", ["other-project"])
async def test_the_bound_key_cannot_mint_a_key_wider_than_itself(world, extra):
    body = {"name": "escape"}
    if extra == "other-project":
        body["project_id"] = str(world.project_b)

    resp = await world.client.post("/api/v1/keys", headers=world.key, json=body)

    assert resp.status_code == 403, resp.text
    assert "raw_key" not in resp.text
    ApiKey = world.models.ApiKey
    assert await _count(
        world, select(func.count()).select_from(ApiKey).where(ApiKey.user_id == world.key_owner)
    ) == 1


async def test_a_key_minted_without_a_project_is_bound_to_the_callers(world):
    """``testlookup keys create`` sends no project_id: CI rotation must keep
    working, and the key it gets must never be unbound."""
    resp = await world.client.post("/api/v1/keys", headers=world.key, json={"name": "rotate-cli"})

    assert resp.status_code in (200, 201), resp.text
    assert resp.json()["project_id"] == str(world.project_a)


# ── and still does its own project's work ────────────────────────────────────


async def test_the_bound_key_rotates_itself_for_its_own_project(world):
    resp = await world.client.post(
        "/api/v1/keys", headers=world.key,
        json={"name": "team-a ci (rotated)", "project_id": str(world.project_a)},
    )

    assert resp.status_code == 201, resp.text
    minted = resp.json()
    assert minted["project_id"] == str(world.project_a)
    ApiKey = world.models.ApiKey
    async with world.sessions() as db:
        owner = (await db.execute(
            select(ApiKey.user_id).where(ApiKey.id == uuid.UUID(minted["id"]))
        )).scalar_one()
    assert owner == world.key_owner

    listed = await world.client.get("/api/v1/keys", headers={"X-API-Key": minted["raw_key"]})
    assert listed.status_code == 200, listed.text
    assert {k["project_id"] for k in listed.json()} == {str(world.project_a)}


async def test_the_bound_key_deletes_its_own_projects_run_and_no_other(world):
    body = {"confirm": True, "reason": "n20 ci cleanup"}

    foreign = await world.client.request(
        "DELETE", f"/api/v1/runs/{world.run_b}", headers=world.key, json=body
    )
    own = await world.client.request(
        "DELETE", f"/api/v1/runs/{world.run_a}", headers=world.key, json=body
    )

    assert foreign.status_code == 403, foreign.text
    assert own.status_code == 202, own.text
    assert [args[0] for args in world.queued] == [str(world.run_a)]
    TestRun = world.models.TestRun
    assert await _count(
        world, select(func.count()).select_from(TestRun).where(TestRun.id == world.run_b)
    ) == 1


async def test_the_bound_key_writes_only_its_own_projects_release_gate(world):
    def create(project_id):
        return world.client.post("/api/v1/release-gate-policies", headers=world.key, json={
            "project_id": project_id, "name": "ci gate",
        })

    own = await create(str(world.project_a))
    other = await create(str(world.project_b))
    system_default = await create(None)
    foreign_edit = await world.client.patch(
        f"/api/v1/release-gate-policies/{world.policy_b}", headers=world.key, json={"name": "hijacked"}
    )

    assert own.status_code == 201, own.text
    assert own.json()["project_id"] == str(world.project_a)
    assert other.status_code == 403, other.text
    assert system_default.status_code == 403, system_default.text
    assert foreign_edit.status_code == 403, foreign_edit.text
    Policy = world.models.ReleaseGatePolicy
    async with world.sessions() as db:
        rows = (await db.execute(
            select(Policy.project_id, Policy.name).where(Policy.created_by == world.key_owner)
        )).all()
        name_b = (await db.execute(select(Policy.name).where(Policy.id == world.policy_b))).scalar_one()
    assert [r.project_id for r in rows] == [world.project_a]
    assert name_b == "team-b gate"


# ── an unbound administrator is unchanged ────────────────────────────────────


async def test_an_instance_admin_still_administers_the_instance(world):
    email = f"n20-new-{world.tag}@example.com"
    world.created_emails.append(email)

    created = await world.client.post("/api/v1/users", headers=world.admin, json={
        "email": email, "username": f"n20_new_{world.tag}", "full_name": "New", "role": "QA_ENGINEER",
    })
    promoted = await world.client.patch(
        f"/api/v1/users/{world.target}/role", headers=world.admin, json={"role": "QA_LEAD"}
    )
    flag = await world.client.post(
        "/api/v1/feature-flags", headers=world.admin, json={"key": world.flag_key}
    )
    flipped = await world.client.patch(
        f"/api/v1/feature-flags/{world.flag_key}", headers=world.admin, json={"enabled_global": True}
    )
    quota = await world.client.put(
        f"/api/v1/projects/{world.project_a}/llm-quota", headers=world.admin, json={"hard_cap_usd": 50}
    )
    unbound_key = await world.client.post("/api/v1/keys", headers=world.admin, json={"name": "ops"})

    assert created.status_code == 201, created.text
    assert created.json()["temp_password"]
    assert promoted.status_code == 200, promoted.text
    assert flag.status_code == 201, flag.text
    assert flipped.status_code == 200 and flipped.json()["enabled_global"] is True, flipped.text
    assert quota.status_code == 200, quota.text
    assert unbound_key.status_code == 201 and unbound_key.json()["project_id"] is None, unbound_key.text
