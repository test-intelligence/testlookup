"""W31 STRAT-12: the erasure routes (``routers/subject_erasure.py``; contract §3.1, §6.3 G-C).

The real router on a bare FastAPI app over SQLite (the W24 route-test pattern:
``get_db`` / ``get_current_active_user`` overridden, ``require_role`` and the
session-only dependency real). ADMIN-only; ``session_required`` for every API-key
principal; ``user_not_found``; ``self_erasure``; the typed confirm; the lock-out
is ONE commit holding the job, the token revocation, the session / key lock-out
and the audit row — then the cache drop and the enqueue; the resume re-queues
the SAME job with no second lock-out; the preview writes nothing.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from app.core.deps import _bind_api_key_project, get_current_active_user
from app.db.postgres import get_db
from app.models.postgres import AccessAuditLog, Base, SubjectErasureRequest, SubjectRightsJob, User, UserRole
from app.routers import subject_erasure as routes
from app.services import subject_erasure_service as svc
from app.services.subject_rights_vocab import ERROR_SENTENCES
from tests.test_subject_erasure_service import _api_key, _job, _user
from tests.test_subject_erasure_service import maker as _sqlite_maker
from tests.test_subject_erasure_service import world as _sqlite_world

#: The SQLite world of test_subject_erasure_service.py (users, the job tables, every inventory table SQLite
#: can build), re-registered here under the same fixture names.
maker = pytest.fixture(name="maker")(_sqlite_maker.__wrapped__)  # type: ignore[attr-defined]
world = pytest.fixture(name="world")(_sqlite_world.__wrapped__)  # type: ignore[attr-defined]

API = "/api/v1/users"


def _detail(code: str) -> dict[str, str]:
    return {"code": code, "message": ERROR_SENTENCES[code]}


@pytest.fixture
def events(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    """The order of the side effects: the revocation, each handler commit, the cache drop, the enqueue."""
    seen: list[Any] = []

    async def _revoke(user_id: uuid.UUID, db: Any = None) -> None:
        seen.append(("revoke", user_id, db is not None))

    async def _invalidate(user_id: uuid.UUID) -> None:
        seen.append(("membership", user_id))

    monkeypatch.setattr("app.core.token_revocation.revoke_all_user_tokens", _revoke)
    monkeypatch.setattr(routes, "invalidate_membership_cache", _invalidate)
    monkeypatch.setattr(routes, "_dispatch", lambda job_id: seen.append(("dispatch", str(job_id))))
    return seen


def _client(maker: Any, user: Any, events: list[Any]) -> AsyncClient:
    app = FastAPI()
    app.include_router(routes.router)

    async def _db() -> Any:
        async with maker.session() as session:
            original = session.commit

            async def _counted() -> None:
                events.append(("commit",))
                await original()

            session.commit = _counted  # type: ignore[method-assign]
            try:
                yield session
                await original()   # get_db's own end-of-request commit (not counted)
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_current_active_user] = lambda: user
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _count(maker: Any, model: Any, *where: Any) -> int:
    async with maker.session() as db:
        return int((await db.execute(select(func.count()).select_from(model).where(*where))).scalar() or 0)


async def _seed_sessions_and_keys(maker: Any, subject: User) -> None:
    async with maker.session() as db:
        await db.execute(Base.metadata.tables["refresh_token_records"].insert().values(
            id=uuid.uuid4(), user_id=subject.id, jti_hash="j" * 64,
            issued_at=datetime.now(timezone.utc), expires_at=datetime.now(timezone.utc)))
        await db.execute(Base.metadata.tables["api_keys"].insert().values(
            id=uuid.uuid4(), user_id=subject.id, name="ci", key_hash="h" * 20, key_hint="tl_zz", scopes=[],
            is_active=True))
        await db.commit()


# ── who may call ─────────────────────────────────────────────────────────────

async def test_both_routes_are_admin_only(maker: Any, world: SimpleNamespace, events: list[Any]) -> None:
    lead = _user(UserRole.QA_LEAD, "zqxwqalead")
    async with _client(maker, lead, events) as client:
        preview = await client.get(f"{API}/{world.subject.id}/erasure-preview")
        erase = await client.post(f"{API}/{world.subject.id}/erasure", json={"confirm_username": "zqxwsubj"})
    assert preview.status_code == 403 and erase.status_code == 403
    assert await _count(maker, SubjectRightsJob) == 0
    async with _client(maker, world.admin, events) as client:
        ok = await client.get(f"{API}/{world.subject.id}/erasure-preview")
    assert ok.status_code == 200
    assert ok.json()["username"] == "zqxwsubj" and ok.json()["blockers"] == []


@pytest.mark.parametrize("kind", ["scoped", "bound"])
async def test_every_api_key_is_session_required_before_any_id(
    maker: Any, world: SimpleNamespace, events: list[Any], kind: str,
) -> None:
    key_user = _api_key(_user(UserRole.ADMIN, f"zqxwkey{kind}"))
    if kind == "bound":
        _bind_api_key_project(key_user, uuid.uuid4())
    unknown = uuid.uuid4()
    async with _client(maker, key_user, events) as client:
        for path in (f"{API}/{world.subject.id}/erasure-preview", f"{API}/{unknown}/erasure-preview"):
            response = await client.get(path)
            assert response.status_code == 403 and response.json()["detail"] == _detail("session_required")
        response = await client.post(f"{API}/{unknown}/erasure", json={"confirm_username": "x"})
        assert response.status_code == 403 and response.json()["detail"] == _detail("session_required")
    assert await _count(maker, SubjectRightsJob) == 0 and events == []


async def test_unknown_self_and_service_targets_are_refused(
    maker: Any, world: SimpleNamespace, events: list[Any],
) -> None:
    async with _client(maker, world.admin, events) as client:
        missing = await client.post(f"{API}/{uuid.uuid4()}/erasure", json={"confirm_username": "x"})
        missing_preview = await client.get(f"{API}/{uuid.uuid4()}/erasure-preview")
        own = await client.post(f"{API}/{world.admin.id}/erasure", json={"confirm_username": "zqxwadmin"})
        bot = await client.post(f"{API}/{world.bot.id}/erasure", json={"confirm_username": "zqxwbot"})
        bot_preview = await client.get(f"{API}/{world.bot.id}/erasure-preview")
    assert missing.status_code == 404 and missing.json()["detail"] == _detail("user_not_found")
    assert missing_preview.status_code == 404 and missing_preview.json()["detail"] == _detail("user_not_found")
    assert own.status_code == 409 and own.json()["detail"] == _detail("self_erasure")
    assert bot.status_code == 409 and bot.json()["detail"] == _detail("not_a_person")
    assert [blocker["code"] for blocker in bot_preview.json()["blockers"]] == ["not_a_person"]
    assert await _count(maker, SubjectRightsJob) == 0 and await _count(maker, AccessAuditLog) == 0
    assert events == []


async def test_the_typed_confirm_is_exact(maker: Any, world: SimpleNamespace, events: list[Any]) -> None:
    async with _client(maker, world.admin, events) as client:
        for typed in ("ZQXWSUBJ", "zqxwsubj ", "zqxwsub"):
            response = await client.post(f"{API}/{world.subject.id}/erasure", json={"confirm_username": typed})
            assert response.status_code == 422 and response.json()["detail"] == _detail("confirm_mismatch")
    async with maker.session() as db:
        assert (await db.get(User, world.subject.id)).is_active is True
    assert await _count(maker, SubjectRightsJob) == 0 and await _count(maker, AccessAuditLog) == 0
    assert events == []


# ── the lock-out: ONE commit, then the cache drop and the enqueue ────────────

async def test_the_lockout_is_one_commit_then_the_enqueue(maker: Any, world: SimpleNamespace,
                                                          events: list[Any]) -> None:
    subject = world.subject
    await _seed_sessions_and_keys(maker, subject)
    async with _client(maker, world.admin, events) as client:
        response = await client.post(f"{API}/{subject.id}/erasure", json={"confirm_username": "zqxwsubj"})
    assert response.status_code == 202, response.text
    body = response.json()
    assert body["kind"] == "erasure" and body["status"] == "queued" and body["phase"] == "lockout"
    assert body["subject_user_id"] == str(subject.id) and body["requested_by_display"] == "Zqxwadmin"
    assert body["counts"] == {"refresh_token_records.user_id": 1}
    # the revocation ran on the request's session, then ONE commit, then the cache drop and the enqueue
    assert events == [("revoke", subject.id, True), ("commit",), ("membership", subject.id), ("dispatch", body["id"])]
    async with maker.session() as db:
        row = await db.get(User, subject.id)
        assert row.is_active is False and row.erased_at is None and row.username == "zqxwsubj"
        sessions = Base.metadata.tables["refresh_token_records"]
        keys = Base.metadata.tables["api_keys"]
        assert (await db.execute(select(func.count()).select_from(sessions))).scalar() == 0
        assert (await db.execute(select(keys.c.is_active))).scalars().all() == [False]
        audit = (await db.execute(select(AccessAuditLog))).scalars().all()
    assert len(audit) == 1
    assert audit[0].action == "subject_erasure_requested" and audit[0].actor_user_id == world.admin.id
    assert audit[0].actor_name == "zqxwadmin" and audit[0].target_user_id == subject.id
    assert audit[0].before_value == {"is_active": True} and audit[0].after_value == {"job_id": body["id"]}


async def test_a_second_post_while_queued_is_erasure_running(maker: Any, world: SimpleNamespace,
                                                             events: list[Any]) -> None:
    async with _client(maker, world.admin, events) as client:
        first = await client.post(f"{API}/{world.subject.id}/erasure", json={"confirm_username": "zqxwsubj"})
        events.clear()
        second = await client.post(f"{API}/{world.subject.id}/erasure", json={"confirm_username": "zqxwsubj"})
    assert first.status_code == 202
    assert second.status_code == 409 and second.json()["detail"] == _detail("erasure_running")
    assert events == [] and await _count(maker, AccessAuditLog) == 1


async def test_an_incomplete_erasure_is_resumed_not_repeated(maker: Any, world: SimpleNamespace,
                                                             events: list[Any]) -> None:
    """The Postgres phase committed (the row reads the pseudonym), the object phase stopped: the admin
    types the CURRENT username (the pseudonym) and the SAME job is re-queued — no second lock-out."""
    subject = world.subject
    pseudonym = svc.pseudonym_for(subject.id)
    async with maker.session() as db:
        job = _job(subject, world.admin, kind="erasure", status="running", error_code="erasure_incomplete")
        job.phase = "postgres"
        db.add(job)
        row = await db.get(User, subject.id)
        row.username, row.is_active, row.erased_at = pseudonym, False, datetime.now(timezone.utc)
        await db.commit()
        job_id = job.id
    async with _client(maker, world.admin, events) as client:
        old_name = await client.post(f"{API}/{subject.id}/erasure", json={"confirm_username": "zqxwsubj"})
        preview = await client.get(f"{API}/{subject.id}/erasure-preview")
        resumed = await client.post(f"{API}/{subject.id}/erasure", json={"confirm_username": pseudonym})
    assert old_name.status_code == 422 and old_name.json()["detail"] == _detail("confirm_mismatch")
    assert preview.json()["blockers"] == [] and preview.json()["erasure_job"]["id"] == str(job_id)
    assert preview.json()["display"] == "Erased user" and preview.json()["pseudonym"] == pseudonym
    assert resumed.status_code == 202 and resumed.json()["id"] == str(job_id)
    assert resumed.json()["status"] == "queued" and resumed.json()["phase"] == "postgres"
    assert events == [("commit",), ("membership", subject.id), ("dispatch", str(job_id))]
    assert await _count(maker, SubjectRightsJob) == 1 and await _count(maker, AccessAuditLog) == 0


async def test_erased_and_exporting_targets_are_refused(maker: Any, world: SimpleNamespace,
                                                        events: list[Any]) -> None:
    other = _user(UserRole.TESTER, "zqxwother")
    async with maker.session() as db:
        db.add(other)
        db.add(_job(other, world.admin, kind="export", status="running"))
        row = await db.get(User, world.subject.id)
        row.erased_at = datetime.now(timezone.utc)
        await db.commit()
    async with _client(maker, world.admin, events) as client:
        erased = await client.post(f"{API}/{world.subject.id}/erasure", json={"confirm_username": "zqxwsubj"})
        exporting = await client.post(f"{API}/{other.id}/erasure", json={"confirm_username": "zqxwother"})
    assert erased.status_code == 409 and erased.json()["detail"] == _detail("user_erased")
    assert exporting.status_code == 409 and exporting.json()["detail"] == _detail("export_blocks_erasure")
    assert events == [] and await _count(maker, AccessAuditLog) == 0


async def test_the_preview_writes_nothing_through_the_route(maker: Any, world: SimpleNamespace,
                                                            events: list[Any]) -> None:
    await _seed_sessions_and_keys(maker, world.subject)
    async with _client(maker, world.admin, events) as client:
        response = await client.get(f"{API}/{world.subject.id}/erasure-preview")
        # W38: the person's pending erasure request is shown (still read-only); none → null
        assert response.json()["pending_request"] is None
        request_id = await _seed_request(maker, world.subject)
        asked = await client.get(f"{API}/{world.subject.id}/erasure-preview")
    assert response.status_code == 200 and asked.status_code == 200
    assert asked.json()["pending_request"]["id"] == str(request_id)
    assert asked.json()["pending_request"]["requested_at"].startswith("2026-11-01T09:00:00")
    changes = {entry["key"]: entry for entry in response.json()["changes"]}
    assert changes["api_keys.user_id"]["count"] == 1 and changes["refresh_token_records.user_id"]["count"] == 1
    assert events == []
    assert await _count(maker, SubjectRightsJob) == 0 and await _count(maker, AccessAuditLog) == 0
    async with maker.session() as db:
        assert (await db.get(User, world.subject.id)).is_active is True
        request = await db.get(SubjectErasureRequest, request_id)
        assert (request.status, request.decided_at, request.erasure_job_id) == ("pending", None, None)


# ── W38 D-5: any erasure closes the person's pending request, in the SAME commit ──

async def _seed_request(maker: Any, subject: User, *, status: str = "pending") -> uuid.UUID:
    async with maker.session() as db:
        request = SubjectErasureRequest(
            id=uuid.uuid4(), subject_user_id=subject.id, status=status,
            requested_at=datetime(2026, 11, 1, 9, 0, tzinfo=timezone.utc),
            decided_at=None if status == "pending" else datetime(2026, 11, 2, tzinfo=timezone.utc),
        )
        db.add(request)
        await db.commit()
        return request.id


async def test_the_erasure_accepts_the_pending_request_in_its_one_commit(
    maker: Any, world: SimpleNamespace, events: list[Any],
) -> None:
    subject = world.subject
    request_id = await _seed_request(maker, subject)
    async with _client(maker, world.admin, events) as client:
        response = await client.post(f"{API}/{subject.id}/erasure", json={"confirm_username": "zqxwsubj"})
    assert response.status_code == 202, response.text
    body = response.json()
    # still ONE commit, then the cache drop and the enqueue
    assert events == [("revoke", subject.id, True), ("commit",), ("membership", subject.id), ("dispatch", body["id"])]
    async with maker.session() as db:
        request = await db.get(SubjectErasureRequest, request_id)
        assert request.status == "accepted" and str(request.erasure_job_id) == body["id"]
        assert request.decided_by_id == world.admin.id and request.decided_at is not None
        assert request.decline_code is None
        audit = (await db.execute(select(AccessAuditLog))).scalars().all()
    # the request's acceptance is the erasure's own audit row (no second row), naming the request (W38 §1.2)
    assert [row.action for row in audit] == ["subject_erasure_requested"]
    assert audit[0].after_value == {"job_id": body["id"], "request_id": str(request_id)}


async def test_an_erasure_closes_nothing_when_no_request_is_pending(
    maker: Any, world: SimpleNamespace, events: list[Any],
) -> None:
    withdrawn = await _seed_request(maker, world.subject, status="withdrawn")
    async with _client(maker, world.admin, events) as client:
        response = await client.post(f"{API}/{world.subject.id}/erasure", json={"confirm_username": "zqxwsubj"})
    assert response.status_code == 202
    async with maker.session() as db:
        request = await db.get(SubjectErasureRequest, withdrawn)
        assert request.status == "withdrawn" and request.erasure_job_id is None and request.decided_by_id is None


async def test_a_refused_erasure_leaves_the_request_pending(maker: Any, world: SimpleNamespace,
                                                            events: list[Any]) -> None:
    request_id = await _seed_request(maker, world.subject)
    async with _client(maker, world.admin, events) as client:
        wrong = await client.post(f"{API}/{world.subject.id}/erasure", json={"confirm_username": "nope"})
    assert wrong.status_code == 422 and events == []
    async with maker.session() as db:
        assert (await db.get(SubjectErasureRequest, request_id)).status == "pending"


async def test_a_resume_closes_nothing_new(maker: Any, world: SimpleNamespace, events: list[Any]) -> None:
    subject = world.subject
    async with maker.session() as db:
        job = _job(subject, world.admin, kind="erasure", status="running", error_code="erasure_incomplete")
        job.phase = "lockout"
        db.add(job)
        row = await db.get(User, subject.id)
        row.is_active = False
        await db.commit()
    request_id = await _seed_request(maker, subject)
    async with _client(maker, world.admin, events) as client:
        resumed = await client.post(f"{API}/{subject.id}/erasure", json={"confirm_username": "zqxwsubj"})
    assert resumed.status_code == 202 and resumed.json()["id"] == str(job.id)
    async with maker.session() as db:
        request = await db.get(SubjectErasureRequest, request_id)
        assert request.status == "pending" and request.erasure_job_id is None


# ── the runner: claim, phases each committed, incomplete keeps running, resume ──

@pytest.fixture
def phases(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """The three phase functions replaced by recorders (the real ones are proven on Postgres by
    tests/integration/test_subject_erasure_postgres.py); ``fail`` names a phase that raises once."""
    state = SimpleNamespace(calls=[], fail=set())

    async def _postgres(db: Any, *, job_id: uuid.UUID, now: datetime) -> dict[str, int]:
        state.calls.append("postgres")
        if "postgres" in state.fail:
            state.fail.discard("postgres")
            raise RuntimeError("boom")
        return {"api_keys.user_id": 2, "users.*": 1}

    async def _objects(db: Any, storage: Any, *, job_id: uuid.UUID) -> dict[str, int]:
        state.calls.append("objects")
        if "objects" in state.fail:
            state.fail.discard("objects")
            raise OSError("storage down")
        return {"objects:subject-exports/": 1, "objects:project-exports/": 0}

    async def _redis(*, user_id: uuid.UUID, redis: Any = None) -> dict[str, int]:
        state.calls.append("redis")
        return {"redis:membership:{id}": 1}

    monkeypatch.setattr(svc, "run_postgres_phase", _postgres)
    monkeypatch.setattr(svc, "run_object_phase", _objects)
    monkeypatch.setattr(svc, "run_redis_phase", _redis)
    return state


async def _queued_erasure(maker: Any, world: SimpleNamespace) -> uuid.UUID:
    async with maker.session() as db:
        job = _job(world.subject, world.admin, kind="erasure", status="queued")
        job.phase = "lockout"
        job.counts = {"refresh_token_records.user_id": 1}
        db.add(job)
        await db.commit()
        return job.id


async def test_the_runner_commits_each_phase_and_audits_at_done(
    maker: Any, world: SimpleNamespace, phases: SimpleNamespace,
) -> None:
    from app.worker.subject_erasure_runner import run_erasure_job

    job_id = await _queued_erasure(maker, world)
    assert await run_erasure_job(job_id, storage=object(), session_factory=maker.session) == "succeeded"
    assert phases.calls == ["postgres", "objects", "redis"]
    async with maker.session() as db:
        job = await db.get(SubjectRightsJob, job_id)
        audit = (await db.execute(select(AccessAuditLog))).scalars().all()
    assert job.status == "succeeded" and job.phase == "done" and job.error_code is None
    assert job.counts == {"refresh_token_records.user_id": 1, "api_keys.user_id": 2, "users.*": 1,
                          "objects:subject-exports/": 1, "objects:project-exports/": 0,
                          "redis:membership:{id}": 1}
    assert len(audit) == 1 and audit[0].action == "subject_erased"
    assert audit[0].actor_user_id == world.admin.id and audit[0].target_user_id == world.subject.id
    assert audit[0].after_value == {"job_id": str(job_id), "counts": job.counts}
    # a redelivered message finds nothing queued
    assert await run_erasure_job(str(job_id), storage=object(), session_factory=maker.session) == "skipped"


async def test_a_failing_phase_keeps_the_job_running_and_resumes_there(
    maker: Any, world: SimpleNamespace, phases: SimpleNamespace,
) -> None:
    from app.services import subject_rights_job_service as jobs
    from app.worker.subject_erasure_runner import run_erasure_job

    phases.fail = {"objects"}
    job_id = await _queued_erasure(maker, world)
    assert await run_erasure_job(job_id, storage=object(), session_factory=maker.session) == "incomplete"
    async with maker.session() as db:
        job = await db.get(SubjectRightsJob, job_id)
        assert job.status == "running" and job.phase == "postgres" and job.error_code == "erasure_incomplete"
        assert job.error == ERROR_SENTENCES["erasure_incomplete"].format(phase="postgres")
        assert await jobs.requeue(db, job_id, kind="erasure")
        await db.commit()
    assert await _count(maker, AccessAuditLog) == 0
    assert await run_erasure_job(job_id, storage=object(), session_factory=maker.session) == "succeeded"
    assert phases.calls == ["postgres", "objects", "objects", "redis"]   # the Postgres phase is not re-run
    async with maker.session() as db:
        job = await db.get(SubjectRightsJob, job_id)
    assert job.status == "succeeded" and job.error_code is None and job.attempts == 2


async def test_a_failing_postgres_phase_records_lockout(
    maker: Any, world: SimpleNamespace, phases: SimpleNamespace,
) -> None:
    from app.worker.subject_erasure_runner import run_erasure_job

    phases.fail = {"postgres"}
    job_id = await _queued_erasure(maker, world)
    assert await run_erasure_job(job_id, storage=object(), session_factory=maker.session) == "incomplete"
    async with maker.session() as db:
        job = await db.get(SubjectRightsJob, job_id)
    assert job.status == "running" and job.phase == "lockout" and job.error_code == "erasure_incomplete"
    assert job.counts == {"refresh_token_records.user_id": 1} and phases.calls == ["postgres"]
