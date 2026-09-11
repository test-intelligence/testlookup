"""QA-R3-11 against a real database: a scoped API key is limited to its scopes.

``api_keys.scopes`` was read only by the streaming ingest dependency. A key
bound to project A with ``scopes=["stream:write"]`` (the scope the API Keys
page mints for CI) could, through N20's ``allow_project_key=True`` routes,
delete A's runs and reset A, and ``POST /api/v1/keys {"name": "rotated"}``
minted it an unrestricted (``scopes: []``), never-expiring replacement that
survived revoking the original.

Now a scoped key needs ``project:admin`` on those routes, an empty (legacy)
scope list stays full access, and a key that mints a key grants no scope it
does not hold and no expiry later than its own.

QA-R4-1 / QA-R4-2: the same key still administered its project through routes
gated below ADMIN (team-channel webhook, add member, update and delete the
project), and an UNBOUND one created instance administrators. A scoped key
without ``project:admin`` is now refused by ``require_role`` at QA_LEAD and
above, and by the project-scoped guards on every write; reads and streaming
still work.

Real app, real ``api_keys`` rows authenticated by ``_validate_api_key``, no
auth override. ``get_db`` and ``AsyncSessionLocal`` point at this test's
engine; the app's own engine is disposed at the start and end (QA-R3-14).
Doubles: the MongoDB citation count (0) and ``delete_run_everywhere.delay``,
which binds its arguments to the task's real signature.

Requires ``TESTLOOKUP_POSTGRES_TEST_DSN`` and ``REDIS_URL``, migrated to head.
"""
from __future__ import annotations

import hashlib
import inspect
import os
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.deps import PROJECT_KEY_NOT_INSTANCE_ADMIN_DETAIL  # noqa: E402

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


#: name -> (scopes, days until expiry or None, bound to project A). All owned by
#: one ADMIN. ``stream_unbound`` is QA-R4-2's key (and kills QA's surviving
#: mutation MA, "scope checked only for a bound key"); ``legacy_expiring`` is
#: QA's MD (a legacy key WITH an expiry).
_KEYS = {
    "stream": (["stream:write"], 30, True),
    "project_admin": (["stream:write", "project:admin"], 10, True),
    "legacy": ([], None, True),
    "stream_unbound": (["stream:write"], 30, False),
    "legacy_expiring": ([], 5, True),
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
        DeletionJob,
        LaunchStatus,
        Project,
        ProjectActivityEvent,
        ProjectMember,
        TeamNotificationChannel,
        TestRun,
        User,
        UserRole,
    )
    from app.worker.tasks import delete_run_everywhere

    await app_postgres.dispose_engine_for_loop()
    engine = create_async_engine(
        _env("TESTLOOKUP_POSTGRES_TEST_DSN"), pool_size=4, max_overflow=0
    )
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
    monkeypatch.setattr("app.db.mongo.get_mongo_db", lambda: _Mongo())

    queued: list[tuple] = []
    task = delete_run_everywhere._get_current_object()
    signature = inspect.signature(task.run)

    def _delay(*args, **kwargs):
        signature.bind(*args, **kwargs)  # the argument check a real broker hop runs
        queued.append(args)

    monkeypatch.setattr(task, "delay", _delay)

    tag = uuid.uuid4().hex[:10]
    project_a = uuid.uuid4()
    project_a_name = f"r4s-a-{tag}"
    owner = uuid.uuid4()
    run_1, run_2 = uuid.uuid4(), uuid.uuid4()
    now = datetime.now(timezone.utc)
    raw = {name: f"qai_{secrets.token_urlsafe(32)}" for name in _KEYS}
    expiry = {
        name: (None if days is None else now + timedelta(days=days))
        for name, (_scopes, days, _bound) in _KEYS.items()
    }
    outsider = uuid.uuid4()

    async with sessions() as db:
        db.add(Project(id=project_a, name=project_a_name, slug=project_a_name, is_active=True))
        db.add(User(
            id=owner, email=f"r4s-owner-{tag}@example.com", username=f"r4s_owner_{tag}",
            full_name="R4 key owner", hashed_password="!unusable", role=UserRole.ADMIN.value,
        ))
        # QA-R4-1: the account a leaked key would make a QA_LEAD of project A.
        db.add(User(
            id=outsider, email=f"r4s-outsider-{tag}@example.com",
            username=f"r4s_outsider_{tag}", full_name="R4 outsider",
            hashed_password="!unusable", role=UserRole.QA_ENGINEER.value,
        ))
        await db.flush()
        for rid in (run_1, run_2):
            db.add(TestRun(
                id=rid, project_id=project_a, build_number=f"r4s-{rid.hex[:8]}",
                jenkins_job="r4s", status=LaunchStatus.FAILED, ingestion_source="unknown",
                total_tests=1, failed_tests=1,
            ))
        for name, (scopes, _days, bound) in _KEYS.items():
            db.add(ApiKey(
                id=uuid.uuid4(), user_id=owner, name=f"team-a {name}",
                key_hash=hashlib.sha256(raw[name].encode()).hexdigest(),
                key_hint=raw[name][:8] + "...", scopes=scopes,
                project_id=project_a if bound else None,
                expires_at=expiry[name],
            ))
        await db.commit()

    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")
    world = SimpleNamespace(
        client=client, sessions=sessions, tag=tag, queued=queued,
        project_a=project_a, project_a_name=project_a_name, owner=owner,
        outsider=outsider, run_1=run_1, run_2=run_2, expiry=expiry,
        headers={name: {"X-API-Key": value} for name, value in raw.items()},
        admin_jwt={"Authorization": f"Bearer {create_access_token(str(owner))}"},
        models=SimpleNamespace(
            ApiKey=ApiKey, TestRun=TestRun, Project=Project, User=User,
            ProjectMember=ProjectMember, TeamNotificationChannel=TeamNotificationChannel,
        ),
    )
    try:
        yield world
    finally:
        await client.aclose()
        app.dependency_overrides.pop(get_db, None)
        for statement in (
            delete(ApiKey).where(ApiKey.user_id == owner),
            delete(DeletionJob).where(DeletionJob.project_id == project_a),
            delete(ProjectActivityEvent).where(ProjectActivityEvent.project_id == project_a),
            delete(AccessAuditLog).where(AccessAuditLog.actor_user_id == owner),
            delete(TestRun).where(TestRun.project_id == project_a),
            # Members, team channels and live sessions cascade from these two.
            delete(Project).where(Project.id == project_a),
            # owner, outsider, and any account a regression let a key create.
            delete(User).where(User.email.like(f"r4s-%-{tag}@example.com")),
        ):
            try:
                async with sessions.begin() as db:
                    await db.execute(statement)
            except Exception as exc:  # noqa: BLE001 -- report, keep cleaning
                print(f"r4 scopes teardown: {type(exc).__name__}: {str(exc)[:160]}")
        await redis.aclose()
        await engine.dispose()
        await app_postgres.dispose_engine_for_loop()


async def _runs_left(world) -> int:
    TestRun = world.models.TestRun
    async with world.sessions() as db:
        return (await db.execute(
            select(func.count()).select_from(TestRun).where(TestRun.project_id == world.project_a)
        )).scalar_one()


async def _key_count(world) -> int:
    ApiKey = world.models.ApiKey
    async with world.sessions() as db:
        return (await db.execute(
            select(func.count()).select_from(ApiKey).where(ApiKey.user_id == world.owner)
        )).scalar_one()


def _delete_run(world, headers, run_id):
    return world.client.request(
        "DELETE", f"/api/v1/runs/{run_id}", headers=headers,
        json={"confirm": True, "reason": "r4 scope probe"},
    )


def _mint(world, key, body):
    headers = world.headers[key] if isinstance(key, str) else key
    return world.client.post("/api/v1/keys", headers=headers, json=body)


def _expires(resp) -> datetime | None:
    value = resp.json()["expires_at"]
    return None if value is None else datetime.fromisoformat(value.replace("Z", "+00:00"))


# ── a stream-scoped key does not administer its project ─────────────────────


async def test_a_stream_scoped_key_cannot_delete_its_projects_run(world):
    resp = await _delete_run(world, world.headers["stream"], world.run_1)

    assert resp.status_code == 403, resp.text
    assert "project:admin" in resp.json()["detail"]
    assert world.queued == []
    assert await _runs_left(world) == 2


async def test_a_stream_scoped_key_cannot_reset_its_project(world):
    resp = await world.client.post(
        f"/api/v1/projects/{world.project_a}/reset", headers=world.headers["stream"],
        json={"mode": "runs", "confirmation_name": world.project_a_name},
    )

    assert resp.status_code == 403, resp.text
    assert await _runs_left(world) == 2


# ── ...and a key that holds project:admin, or a legacy key, still does ──────


async def test_a_project_admin_scoped_key_still_administers_its_project(world):
    deleted = await _delete_run(world, world.headers["project_admin"], world.run_1)
    # A wrong confirmation name: past the authorization, refused by the typed
    # confirmation, so nothing is wiped.
    reset = await world.client.post(
        f"/api/v1/projects/{world.project_a}/reset", headers=world.headers["project_admin"],
        json={"mode": "runs", "confirmation_name": "not the project name"},
    )

    assert deleted.status_code == 202, deleted.text
    assert [args[0] for args in world.queued] == [str(world.run_1)]
    assert reset.status_code != 403 and reset.status_code < 500, reset.text


async def test_a_legacy_unscoped_key_is_unchanged(world):
    deleted = await _delete_run(world, world.headers["legacy"], world.run_2)
    minted = await _mint(world, "legacy", {"name": "legacy rotated"})

    assert deleted.status_code == 202, deleted.text
    assert minted.status_code == 201, minted.text
    assert minted.json()["scopes"] == []
    assert minted.json()["expires_at"] is None
    assert minted.json()["project_id"] == str(world.project_a)


# ── a key mints no more than it holds ────────────────────────────────────────


@pytest.mark.parametrize("scopes", [[], ["stream:write", "project:admin"], ["project:admin"]])
async def test_a_stream_scoped_key_cannot_mint_a_wider_key(world, scopes):
    resp = await _mint(world, "stream", {"name": "escape", "scopes": scopes})

    assert resp.status_code == 403, resp.text
    assert "raw_key" not in resp.text
    assert await _key_count(world) == len(_KEYS)


async def test_rotating_a_scoped_key_keeps_its_scopes_and_end_date(world):
    """``testlookup keys create`` sends only a name. The rotated key is as
    narrow as the original and ends when it does, so revoking the original
    and waiting out its expiry both still bound what leaked."""
    rotated = await _mint(world, "stream", {"name": "rotate-cli"})

    assert rotated.status_code == 201, rotated.text
    body = rotated.json()
    assert body["scopes"] == ["stream:write"]
    assert body["project_id"] == str(world.project_a)
    assert _expires(rotated) == world.expiry["stream"]

    child = {"X-API-Key": body["raw_key"]}
    child_delete = await _delete_run(world, child, world.run_1)
    child_escape = await _mint(world, child, {"name": "escape", "scopes": []})
    assert child_delete.status_code == 403, child_delete.text
    assert child_escape.status_code == 403, child_escape.text
    assert await _runs_left(world) == 2


async def test_a_minted_key_may_not_outlive_its_minter(world):
    too_long = await _mint(world, "stream", {"name": "long", "expires_days": 365})
    shorter = await _mint(world, "stream", {"name": "short", "expires_days": 7})

    assert too_long.status_code == 403, too_long.text
    assert shorter.status_code == 201, shorter.text
    assert _expires(shorter) < world.expiry["stream"]
    assert await _key_count(world) == len(_KEYS) + 1


async def test_a_subset_of_the_callers_scopes_is_mintable(world):
    resp = await _mint(world, "project_admin", {"name": "stream only", "scopes": ["stream:write"]})

    assert resp.status_code == 201, resp.text
    assert resp.json()["scopes"] == ["stream:write"]
    assert _expires(resp) == world.expiry["project_admin"]


async def test_minted_scopes_are_stored_once_each_in_order(world):
    """QA-R4 P4: duplicates were stored; blanks were already dropped."""
    resp = await _mint(world, "project_admin", {
        "name": "dupes", "scopes": ["project:admin", "stream:write", "project:admin", "", "stream:write"],
    })

    assert resp.status_code == 201, resp.text
    assert resp.json()["scopes"] == ["project:admin", "stream:write"]
    ApiKey = world.models.ApiKey
    async with world.sessions() as db:
        stored = (await db.execute(
            select(ApiKey.scopes).where(ApiKey.id == uuid.UUID(resp.json()["id"]))
        )).scalar_one()
    assert stored == ["project:admin", "stream:write"]


async def test_a_signed_in_admin_mints_project_admin_keys_as_before(world):
    """``project:admin`` is mintable, and a JWT caller has no scope or expiry cap."""
    resp = await _mint(world, world.admin_jwt, {
        "name": "ci admin", "project_id": str(world.project_a),
        "scopes": ["stream:write", "project:admin"],
    })

    assert resp.status_code == 201, resp.text
    assert resp.json()["scopes"] == ["stream:write", "project:admin"]
    assert resp.json()["expires_at"] is None


async def test_a_legacy_key_with_an_expiry_passes_it_on(world):
    """QA's MD: the expiry cap is not only for SCOPED callers. A legacy
    (unscoped) key that expires mints, with no ``expires_days``, a key that
    ends when it does -- not a never-expiring one that outlives it."""
    rotated = await _mint(world, "legacy_expiring", {"name": "legacy rotated"})
    too_long = await _mint(world, "legacy_expiring", {"name": "long", "expires_days": 365})

    assert rotated.status_code == 201, rotated.text
    assert rotated.json()["scopes"] == []
    assert _expires(rotated) == world.expiry["legacy_expiring"]
    assert too_long.status_code == 403, too_long.text


# ── QA-R4-1: a stream-scoped key does not administer its project below ADMIN ─


#: The four doors QA walked with a bound ["stream:write"] key, plus the
#: team-channel DELETE and member role changes of the same class.
def _project_writes(world):
    a = world.project_a
    return {
        "team channel webhook": (
            "PUT", f"/api/v1/projects/{a}/ownership/team-channels/r4b-team",
            {"channel_type": "slack", "target": "https://hooks.slack.com/services/T0/B0/attacker"},
        ),
        "add member": (
            "POST", f"/api/v1/projects/{a}/members",
            {"user_id": str(world.outsider), "role": "QA_LEAD"},
        ),
        "update project": ("PUT", f"/api/v1/projects/{a}", {"description": "r4b takeover"}),
        "delete project": ("DELETE", f"/api/v1/projects/{a}", None),
    }


async def _project_a_state(world):
    m = world.models
    async with world.sessions() as db:
        project = await db.get(m.Project, world.project_a)
        channels = (await db.execute(
            select(func.count()).select_from(m.TeamNotificationChannel)
            .where(m.TeamNotificationChannel.project_id == world.project_a)
        )).scalar_one()
        members = (await db.execute(
            select(func.count()).select_from(m.ProjectMember)
            .where(m.ProjectMember.user_id == world.outsider)
        )).scalar_one()
        return project.is_active, project.description, channels, members


@pytest.mark.parametrize(
    "door", ["team channel webhook", "add member", "update project", "delete project"]
)
async def test_a_stream_scoped_key_cannot_administer_its_project_below_admin(world, door):
    method, path, body = _project_writes(world)[door]

    resp = await world.client.request(method, path, headers=world.headers["stream"], json=body)

    assert resp.status_code == 403, resp.text
    assert "project:admin" in resp.json()["detail"]
    assert await _project_a_state(world) == (True, None, 0, 0)


async def test_a_stream_scoped_key_still_reads_and_streams(world):
    headers = world.headers["stream"]
    project = await world.client.get(f"/api/v1/projects/{world.project_a}", headers=headers)
    members = await world.client.get(f"/api/v1/projects/{world.project_a}/members", headers=headers)
    run = await world.client.get(f"/api/v1/runs/{world.run_1}", headers=headers)
    session = await world.client.post(
        "/api/v1/stream/sessions", headers=headers,
        json={"project_id": str(world.project_a), "client_name": "r4b-ci"},
    )

    assert project.status_code == 200, project.text
    assert members.status_code == 200, members.text
    assert run.status_code == 200, run.text
    assert session.status_code == 201, session.text


async def test_a_project_admin_key_still_administers_below_admin(world):
    writes = _project_writes(world)
    results = {}
    for door in ("team channel webhook", "add member", "update project"):
        method, path, body = writes[door]
        results[door] = await world.client.request(
            method, path, headers=world.headers["project_admin"], json=body
        )

    assert results["team channel webhook"].status_code == 200, results["team channel webhook"].text
    assert results["add member"].status_code == 201, results["add member"].text
    assert results["update project"].status_code == 200, results["update project"].text
    assert await _project_a_state(world) == (True, "r4b takeover", 1, 1)


async def test_a_legacy_key_still_administers_below_admin(world):
    method, path, body = _project_writes(world)["update project"]

    resp = await world.client.request(method, path, headers=world.headers["legacy"], json=body)

    assert resp.status_code == 200, resp.text


# ── each layer of the rule, alone ─────────────────────────────────────────────
#
# QA's four doors carry BOTH require_role(QA_LEAD) and require_project_access,
# so either layer refuses them and neither is tested by them. These routes
# carry only one layer each (found by walking the real app's dependencies).
# The body is deliberately empty: a dependency's 403 comes before the body's
# 422, so anything but 403 means the key got past authorization.


#: require_role(QA_LEAD or ADMIN), no project guard: the require_role layer alone.
_ROLE_ONLY_WRITES = {
    "create outbound webhook": ("POST", "/api/v1/webhooks"),
    "create project": ("POST", "/api/v1/projects"),
    "create release": ("POST", "/api/v1/releases"),
}


@pytest.mark.parametrize("key", ["stream", "stream_unbound"])
@pytest.mark.parametrize("door", sorted(_ROLE_ONLY_WRITES))
async def test_a_qa_lead_route_refuses_a_stream_scoped_key(world, door, key):
    method, path = _ROLE_ONLY_WRITES[door]

    resp = await world.client.request(method, path, headers=world.headers[key], json={})

    assert resp.status_code == 403, resp.text
    if door == "create project" and key == "stream":
        # Re-audit N26: creating a project is instance-wide, so a key bound to
        # one project is refused on its binding before its scopes are read.
        assert resp.json()["detail"] == PROJECT_KEY_NOT_INSTANCE_ADMIN_DETAIL
    else:
        assert "project:admin" in resp.json()["detail"]


def _guard_only_writes(world):
    """A project-scoped guard, no QA_LEAD+ role: the guard layer alone, per method."""
    a, run = world.project_a, world.run_1
    return {
        "PUT agent policy": ("PUT", f"/api/v1/projects/{a}/agent-policies/r4b-agent"),
        "PUT fixer config": ("PUT", f"/api/v1/projects/{a}/fixer/config"),
        "POST fix outcome": ("POST", f"/api/v1/projects/{a}/fix-outcomes"),
        "POST recover live run": ("POST", f"/api/v1/runs/{run}/recover-live"),
    }


#: ``stream_unbound`` belongs to an ADMIN, whom these guards wave through
#: before any membership check: the refusal must come before that bypass.
@pytest.mark.parametrize("key", ["stream", "stream_unbound"])
@pytest.mark.parametrize(
    "door", ["PUT agent policy", "PUT fixer config", "POST fix outcome", "POST recover live run"]
)
async def test_a_project_guard_refuses_a_stream_scoped_keys_write(world, door, key):
    method, path = _guard_only_writes(world)[door]

    resp = await world.client.request(method, path, headers=world.headers[key], json={})

    assert resp.status_code == 403, resp.text
    assert "project:admin" in resp.json()["detail"]


@pytest.mark.parametrize("key", ["project_admin", "legacy"])
async def test_a_project_guard_lets_an_admin_scoped_or_legacy_key_write(world, key):
    method, path = _guard_only_writes(world)["POST fix outcome"]

    resp = await world.client.request(method, path, headers=world.headers[key], json={})

    # Past authorization: the empty body is what is refused.
    assert resp.status_code == 422, resp.text


# ── QA-R4-2: an UNBOUND stream-scoped key is not an instance administrator ───


async def test_an_unbound_stream_scoped_key_cannot_create_an_admin(world):
    email = f"r4s-created-{world.tag}@example.com"
    resp = await world.client.post(
        "/api/v1/users", headers=world.headers["stream_unbound"],
        json={"email": email, "username": f"r4s_created_{world.tag}", "role": "ADMIN"},
    )

    assert resp.status_code == 403, resp.text
    assert "project:admin" in resp.json()["detail"]
    User = world.models.User
    async with world.sessions() as db:
        created = (await db.execute(select(func.count()).select_from(User).where(User.email == email))).scalar_one()
    assert created == 0


async def test_an_unbound_stream_scoped_key_cannot_use_an_opted_in_route(world):
    resp = await _delete_run(world, world.headers["stream_unbound"], world.run_1)

    assert resp.status_code == 403, resp.text
    assert "project:admin" in resp.json()["detail"]
    assert world.queued == []
    assert await _runs_left(world) == 2


# ── review of QA-R4-1: the doors that check the role in their own body ──────


@pytest.mark.parametrize("key", ["stream", "stream_unbound"])
@pytest.mark.parametrize("query", ["?full=true", "?project_id={a}"])
async def test_a_stream_scoped_key_cannot_queue_a_reindex(world, key, query):
    """The unbound key's owner is an ADMIN: without the scope rule it queued
    a rebuild of every tenant's index."""
    resp = await world.client.post(
        "/api/v1/search/reindex" + query.format(a=world.project_a), headers=world.headers[key]
    )

    assert resp.status_code == 403, resp.text
    assert "project:admin" in resp.json()["detail"]


async def _key_row(world, name):
    ApiKey = world.models.ApiKey
    async with world.sessions() as db:
        return (await db.execute(
            select(ApiKey).where(ApiKey.user_id == world.owner, ApiKey.name == f"team-a {name}")
        )).scalar_one()


async def test_a_stream_scoped_key_cannot_revoke_its_owners_other_keys(world):
    target = await _key_row(world, "project_admin")

    resp = await world.client.delete(f"/api/v1/keys/{target.id}", headers=world.headers["stream"])

    assert resp.status_code == 403, resp.text
    assert "project:admin" in resp.json()["detail"]
    assert (await _key_row(world, "project_admin")).is_active is True


async def test_a_stream_scoped_key_may_revoke_itself(world):
    own = await _key_row(world, "stream")

    resp = await world.client.delete(f"/api/v1/keys/{own.id}", headers=world.headers["stream"])

    assert resp.status_code == 204, resp.text
    assert (await _key_row(world, "stream")).is_active is False


async def test_a_project_admin_key_still_revokes_its_owners_keys(world):
    target = await _key_row(world, "legacy")

    resp = await world.client.delete(f"/api/v1/keys/{target.id}", headers=world.headers["project_admin"])

    assert resp.status_code == 204, resp.text
    assert (await _key_row(world, "legacy")).is_active is False


# ── the routes a CI key ingests through never reach a scope refusal ─────────


#: Every route the SDKs (client/), the CLI (cli/) and /ws/events producers call
#: with an API key to put results in. None of them goes through a guard that
#: refuses a scoped key, which is why there is no ingest allow-list in deps.py:
#: they authenticate with get_api_key_context / get_streaming_api_key_context /
#: an in-handler key lookup instead. This pins that; routing one of them
#: through require_project_access (or require_role(QA_LEAD+)) fails here
#: before it breaks every ["stream:write"] pipeline.
_KEY_INGEST_ROUTES = (
    ("POST", "/api/v1/stream/sessions"),
    ("DELETE", "/api/v1/stream/sessions/{session_id}"),
    ("POST", "/api/v1/stream/events/batch"),
    ("POST", "/api/v1/stream/ingest"),
    ("POST", "/api/v1/ingest"),
    ("POST", "/api/v1/ingest/file"),
    ("GET", "/api/v1/ingest/uploads/{task_id}"),
    ("POST", "/ws/events/{run_id}"),
    ("POST", "/api/v1/keys"),
)

_SCOPE_REFUSING_GUARDS = (
    "require_project_access.", "require_project_role.", "require_run_access.",
    "require_release_access.", "require_knowledge_source_access.",
    "require_generation_batch_access.", "require_live_session_access.",
)


def _scope_refusals(dependant, found):
    call = dependant.call
    qualname = getattr(call, "__qualname__", "") or ""
    if qualname.startswith(_SCOPE_REFUSING_GUARDS):
        found.append(qualname)
    code = getattr(call, "__code__", None)
    if code is not None and call.__closure__:
        cells = dict(zip(code.co_freevars, (c.cell_contents for c in call.__closure__)))
        if cells.get("refuse_scoped_key") is True:
            found.append(qualname)
    for sub in dependant.dependencies:
        _scope_refusals(sub, found)
    return found


async def test_the_ingest_routes_never_reach_a_scope_refusal():
    from fastapi.routing import APIRoute

    from app.main import app

    routes = {
        (method, route.path): route
        for route in app.routes if isinstance(route, APIRoute)
        for method in route.methods
    }
    missing = [key for key in _KEY_INGEST_ROUTES if key not in routes]
    refused = {
        key: _scope_refusals(routes[key].dependant, [])
        for key in _KEY_INGEST_ROUTES if key in routes
    }

    assert missing == []
    assert {key: found for key, found in refused.items() if found} == {}
    # ...and the walker does see a refusal where one exists.
    assert _scope_refusals(routes[("POST", "/api/v1/users")].dependant, [])
    assert _scope_refusals(routes[("PUT", "/api/v1/projects/{project_id}")].dependant, [])
