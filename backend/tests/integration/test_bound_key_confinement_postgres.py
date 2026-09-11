"""Re-audit N26 against a real database: a project-bound key stays in its project.

A key bound to one project is that project's credential, but its owner is
almost always an ADMIN, and the checks below compared the owner:

(a) ``GET /api/v1/release-gate-policies/{id}`` checked nothing: any signed-in
    caller, and a key bound to another project, read any project's gate rules.
(b) ``require_role(UserRole.QA_LEAD)`` never read the binding, so a bound key
    reached the instance-wide QA_LEAD routes: the user directory,
    ``POST /projects``, the training export, the settings reads, AI-eval,
    integration health. QA_LEAD now refuses a bound key unless the route opts
    in (and the opt-ins are the reviewed, project-confined routes).
(c) ``/me/assigned-failures?scope=mine`` is scoped to the key OWNER's
    assignments, which span every project the owner works in.
(d) A rotated key inherits its minter's expiry (round 4); a minter with none
    passes on none -- the same lifetime, not a longer one.
(e) The debug router's route is checked like every other (the scan half is in
    tests/test_architectural_authorization.py).

Real app, real ``api_keys`` rows authenticated by ``_validate_api_key``, no
auth override. The app's own engine is disposed at the start and the end.
Requires ``TESTLOOKUP_POSTGRES_TEST_DSN`` and ``REDIS_URL``, migrated to head.
"""
from __future__ import annotations

import hashlib
import os
import secrets
import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytest.importorskip("asyncpg")
pytest.importorskip("httpx")
pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def _env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        pytest.skip(f"{name} is not configured")
    return value


#: name -> (scopes, bound to project A). All owned by one ADMIN.
_KEYS = {
    "legacy_a": ([], True),                 # a pre-scope CI key: full access, bound
    "admin_a": (["project:admin"], True),   # administers A -- and only A
    "unbound": ([], False),                 # a user-scoped key: no binding at all
}


@pytest.fixture
async def world(monkeypatch):
    redis_asyncio = pytest.importorskip("redis.asyncio")
    from httpx import ASGITransport, AsyncClient

    from app.core.security import create_access_token
    from app.db import postgres as app_postgres
    from app.db.postgres import get_db
    from app.main import app
    from app.models.postgres import (
        AccessAuditLog,
        ApiKey,
        LaunchStatus,
        Project,
        ProjectActivityEvent,
        ProjectMember,
        ReleaseGatePolicy,
        TestCase,
        TestRun,
        TestStatus,
        User,
        UserRole,
    )

    await app_postgres.dispose_engine_for_loop()
    engine = create_async_engine(_env("TESTLOOKUP_POSTGRES_TEST_DSN"), pool_size=4, max_overflow=0)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    redis = redis_asyncio.Redis.from_url(_env("REDIS_URL"), decode_responses=True)

    async def _get_db():
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

    tag = uuid.uuid4().hex[:10]
    project_a, project_b = uuid.uuid4(), uuid.uuid4()
    owner, member_b, outsider = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    run_a, run_b = uuid.uuid4(), uuid.uuid4()
    policy_b, policy_system = uuid.uuid4(), uuid.uuid4()
    raw = {name: f"qai_{secrets.token_urlsafe(32)}" for name in _KEYS}

    async with sessions() as db:
        for pid, label in ((project_a, "a"), (project_b, "b")):
            name = f"n26-{label}-{tag}"
            db.add(Project(id=pid, name=name, slug=name, is_active=True))
        for uid, label, role in (
            (owner, "owner", UserRole.ADMIN),
            (member_b, "memberb", UserRole.QA_ENGINEER),
            (outsider, "outsider", UserRole.QA_ENGINEER),
        ):
            db.add(User(
                id=uid, email=f"n26-{label}-{tag}@example.com", username=f"n26_{label}_{tag}",
                full_name=f"N26 {label}", hashed_password="!unusable", role=role.value,
            ))
        await db.flush()
        db.add(ProjectMember(project_id=project_b, user_id=member_b, role=UserRole.QA_ENGINEER.value))
        for rid, pid in ((run_a, project_a), (run_b, project_b)):
            db.add(TestRun(
                id=rid, project_id=pid, build_number=f"n26-{rid.hex[:8]}", jenkins_job="n26",
                status=LaunchStatus.FAILED, ingestion_source="unknown", total_tests=1, failed_tests=1,
            ))
        await db.flush()
        # One failure assigned to the key owner in each project (finding c).
        for rid in (run_a, run_b):
            db.add(TestCase(
                id=uuid.uuid4(), test_run_id=rid, test_name=f"n26_{tag}_{rid.hex[:6]}",
                test_fingerprint=f"n26-{tag}-{rid.hex}",
                status=TestStatus.FAILED, assigned_to_user_id=owner,
            ))
        # Drafts and inactive, so no other test's effective-policy resolution sees them.
        for pol_id, pid in ((policy_b, project_b), (policy_system, None)):
            db.add(ReleaseGatePolicy(
                id=pol_id, project_id=pid, version=1, name=f"n26-policy-{tag}",
                rules={"rules": []}, is_active=False, is_draft=True, created_by=owner,
            ))
        for name, (scopes, bound) in _KEYS.items():
            db.add(ApiKey(
                id=uuid.uuid4(), user_id=owner, name=f"n26 {name}",
                key_hash=hashlib.sha256(raw[name].encode()).hexdigest(),
                key_hint=raw[name][:8] + "...", scopes=scopes,
                project_id=project_a if bound else None, expires_at=None,
            ))
        await db.commit()

    def _jwt(uid):
        return {"Authorization": f"Bearer {create_access_token(str(uid))}"}

    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")
    world = SimpleNamespace(
        client=client, sessions=sessions, tag=tag, owner=owner,
        project_a=project_a, project_b=project_b, policy_b=policy_b, policy_system=policy_system,
        headers={name: {"X-API-Key": value} for name, value in raw.items()},
        owner_jwt=_jwt(owner), member_b_jwt=_jwt(member_b), outsider_jwt=_jwt(outsider),
        models=SimpleNamespace(ApiKey=ApiKey, Project=Project),
    )
    try:
        yield world
    finally:
        await client.aclose()
        app.dependency_overrides.pop(get_db, None)
        projects = (project_a, project_b)
        for statement in (
            delete(ApiKey).where(ApiKey.user_id == owner),
            delete(ReleaseGatePolicy).where(ReleaseGatePolicy.id.in_((policy_b, policy_system))),
            delete(ProjectActivityEvent).where(ProjectActivityEvent.project_id.in_(projects)),
            delete(AccessAuditLog).where(AccessAuditLog.actor_user_id == owner),
            delete(TestRun).where(TestRun.project_id.in_(projects)),  # test cases cascade
            delete(Project).where(Project.id.in_(projects)),          # members cascade
            # ...and any project a regression let a key create.
            delete(Project).where(Project.name.like(f"n26-%-{tag}")),
            delete(User).where(User.email.like(f"n26-%-{tag}@example.com")),
        ):
            try:
                async with sessions.begin() as db:
                    await db.execute(statement)
            except Exception as exc:  # noqa: BLE001 -- report, keep cleaning
                print(f"n26 teardown: {type(exc).__name__}: {str(exc)[:160]}")
        await redis.aclose()
        await engine.dispose()
        await app_postgres.dispose_engine_for_loop()


# ── (a) a release-gate policy is read through its project ───────────────────


@pytest.mark.parametrize("who", ["outsider_jwt", "legacy_a"])
async def test_another_projects_policy_is_not_readable(world, who):
    headers = world.headers[who] if who in world.headers else getattr(world, who)

    resp = await world.client.get(f"/api/v1/release-gate-policies/{world.policy_b}", headers=headers)

    assert resp.status_code == 403, resp.text


@pytest.mark.parametrize("who", ["member_b_jwt", "owner_jwt"])
async def test_a_member_or_an_admin_reads_the_policy(world, who):
    resp = await world.client.get(
        f"/api/v1/release-gate-policies/{world.policy_b}", headers=getattr(world, who)
    )

    assert resp.status_code == 200, resp.text
    assert resp.json()["id"] == str(world.policy_b)


async def test_the_system_default_stays_readable_by_anyone_signed_in(world):
    resp = await world.client.get(
        f"/api/v1/release-gate-policies/{world.policy_system}", headers=world.outsider_jwt
    )

    assert resp.status_code == 200, resp.text


# ── (b) instance-wide QA_LEAD routes refuse a bound key ─────────────────────


def _instance_wide(world):
    return {
        "user directory": ("GET", "/api/v1/users", None),
        "one user": ("GET", f"/api/v1/users/{world.owner}", None),
        "a user's memberships": ("GET", f"/api/v1/users/{world.owner}/memberships", None),
        "training export": ("POST", "/api/v1/training/export", None),
        "training finetune": ("POST", "/api/v1/training/finetune", None),
        "smtp settings": ("GET", "/api/v1/settings/smtp", None),
        "settings audit log": ("GET", "/api/v1/settings/audit-log", None),
        "integration probe": ("POST", "/api/v1/integration-health/probe", None),
        "ai-eval dashboard": ("GET", "/api/v1/ai-eval/dashboard", None),
        "performance budgets": ("GET", "/api/v1/performance/budgets", None),
    }


@pytest.mark.parametrize("key", ["legacy_a", "admin_a"])
@pytest.mark.parametrize("door", sorted(_instance_wide(SimpleNamespace(owner="x"))))
async def test_a_bound_key_is_refused_an_instance_wide_qa_lead_route(world, door, key):
    from app.core.deps import PROJECT_KEY_NOT_INSTANCE_ADMIN_DETAIL

    method, path, body = _instance_wide(world)[door]

    resp = await world.client.request(method, path, headers=world.headers[key], json=body)

    assert resp.status_code == 403, resp.text
    assert resp.json()["detail"] == PROJECT_KEY_NOT_INSTANCE_ADMIN_DETAIL


async def test_a_bound_key_cannot_create_a_project(world):
    Project = world.models.Project
    name = f"n26-made-{world.tag}"

    resp = await world.client.post(
        "/api/v1/projects", headers=world.headers["legacy_a"], json={"name": name, "slug": name}
    )

    assert resp.status_code == 403, resp.text
    async with world.sessions() as db:
        made = (await db.execute(
            select(func.count()).select_from(Project).where(Project.name == name)
        )).scalar_one()
    assert made == 0


@pytest.mark.parametrize("who", ["unbound", "owner_jwt"])
async def test_an_unbound_admin_still_reads_the_user_directory(world, who):
    headers = world.headers[who] if who in world.headers else getattr(world, who)

    resp = await world.client.get("/api/v1/users", headers=headers)

    assert resp.status_code == 200, resp.text


async def test_an_opted_in_qa_lead_route_still_takes_the_key_in_its_own_project(world):
    ok = await world.client.put(
        f"/api/v1/projects/{world.project_a}", headers=world.headers["legacy_a"],
        json={"description": "n26 own project"},
    )
    other = await world.client.put(
        f"/api/v1/projects/{world.project_b}", headers=world.headers["legacy_a"],
        json={"description": "n26 other project"},
    )

    assert ok.status_code == 200, ok.text
    assert other.status_code == 403, other.text


# ── (c) scope=mine for a bound key is its project's share of the owner's inbox ─


async def _inbox(world, headers):
    listing = await world.client.get(
        "/api/v1/me/assigned-failures", headers=headers, params={"scope": "mine"}
    )
    count = await world.client.get(
        "/api/v1/me/assigned-failures/count", headers=headers, params={"scope": "mine"}
    )
    assert listing.status_code == 200, listing.text
    assert count.status_code == 200, count.text
    projects = {item["project_id"] for item in listing.json()["items"]}
    return projects & {str(world.project_a), str(world.project_b)}, count.json()


async def test_a_bound_keys_inbox_is_its_own_projects_only(world):
    projects, count = await _inbox(world, world.headers["legacy_a"])

    assert projects == {str(world.project_a)}
    # The owner is an ADMIN of a shared database: other tests' rows may be
    # assigned to others, never to this owner, so the count is exactly ours.
    assert count["count"] == 1, count


async def test_the_owner_signed_in_still_sees_every_project(world):
    projects, count = await _inbox(world, world.owner_jwt)

    assert projects == {str(world.project_a), str(world.project_b)}
    assert count["count"] == 2, count


# ── (d) a rotated key lives no longer than the key that minted it ───────────


async def test_rotating_a_never_expiring_key_gives_a_never_expiring_key(world):
    """DOCUMENTED-AS-ACCEPTED: a minter with no expiry passes on none.

    The round-4 cap stops a key outliving its minter; a never-expiring minter
    already lives for ever, so its rotation lives exactly as long. Bounding
    every key's lifetime is a policy (a maximum key age), not this fix.
    """
    resp = await world.client.post(
        "/api/v1/keys", headers=world.headers["legacy_a"], json={"name": f"n26 rotated {world.tag}"}
    )

    assert resp.status_code == 201, resp.text
    assert resp.json()["expires_at"] is None


# ── (e) the debug route checks the project it is handed ─────────────────────


async def test_the_debug_route_refuses_a_bound_key_and_checks_its_project(world):
    from app.core.deps import PROJECT_KEY_NOT_INSTANCE_ADMIN_DETAIL

    path = "/api/v1/debug/generate-test-run"
    refused = await world.client.post(
        path, headers=world.headers["legacy_a"], params={"project_id": str(world.project_a)}
    )
    missing = await world.client.post(
        path, headers=world.owner_jwt, params={"project_id": str(uuid.uuid4())}
    )

    assert refused.status_code == 403, refused.text
    assert refused.json()["detail"] == PROJECT_KEY_NOT_INSTANCE_ADMIN_DETAIL
    assert missing.status_code == 404, missing.text
