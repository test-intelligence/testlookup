"""Retention holds on a real Postgres (W22a ACT-05, G-B).

The service against real rows — a run held directly, through its release, as
the baseline of a held run (one hop: the baseline's baseline is NOT held), and
by a project hold; a release clears the next read; an expired hold never
applies; each anti-join arm selects the right runs while a NULL-``entity_id``
project hold sits in the table, beside a ``NOT IN`` version of the same filter
that selects ZERO rows — then every explicit path through the real app with
real JWTs and a real project-bound API key: the role gates, the key refused on
place / release, and the 409 ``Held: …`` of the run delete, the criteria
preview, the release unlink and delete, the pack retire and delete and the
project reset (which writes no attempt row). Last, the D-14 lock conflict: a
purge's ``FOR NO KEY UPDATE`` on the project makes ``place_hold`` (``FOR
SHARE``) wait, so under ``SET LOCAL lock_timeout`` it raises LockNotAvailable,
and succeeds once the purge commits.

The fixture awaits ``dispose_engine_for_loop()`` before building its engine
and after disposing it; it binds its OWN Redis client (patched in as
``get_redis``, closed here) and fakes Mongo and storage, so it closes no
app-global client. Requires ``TESTLOOKUP_POSTGRES_TEST_DSN`` at ``0240`` and
``REDIS_URL``; skips otherwise.
"""
from __future__ import annotations

import hashlib
import os
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import delete, select, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytest.importorskip("asyncpg")

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def _env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        pytest.skip(f"{name} is not configured")
    return value


class _Mongo:
    class _Collection:
        async def count_documents(self, _query):
            return 0

    def __getitem__(self, _name):
        return self._Collection()


class _Storage:
    def __init__(self):
        self.deleted: list[str] = []

    async def delete_object(self, key, **_kw):
        self.deleted.append(key)


@pytest.fixture
async def world(monkeypatch):
    redis_asyncio = pytest.importorskip("redis.asyncio")
    from httpx import ASGITransport, AsyncClient

    from app.core import deps
    from app.core.security import create_access_token
    from app.db import postgres as app_postgres
    from app.db.postgres import get_db
    from app.main import app
    from app.models.postgres import (
        AccessAuditLog, ApiKey, CompliancePack, DeletionJob, LaunchStatus, Project,
        ProjectActivityEvent, ProjectMember, Release, ReleaseTestRunLink, RetentionHold,
        RunBaseline, SettingsAuditLog, TestRun, User, UserRole,
    )

    dsn = _env("TESTLOOKUP_POSTGRES_TEST_DSN")
    await app_postgres.dispose_engine_for_loop()
    engine = create_async_engine(dsn, pool_size=4, max_overflow=0)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    redis = redis_asyncio.Redis.from_url(_env("REDIS_URL"), decode_responses=True)
    storage = _Storage()

    async def _get_db():
        async with sessions() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    previous = app.dependency_overrides.get(get_db)
    app.dependency_overrides[get_db] = _get_db
    monkeypatch.setattr("app.db.redis_client.get_redis", lambda: redis)
    monkeypatch.setattr("app.db.postgres.AsyncSessionLocal", sessions, raising=False)
    monkeypatch.setattr("app.db.mongo.get_mongo_db", lambda: _Mongo())
    monkeypatch.setattr("app.db.storage.get_storage_provider", lambda: storage)

    tag = "t" + uuid.uuid4().hex[:9]  # starts with a letter: never read as a phone number
    now = datetime.now(timezone.utc)
    P, Q = uuid.uuid4(), uuid.uuid4()
    admin, lead, tester = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    runs = {name: uuid.uuid4() for name in ("free", "held", "rel", "base", "basebase")}
    rel, q_rel, pack = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    raw_key = f"qai_{secrets.token_urlsafe(32)}"
    async with sessions.begin() as db:
        db.add(Project(id=P, name=f"Holds {tag}", slug=f"holds-{tag}", is_active=True))
        db.add(Project(id=Q, name=f"Other {tag}", slug=f"other-{tag}", is_active=True))
        for uid, label, role in ((admin, "admin", UserRole.ADMIN), (lead, "lead", UserRole.QA_LEAD),
                                 (tester, "tester", UserRole.TESTER)):
            db.add(User(id=uid, email=f"holds-{label}-{tag}@example.com", username=f"holds_{label}_{tag}",
                        full_name=f"Holds {label}", hashed_password="!unusable", role=role.value))
        await db.flush()
        db.add(ProjectMember(project_id=P, user_id=lead, role=UserRole.QA_LEAD.value))
        db.add(ProjectMember(project_id=P, user_id=tester, role=UserRole.TESTER.value))
        for index, (name, rid) in enumerate(runs.items()):
            db.add(TestRun(
                id=rid, project_id=P, build_number=f"{tag}-{name}", jenkins_job="holds",
                status=LaunchStatus.FAILED, ingestion_source="unknown", total_tests=1, failed_tests=1,
                created_at=now - timedelta(days=50 - index),
            ))
        db.add(Release(id=rel, project_id=P, name=f"2026.10-{tag}", status="in_progress"))
        db.add(Release(id=q_rel, project_id=Q, name=f"q-{tag}", status="in_progress"))
        db.add(ApiKey(
            id=uuid.uuid4(), user_id=admin, name=f"holds ci {tag}",
            key_hash=hashlib.sha256(raw_key.encode()).hexdigest(), key_hint=raw_key[:8] + "...",
            scopes=[], project_id=P,
        ))
        await db.flush()
        db.add(ReleaseTestRunLink(release_id=rel, test_run_id=runs["rel"], is_primary=True))
        for run_key, base_key in (("held", "base"), ("base", "basebase")):
            db.add(RunBaseline(id=uuid.uuid4(), run_id=runs[run_key], baseline_run_id=runs[base_key],
                               baseline_build_number=f"{tag}-{base_key}", selection_reason="newest_passed",
                               classification="same"))
        db.add(CompliancePack(id=pack, release_id=rel, project_id=P, minio_key=f"packs/{tag}.zip",
                              manifest_sha256="0" * 64, retention_expires_at=now - timedelta(days=1)))
    for uid in (admin, lead, tester):
        await deps.invalidate_membership_cache(uid)

    http = AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")
    try:
        yield SimpleNamespace(
            sessions=sessions, engine=engine, http=http, tag=tag, now=now, P=P, Q=Q, runs=runs,
            rel=rel, q_rel=q_rel, pack=pack, storage=storage, users={"admin": admin, "lead": lead},
            auth={label: {"Authorization": f"Bearer {create_access_token(str(uid))}"}
                  for label, uid in (("admin", admin), ("lead", lead), ("tester", tester))},
            key={"X-API-Key": raw_key},
        )
    finally:
        await http.aclose()
        if previous is None:
            app.dependency_overrides.pop(get_db, None)
        else:
            app.dependency_overrides[get_db] = previous
        for statement in (
            delete(ApiKey).where(ApiKey.user_id == admin),
            delete(RetentionHold).where(RetentionHold.project_id.in_([P, Q])),
            delete(DeletionJob).where(DeletionJob.project_id.in_([P, Q])),
            delete(ProjectActivityEvent).where(ProjectActivityEvent.project_id.in_([P, Q])),
            delete(CompliancePack).where(CompliancePack.project_id.in_([P, Q])),
            delete(TestRun).where(TestRun.project_id.in_([P, Q])),
            delete(Release).where(Release.project_id.in_([P, Q])),
            delete(SettingsAuditLog).where(SettingsAuditLog.setting_key.in_([f"retention_hold:{P}", f"retention_hold:{Q}"])),
            delete(AccessAuditLog).where(AccessAuditLog.actor_user_id.in_([admin, lead, tester])),
            delete(Project).where(Project.id.in_([P, Q])),
            delete(User).where(User.id.in_([admin, lead, tester])),
        ):
            try:
                async with sessions.begin() as db:
                    await db.execute(statement)
            except Exception as exc:  # noqa: BLE001 -- report, keep cleaning
                print(f"holds teardown: {type(exc).__name__}: {str(exc)[:160]}")
        for uid in (admin, lead, tester):
            try:
                await deps.invalidate_membership_cache(uid)
            except Exception:  # noqa: BLE001
                pass
        await redis.aclose()
        await engine.dispose()
        await app_postgres.dispose_engine_for_loop()


def _admin(w):
    return SimpleNamespace(id=w.users["admin"], full_name="Holds admin", username="holds_admin")


async def _place(w, entity_type, entity_id, reason, *, expires_at=None):
    from app.services import retention_hold_service as holds

    async with w.sessions.begin() as db:
        hold = await holds.place_hold(
            db, project_id=w.P, entity_type=entity_type, entity_id=entity_id, reason=reason,
            expires_at=expires_at, actor=_admin(w), now=datetime.now(timezone.utc),
        )
        return hold.id


async def _release(w, hold_id, reason="Closed sentinel"):
    from app.services import retention_hold_service as holds

    async with w.sessions.begin() as db:
        await holds.release_hold(
            db, project_id=w.P, hold_id=hold_id, reason=reason, actor=_admin(w), now=datetime.now(timezone.utc),
        )


async def _blockers(w, name):
    from app.services import run_deletion_service

    async with w.sessions() as db:
        return await run_deletion_service.citation_blockers(db, run_id=w.runs[name], mongo=_Mongo())


# ── the service against real rows ────────────────────────────────────────────


async def test_citation_blockers_read_every_pin_one_hop_and_a_release_clears_it(world):
    w = world
    await _place(w, "run", w.runs["held"], "Run hold sentinel")
    await _place(w, "release", w.rel, "Audit Q4 sentinel")

    assert await _blockers(w, "held") == ["Held: Run hold sentinel"]
    rel = await _blockers(w, "rel")
    assert rel[0] == "Held: Audit Q4 sentinel" and rel[1].startswith("linked to 1 release(s)")
    assert await _blockers(w, "base") == ["Held: Run hold sentinel"], "the baseline of a held run is held"
    assert await _blockers(w, "basebase") == [], "ONE hop: the baseline's baseline is not"
    assert await _blockers(w, "free") == []

    project_hold = await _place(w, "project", None, "Litigation sentinel")
    assert await _blockers(w, "free") == ["Held: Litigation sentinel"]
    assert await _blockers(w, "basebase") == ["Held: Litigation sentinel"]

    await _release(w, project_hold)
    assert await _blockers(w, "free") == [], "a released hold stops applying on the next read"


async def test_an_expired_hold_never_applies(world):
    from app.models.postgres import RetentionHold
    from app.services import retention_hold_service as holds

    w = world
    now = datetime.now(timezone.utc)
    async with w.sessions.begin() as db:  # inserted directly: place_hold refuses a past expiry
        db.add(RetentionHold(
            id=uuid.uuid4(), project_id=w.P, entity_type="run", entity_id=w.runs["free"],
            entity_label="Build x", reason="Expired sentinel", expires_at=now - timedelta(minutes=1),
        ))
        db.add(RetentionHold(
            id=uuid.uuid4(), project_id=w.P, entity_type="project", entity_id=None,
            entity_label="the whole project", reason="Expired project sentinel", expires_at=now - timedelta(minutes=1),
        ))
    assert await _blockers(w, "free") == []
    async with w.sessions() as db:
        assert await holds.held_run_ids(db, project_id=w.P, now=now) == set()
        live, live_total = await holds.list_holds(db, project_id=w.P, state="live", now=now)
        expired, expired_total = await holds.list_holds(db, project_id=w.P, state="expired", now=now)
        counts = await holds.count_holds_by_state(db, project_id=w.P, now=now)
    assert (live, live_total, expired_total) == ([], 0, 2)
    assert {h.reason for h in expired} == {"Expired sentinel", "Expired project sentinel"}
    assert counts == {"live": 0, "expired": 2, "released": 0}


async def test_each_arm_is_an_anti_join_and_not_in_would_select_nothing(world):
    """The `NOT IN` trap: a project hold's NULL entity_id in the list makes
    `id NOT IN (…, NULL)` NULL for every row, so a purge filtered that way
    would delete nothing while reporting success."""
    from app.models.postgres import RetentionHold, TestRun
    from app.services import retention_hold_service as holds

    w = world
    run_hold = await _place(w, "run", w.runs["held"], "Run hold sentinel")
    rel_hold = await _place(w, "release", w.rel, "Audit Q4 sentinel")
    project_hold = await _place(w, "project", None, "Litigation sentinel")
    every = set(w.runs.values())
    r = w.runs
    now = datetime.now(timezone.utc)

    async with w.sessions() as db:
        async def ids(*where):
            rows = (await db.execute(select(TestRun.id).where(TestRun.project_id == w.P, *where))).all()
            return {row[0] for row in rows}

        direct, via_release, as_baseline = holds._run_hold_arms(
            lambda col: col == TestRun.id, w.P, now=now, tag="t"
        )
        assert await ids(holds.project_not_held(w.P, now=now)) == set()
        assert await ids(~direct) == every - {r["held"]}
        assert await ids(~via_release) == every - {r["rel"]}
        assert await ids(~as_baseline) == every - {r["base"]}
        naive = TestRun.id.not_in(
            select(RetentionHold.entity_id).where(RetentionHold.project_id == w.P, holds.live_clause(now))
        )
        assert await ids(naive) == set(), "NOT IN over a list holding NULL matches nothing"
        assert await ids(holds.run_not_held(TestRun.id, w.P, now=now)) == set()
        assert await holds.held_run_ids(db, project_id=w.P, now=now) == every
        pinned = await holds.pinned_runs_before(db, project_id=w.P, created_before=now + timedelta(days=1), now=now)
        assert pinned == {run_hold: {r["held"], r["base"]}, rel_hold: {r["rel"]}, project_hold: every}
        packs = await holds.pinned_expired_packs(db, project_id=w.P, now=now)
        assert packs == {rel_hold: {w.pack}, project_hold: {w.pack}}
        covering = await holds.holds_covering_runs(db, project_id=w.P, run_ids=list(every), now=now)
        assert {h.id for h in covering[r["base"]]} == {run_hold, project_hold}
        assert {h.id for h in covering[r["basebase"]]} == {project_hold}

    await _release(w, project_hold)
    async with w.sessions() as db:
        rows = (await db.execute(
            select(TestRun.id).where(TestRun.project_id == w.P, holds.run_not_held(TestRun.id, w.P, now=now))
        )).all()
        assert {row[0] for row in rows} == {r["free"], r["basebase"]}, "exactly the unheld runs"
        by_pack = await holds.holds_covering_packs(
            db, project_id=w.P, release_id=w.rel, pack_ids=[w.pack], now=now
        )
        assert [h.id for h in by_pack[w.pack]] == [rel_hold]


# ── the routes, with real JWTs and a real project-bound key ──────────────────


async def test_role_gates_and_the_bound_key_on_the_real_app(world):
    from app.models.postgres import ProjectActivityEvent, SettingsAuditLog

    w = world
    url = f"/api/v1/projects/{w.P}/retention-holds"
    body = {"entity_type": "release", "entity_id": str(w.rel), "reason": "Audit Q4 sentinel"}

    resp = await w.http.post(url, json=body, headers=w.auth["tester"])
    assert resp.status_code == 403 and resp.json()["detail"] == "Requires at least QA_LEAD role"
    resp = await w.http.post(url, json=body, headers=w.key)
    assert resp.status_code == 403, "a project-bound key can never place a hold"
    foreign = {**body, "entity_id": str(w.q_rel)}
    resp = await w.http.post(url, json=foreign, headers=w.auth["lead"])
    assert resp.status_code == 404 and resp.json()["detail"]["code"] == "entity_not_found"

    resp = await w.http.post(url, json=body, headers=w.auth["lead"])
    assert resp.status_code == 201, resp.text
    hold = resp.json()
    assert (hold["state"], hold["entity_label"]) == ("live", f"release 2026.10-{w.tag}")

    async with w.sessions() as db:
        events = (await db.execute(
            select(ProjectActivityEvent.event_type, ProjectActivityEvent.entity_id)
            .where(ProjectActivityEvent.project_id == w.P)
        )).all()
        audits = (await db.execute(
            select(SettingsAuditLog.action).where(SettingsAuditLog.setting_key == f"retention_hold:{w.P}")
        )).scalars().all()
    assert ("retention_hold.placed", hold["id"]) in [(e[0], e[1]) for e in events]
    assert audits == ["hold_placed"]

    listed = await w.http.get(url, headers=w.key)
    assert listed.status_code == 200 and listed.json()["total"] == 1, "a bound key may read"

    release_url = f"{url}/{hold['id']}/release"
    resp = await w.http.post(release_url, json={"reason": "Audit closed"}, headers=w.key)
    assert resp.status_code == 403, "a project-bound key can never release a hold"
    resp = await w.http.post(release_url, json={"reason": "Audit closed"}, headers=w.auth["lead"])
    assert resp.status_code == 403 and resp.json()["detail"] == "Requires at least ADMIN role"
    other = f"/api/v1/projects/{w.Q}/retention-holds/{hold['id']}/release"
    resp = await w.http.post(other, json={"reason": "Audit closed"}, headers=w.auth["admin"])
    assert resp.status_code == 404 and resp.json()["detail"]["code"] == "hold_not_found"
    resp = await w.http.post(release_url, json={"reason": "Audit closed"}, headers=w.auth["admin"])
    assert resp.status_code == 200 and resp.json()["state"] == "released"
    resp = await w.http.post(release_url, json={"reason": "Again"}, headers=w.auth["admin"])
    assert resp.status_code == 409 and resp.json()["detail"]["code"] == "hold_not_live"


async def test_every_explicit_path_refuses_a_held_release_then_works_once_released(world):
    from app.models.postgres import ProjectActivityEvent, ReleaseTestRunLink, TestRun

    w = world
    resp = await w.http.post(
        f"/api/v1/projects/{w.P}/retention-holds",
        json={"entity_type": "release", "entity_id": str(w.rel), "reason": "Audit Q4 sentinel"},
        headers=w.auth["lead"],
    )
    assert resp.status_code == 201, resp.text
    hold_id = resp.json()["id"]
    held = "Held: Audit Q4 sentinel"
    run = w.runs["rel"]

    for headers in (w.auth["admin"], w.key):
        resp = await w.http.request(
            "DELETE", f"/api/v1/runs/{run}", json={"confirm": True, "reason": "cleanup"}, headers=headers,
        )
        assert resp.status_code == 409, resp.text
        assert resp.json()["detail"]["blockers"][0] == held

    preview = await w.http.post(
        f"/api/v1/projects/{w.P}/deletion/preview", json={"run_ids": [str(run)]}, headers=w.auth["admin"],
    )
    assert preview.status_code == 200, preview.text
    assert preview.json()["blocked"][0]["reasons"][0] == held
    assert preview.json()["run_count"] == 0 and preview.json()["floor_applies"] is False

    checks = [
        ("DELETE", f"/api/v1/releases/{w.rel}/test-runs/{run}", None),
        ("DELETE", f"/api/v1/releases/{w.rel}", None),
        ("POST", f"/api/v1/compliance-packs/{w.pack}/retire", {"reason": "early", "confirmation_id": str(w.pack)}),
        ("DELETE", f"/api/v1/compliance-packs/{w.pack}", None),
        ("POST", f"/api/v1/projects/{w.P}/reset", {"mode": "runs", "confirmation_name": f"Holds {w.tag}"}),
    ]
    for method, url, body in checks:
        resp = await w.http.request(method, url, json=body, headers=w.auth["admin"])
        assert resp.status_code == 409, f"{method} {url}: {resp.status_code} {resp.text}"
        assert resp.json()["detail"]["blockers"][0] == held, url
    assert w.storage.deleted == []

    detail = await w.http.get(f"/api/v1/releases/{w.rel}", headers=w.auth["lead"])
    assert [h["reason"] for h in detail.json()["holds"]] == ["Audit Q4 sentinel"]
    packs = await w.http.get(f"/api/v1/releases/{w.rel}/compliance-packs", headers=w.auth["lead"])
    assert [[h["hold_id"] for h in p["holds"]] for p in packs.json()] == [[hold_id]]

    async with w.sessions() as db:
        assert (await db.scalar(select(TestRun.id).where(TestRun.id == run))) == run
        assert (await db.scalar(select(ReleaseTestRunLink.id).where(ReleaseTestRunLink.test_run_id == run)))
        resets = (await db.execute(
            select(ProjectActivityEvent.id).where(
                ProjectActivityEvent.project_id == w.P, ProjectActivityEvent.event_type == "project.reset"
            )
        )).all()
    assert resets == [], "a refused reset leaves no 'reset was issued' row"

    resp = await w.http.post(
        f"/api/v1/projects/{w.P}/retention-holds/{hold_id}/release",
        json={"reason": "Audit closed"}, headers=w.auth["admin"],
    )
    assert resp.status_code == 200
    resp = await w.http.request("DELETE", f"/api/v1/releases/{w.rel}/test-runs/{run}", headers=w.auth["admin"])
    assert resp.status_code == 204, resp.text
    resp = await w.http.request("DELETE", f"/api/v1/compliance-packs/{w.pack}", headers=w.auth["admin"])
    assert resp.status_code == 204, resp.text
    assert w.storage.deleted == [f"packs/{w.tag}.zip"]


# ── D-14: a hold placed during a purge waits for it ──────────────────────────


async def test_place_hold_waits_on_the_purge_lock_then_succeeds(world):
    from sqlalchemy.exc import DBAPIError

    from app.models.postgres import Project
    from app.services import retention_hold_service as holds

    w = world
    lock = select(Project.id).where(Project.id == w.P).with_for_update(key_share=True)
    assert "FOR NO KEY UPDATE" in str(lock.compile(dialect=postgresql.dialect())), "the purge's lock (D-14)"
    async with w.sessions() as purge:
        await purge.execute(lock)
        async with w.sessions() as placer:
            await placer.execute(text("SET LOCAL lock_timeout = '200ms'"))
            with pytest.raises(DBAPIError) as caught:
                await holds.place_hold(
                    placer, project_id=w.P, entity_type="project", entity_id=None, reason="Race sentinel",
                    expires_at=None, actor=_admin(w), now=datetime.now(timezone.utc),
                )
            assert "LockNotAvailable" in str(caught.value)
            await placer.rollback()
        await purge.commit()

    hold_id = await _place(w, "project", None, "Race sentinel")
    async with w.sessions() as db:
        assert [h.id for h in await holds.live_holds(db, w.P, now=datetime.now(timezone.utc))] == [hold_id]
