"""W31 STRAT-12 — the six export / job routes through the REAL router (``routers/subject_rights.py``; contract §3.1).

An in-memory SQLite database holds the three tables the routes touch (users, subject_rights_jobs with its two
partial uniques rebuilt in SQLite syntax, access_audit_logs); ``get_db`` and ``get_current_active_user`` are
overridden; every other dependency (``require_signed_in_session``, ``require_role``, ``require_subject_job_access``)
is the real one. An engine listener records every statement, so "a GET writes nothing" and "a refusal writes no
audit row" are measured. ``apply_async`` is a recorder that BINDS the payload to the task's real signature.
"""
from __future__ import annotations

import inspect
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event, func, select, text
from sqlalchemy import types as sqltypes
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.sqlite import DATETIME as SQLITE_DATETIME
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.pool import StaticPool

from app.core.deps import (
    CREDENTIAL_KIND_API_KEY,
    ApiKeyGrant,
    _bind_api_key_grant,
    _bind_api_key_project,
    _bind_credential_kind,
    get_current_active_user,
)
from app.db.postgres import get_db
from app.models.postgres import AccessAuditLog, Base, SubjectRightsJob, User, UserRole
from app.routers import subject_rights as routes
from app.services.subject_rights_vocab import ERROR_SENTENCES
from app.worker import tasks


@compiles(JSONB, "sqlite")
def _jsonb_on_sqlite(type_: Any, compiler: Any, **kw: Any) -> str:  # noqa: D103 - JSONB as SQLite JSON
    return "JSON"


class _AwareDateTime(SQLITE_DATETIME):
    def result_processor(self, dialect: Any, coltype: Any) -> Any:  # noqa: D102
        inner = super().result_processor(dialect, coltype)

        def process(value: Any) -> Any:
            parsed = inner(value) if inner is not None else value
            if isinstance(parsed, datetime) and parsed.tzinfo is None:
                return parsed.replace(tzinfo=timezone.utc)
            return parsed

        return process


_TABLES = [User.__table__, SubjectRightsJob.__table__, AccessAuditLog.__table__]
NOT_FOUND = {"detail": {"code": "job_not_found", "message": "Subject data job not found."}}
SESSION_REQUIRED = {"detail": {"code": "session_required", "message": ERROR_SENTENCES["session_required"]}}


class FakeStorage:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.fail_stream = False

    async def stream_object(self, key: str, bucket: str | None = None) -> Any:
        if self.fail_stream:
            raise ConnectionRefusedError("storage down")
        data = self.objects[key]
        for start in range(0, len(data), 1000):
            yield data[start:start + 1000]


@pytest.fixture
async def factory(monkeypatch: pytest.MonkeyPatch) -> Any:
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:", poolclass=StaticPool, connect_args={"check_same_thread": False},
    )
    dialect = engine.sync_engine.dialect
    dialect.colspecs = {**dialect.colspecs, sqltypes.DateTime: _AwareDateTime}

    @event.listens_for(engine.sync_engine, "connect")
    def _no_pysqlite_begin(dbapi_connection: Any, _record: Any) -> None:   # SAVEPOINT support
        dbapi_connection.isolation_level = None

    @event.listens_for(engine.sync_engine, "begin")
    def _begin(conn: Any) -> None:
        conn.exec_driver_sql("BEGIN")

    statements: list[str] = []

    @event.listens_for(engine.sync_engine, "before_cursor_execute")
    def _record(conn: Any, cursor: Any, statement: str, *_: Any) -> None:
        statements.append(statement)

    monkeypatch.setattr(SubjectRightsJob.__table__.c.counts, "server_default", None)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all, tables=_TABLES)
        for kind in ("export", "erasure"):
            await conn.execute(text(f"DROP INDEX IF EXISTS uq_srj_one_live_{kind}"))
            await conn.execute(text(
                f"CREATE UNIQUE INDEX uq_srj_one_live_{kind} ON subject_rights_jobs (subject_user_id) "
                f"WHERE kind = '{kind}' AND status IN ('queued', 'running')"
            ))
    maker = async_sessionmaker(engine, expire_on_commit=False)
    maker.statements = statements  # type: ignore[attr-defined]
    yield maker
    await engine.dispose()


@pytest.fixture
def storage(monkeypatch: pytest.MonkeyPatch) -> FakeStorage:
    store = FakeStorage()
    monkeypatch.setattr("app.db.storage.get_storage_provider", lambda: store)
    return store


@pytest.fixture
def dispatched(monkeypatch: pytest.MonkeyPatch) -> list[tuple]:
    """``apply_async`` recorders that bind the REAL payload to the task's signature and the options to Celery's
    own ``Task.apply_async``."""
    from celery.app.task import Task

    calls: list[tuple] = []

    def apply_async(args: Any = None, kwargs: Any = None, _task: Any = tasks.export_subject_data,
                    **options: Any) -> Any:
        inspect.signature(_task.run).bind(*(args or ()), **(kwargs or {}))
        inspect.signature(Task.apply_async).bind(_task, args, kwargs, **options)
        calls.append((_task.name, *(args or ())))
        return SimpleNamespace(id="celery-id")

    monkeypatch.setattr(tasks.export_subject_data, "apply_async", apply_async)
    return calls


def _user(role: UserRole, name: str, **extra: Any) -> User:
    values: dict[str, Any] = {"is_service_account": False, "is_synthetic": False, "erased_at": None}
    values.update(extra)
    return User(id=uuid.uuid4(), username=name, email=f"{name}@example.test", full_name=name.title(),
                hashed_password="x", role=role.value, is_active=True, **values)


@pytest.fixture
async def world(factory: Any) -> SimpleNamespace:
    people = SimpleNamespace(
        admin=_user(UserRole.ADMIN, "zqxwadmin"), admin2=_user(UserRole.ADMIN, "zqxwsecond"),
        lead=_user(UserRole.QA_LEAD, "zqxwlead"), engineer=_user(UserRole.QA_ENGINEER, "zqxwengineer"),
        viewer=_user(UserRole.VIEWER, "zqxwviewer"), subject=_user(UserRole.TESTER, "zqxwsubject"),
        service=_user(UserRole.QA_LEAD, "zqxwbot", is_service_account=True),
        synthetic=_user(UserRole.QA_LEAD, "zqxwsynth", is_synthetic=True),
        erased=_user(UserRole.TESTER, "erased-zqxwghij", erased_at=datetime.now(timezone.utc)),
    )
    async with factory() as db:
        db.add_all(vars(people).values())
        await db.commit()
    return people


def _as_key(user: User, how: str) -> User:
    """A fresh copy of ``user`` authenticated by an API key, the way each auth path marks it."""
    principal = _user(UserRole(user.role), user.username)
    principal.id = user.id
    grant = {"scoped": ("project:write",), "legacy": (), "bound": ()}
    if how == "bound":
        _bind_api_key_project(principal, uuid.uuid4())
    if how in grant:
        _bind_api_key_grant(principal, ApiKeyGrant(key_id=uuid.uuid4(), scopes=grant[how], expires_at=None))
    _bind_credential_kind(principal, CREDENTIAL_KIND_API_KEY)
    return principal


def _client(factory: Any, user: Any) -> AsyncClient:
    app = FastAPI()
    app.include_router(routes.router)

    async def _db() -> Any:
        async with factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_current_active_user] = lambda: user
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _job(factory: Any, subject: User, requester: User, **values: Any) -> SubjectRightsJob:
    values.setdefault("kind", "export")
    values.setdefault("status", "queued")
    values.setdefault("source", "self" if requester.id == subject.id else "admin")
    row = SubjectRightsJob(id=uuid.uuid4(), subject_user_id=subject.id, requested_by_id=requester.id, counts={},
                           attempts=0, requested_at=datetime.now(timezone.utc), **values)
    async with factory() as db:
        db.add(row)
        await db.commit()
    return row


async def _audit_rows(factory: Any) -> list[AccessAuditLog]:
    async with factory() as db:
        return list((await db.execute(select(AccessAuditLog).order_by(AccessAuditLog.created_at))).scalars())


async def _count_jobs(factory: Any) -> int:
    async with factory() as db:
        return int((await db.execute(select(func.count()).select_from(SubjectRightsJob))).scalar_one())


def _writes(statements: list[str]) -> list[str]:
    return [s for s in statements if s.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE"))]


# ── the person's own export ─────────────────────────────────────────────────

@pytest.mark.parametrize("who", ["viewer", "subject", "engineer", "lead", "admin"])
async def test_anyone_signed_in_exports_their_own_data_committed_then_dispatched(
    factory: Any, world: Any, dispatched: list, who: str,
) -> None:
    me = getattr(world, who)
    async with _client(factory, me) as client:
        answer = await client.post("/api/v1/me/data-export")
    assert answer.status_code == 202, answer.text
    body = answer.json()
    assert (body["kind"], body["status"], body["source"]) == ("export", "queued", "self")
    assert body["subject_user_id"] == str(me.id) and body["subject_display"] == me.full_name
    assert body["download_path"] is None and "storage_key" not in body
    assert dispatched == [("app.worker.tasks.export_subject_data", body["id"])]
    async with factory() as db:                    # committed before the enqueue: the worker can see it
        row = await db.get(SubjectRightsJob, uuid.UUID(body["id"]))
    assert row is not None and row.requested_by_id == me.id == row.subject_user_id
    audit, = await _audit_rows(factory)
    assert (audit.action, audit.actor_user_id, audit.target_user_id) == ("subject_export_requested", me.id, me.id)
    assert audit.after_value == {"job_id": body["id"], "source": "self"}


@pytest.mark.parametrize(("who", "code"), [("service", "not_a_person"), ("synthetic", "not_a_person"),
                                           ("erased", "user_erased")])
async def test_a_machine_or_erased_account_is_refused_and_nothing_is_written(
    factory: Any, world: Any, dispatched: list, who: str, code: str,
) -> None:
    async with _client(factory, getattr(world, who)) as client:
        answer = await client.post("/api/v1/me/data-export")
    assert answer.status_code == 409
    assert answer.json() == {"detail": {"code": code, "message": ERROR_SENTENCES[code]}}
    assert dispatched == [] and await _count_jobs(factory) == 0 and await _audit_rows(factory) == []


async def test_a_second_export_while_one_is_live_is_409_naming_it(factory: Any, world: Any, dispatched: list) -> None:
    live = await _job(factory, world.subject, world.admin, status="running")
    async with _client(factory, world.subject) as client:
        answer = await client.post("/api/v1/me/data-export")
    assert answer.status_code == 409
    assert answer.json()["detail"] == {"code": "export_running", "message": ERROR_SENTENCES["export_running"],
                                       "job_id": str(live.id)}
    assert dispatched == [] and await _audit_rows(factory) == []


async def test_my_exports_lists_only_my_exports_of_myself(factory: Any, world: Any) -> None:
    mine = await _job(factory, world.subject, world.subject, status="succeeded")
    await _job(factory, world.subject, world.admin, status="failed")       # an admin's export of me: not "mine"
    await _job(factory, world.lead, world.lead, status="failed")
    factory.statements.clear()
    async with _client(factory, world.subject) as client:
        answer = await client.get("/api/v1/me/data-exports")
    assert answer.status_code == 200
    assert answer.json()["total"] == 1 and [item["id"] for item in answer.json()["items"]] == [str(mine.id)]
    assert _writes(factory.statements) == []


# ── an administrator, for any person ────────────────────────────────────────

async def test_an_admin_exports_any_person(factory: Any, world: Any, dispatched: list) -> None:
    async with _client(factory, world.admin) as client:
        answer = await client.post(f"/api/v1/users/{world.subject.id}/data-export")
    assert answer.status_code == 202, answer.text
    body = answer.json()
    assert body["source"] == "admin" and body["subject_user_id"] == str(world.subject.id)
    assert body["requested_by_display"] == world.admin.full_name
    audit, = await _audit_rows(factory)
    assert (audit.actor_user_id, audit.target_user_id) == (world.admin.id, world.subject.id)
    assert audit.after_value == {"job_id": body["id"], "source": "admin"}
    assert dispatched == [("app.worker.tasks.export_subject_data", body["id"])]


@pytest.mark.parametrize("who", ["lead", "engineer", "viewer", "subject"])
async def test_the_admin_routes_are_403_below_admin(factory: Any, world: Any, dispatched: list, who: str) -> None:
    async with _client(factory, getattr(world, who)) as client:
        posted = await client.post(f"/api/v1/users/{world.subject.id}/data-export")
        listed = await client.get(f"/api/v1/users/{world.subject.id}/data-jobs")
    for answer in (posted, listed):
        assert answer.status_code == 403 and answer.json()["detail"] == "Requires at least ADMIN role"
    assert dispatched == [] and await _count_jobs(factory) == 0 and await _audit_rows(factory) == []


async def test_the_admin_export_refusals(factory: Any, world: Any, dispatched: list) -> None:
    async with factory() as db:
        live = SubjectRightsJob(id=uuid.uuid4(), kind="erasure", status="running", source="admin", phase="postgres",
                                subject_user_id=world.engineer.id, requested_by_id=world.admin.id, counts={},
                                attempts=1, requested_at=datetime.now(timezone.utc))
        db.add(live)
        await db.commit()
    async with _client(factory, world.admin) as client:
        missing = await client.post(f"/api/v1/users/{uuid.uuid4()}/data-export")
        erased = await client.post(f"/api/v1/users/{world.erased.id}/data-export")
        erasing = await client.post(f"/api/v1/users/{world.engineer.id}/data-export")
        machine = await client.post(f"/api/v1/users/{world.service.id}/data-export")
    assert missing.status_code == 404
    assert missing.json() == {"detail": {"code": "user_not_found", "message": "User not found."}}
    assert erased.status_code == erasing.status_code == machine.status_code == 409
    assert erased.json()["detail"]["code"] == erasing.json()["detail"]["code"] == "user_erased"
    assert machine.json()["detail"]["code"] == "not_a_person"
    assert dispatched == [] and await _audit_rows(factory) == []


async def test_the_admin_job_list_filters_by_kind_and_writes_nothing(factory: Any, world: Any) -> None:
    export = await _job(factory, world.subject, world.admin, status="succeeded")
    erasure = await _job(factory, world.subject, world.admin, kind="erasure", status="running", phase="objects")
    await _job(factory, world.lead, world.admin, status="failed")
    factory.statements.clear()
    async with _client(factory, world.admin) as client:
        both = await client.get(f"/api/v1/users/{world.subject.id}/data-jobs")
        exports = await client.get(f"/api/v1/users/{world.subject.id}/data-jobs?kind=export")
        bogus = await client.get(f"/api/v1/users/{world.subject.id}/data-jobs?kind=import")
        missing = await client.get(f"/api/v1/users/{uuid.uuid4()}/data-jobs")
    assert {item["id"] for item in both.json()["items"]} == {str(export.id), str(erasure.id)}
    assert both.json()["total"] == 2
    assert [item["id"] for item in exports.json()["items"]] == [str(export.id)]
    erasure_item = next(item for item in both.json()["items"] if item["kind"] == "erasure")
    assert erasure_item["phase"] == "objects"
    assert bogus.status_code == 422 and missing.status_code == 404
    assert _writes(factory.statements) == []


# ── every route refuses every API-key principal ─────────────────────────────

@pytest.mark.parametrize("how", ["scoped", "legacy", "bound", "streaming"])
@pytest.mark.parametrize("owner", ["admin", "subject"])
async def test_every_route_is_403_session_required_for_any_api_key(
    factory: Any, world: Any, dispatched: list, storage: FakeStorage, how: str, owner: str,
) -> None:
    now = datetime.now(timezone.utc)
    job = await _job(factory, world.subject, world.subject, status="succeeded", storage_key="subject-exports/k.zip",
                     bytes=3, finished_at=now, expires_at=now + timedelta(hours=72))
    storage.objects["subject-exports/k.zip"] = b"zip"
    principal = _as_key(getattr(world, owner), how)
    factory.statements.clear()
    async with _client(factory, principal) as client:
        answers = [
            await client.post("/api/v1/me/data-export"),
            await client.get("/api/v1/me/data-exports"),
            await client.post(f"/api/v1/users/{world.subject.id}/data-export"),
            await client.get(f"/api/v1/users/{world.subject.id}/data-jobs"),
            await client.get(f"/api/v1/subject-data-jobs/{job.id}"),
            await client.get(f"/api/v1/subject-data-jobs/{job.id}/download"),
            await client.get(f"/api/v1/subject-data-jobs/{uuid.uuid4()}"),        # no oracle: unknown id alike
        ]
    for answer in answers:
        assert answer.status_code == 403, answer.text
        assert answer.json() == SESSION_REQUIRED
    assert dispatched == [] and _writes(factory.statements) == [] and await _count_jobs(factory) == 1


# ── one job: its requester or an ADMIN; everyone else the SAME 404 ──────────

async def test_the_job_get_answers_its_requester_and_any_admin_and_writes_nothing(factory: Any, world: Any) -> None:
    job = await _job(factory, world.subject, world.subject, status="running")
    factory.statements.clear()
    for who in ("subject", "admin", "admin2"):
        async with _client(factory, getattr(world, who)) as client:
            answer = await client.get(f"/api/v1/subject-data-jobs/{job.id}")
        assert answer.status_code == 200 and answer.json()["id"] == str(job.id)
        assert answer.json()["status"] == "running" and answer.json()["download_path"] is None
    assert _writes(factory.statements) == []


async def test_the_job_get_and_download_share_one_404(factory: Any, world: Any, storage: FakeStorage) -> None:
    now = datetime.now(timezone.utc)
    job = await _job(factory, world.subject, world.subject, status="succeeded", storage_key="subject-exports/k.zip",
                     bytes=3, finished_at=now, expires_at=now + timedelta(hours=72))
    by_lead = await _job(factory, world.engineer, world.lead, status="succeeded")   # a QA lead may not request…
    storage.objects["subject-exports/k.zip"] = b"zip"
    factory.statements.clear()
    cases = [(world.lead, job.id), (world.engineer, job.id), (world.viewer, job.id), (world.subject, uuid.uuid4()),
             (world.engineer, by_lead.id)]                                       # …nor its subject read it
    for user, job_id in cases:
        async with _client(factory, user) as client:
            for path in (f"/api/v1/subject-data-jobs/{job_id}", f"/api/v1/subject-data-jobs/{job_id}/download"):
                answer = await client.get(path)
                assert answer.status_code == 404 and answer.json() == NOT_FOUND, (user.username, path)
    assert _writes(factory.statements) == [] and await _audit_rows(factory) == []


# ── the download ────────────────────────────────────────────────────────────

async def test_the_download_streams_the_stored_bytes_after_one_committed_audit_row(
    factory: Any, world: Any, storage: FakeStorage,
) -> None:
    now = datetime.now(timezone.utc)
    data = bytes(range(256)) * 20
    job = await _job(factory, world.subject, world.subject, status="succeeded", storage_key="subject-exports/k.zip",
                     bytes=len(data), finished_at=datetime(2026, 11, 4, 9, 7, tzinfo=timezone.utc),
                     expires_at=now + timedelta(hours=72))
    storage.objects["subject-exports/k.zip"] = data
    for who in ("subject", "admin"):
        async with _client(factory, getattr(world, who)) as client:
            answer = await client.get(f"/api/v1/subject-data-jobs/{job.id}/download")
        assert answer.status_code == 200 and answer.content == data
        assert answer.headers["content-type"] == "application/zip"
        assert answer.headers["content-disposition"] == 'attachment; filename="testlookup-personal-data-20261104-0907.zip"'
        assert answer.headers["content-length"] == str(len(data)) and answer.headers["cache-control"] == "no-store"
    rows = await _audit_rows(factory)
    assert [(row.action, row.actor_user_id, row.target_user_id) for row in rows] == [
        ("subject_export_downloaded", world.subject.id, world.subject.id),
        ("subject_export_downloaded", world.admin.id, world.subject.id),
    ]
    assert all(row.after_value == {"job_id": str(job.id)} for row in rows)
    async with factory() as db:
        assert (await db.get(SubjectRightsJob, job.id)).status == "succeeded"     # the job row is never touched


@pytest.mark.parametrize(
    ("values", "code", "http"),
    [({"kind": "erasure", "status": "running", "phase": "postgres", "source": "admin"}, "not_an_export", 409),
     ({"status": "queued"}, "export_not_ready", 409),
     ({"status": "running"}, "export_not_ready", 409),
     ({"status": "failed", "error_code": "export_build"}, "export_failed", 409),
     ({"status": "expired"}, "export_expired", 410),
     ({"status": "succeeded", "storage_key": "subject-exports/k.zip", "expires_at": "past"}, "export_expired", 410)],
)
async def test_each_download_refusal_reads_its_sentence_and_writes_no_audit_row(
    factory: Any, world: Any, storage: FakeStorage, values: dict, code: str, http: int,
) -> None:
    values = dict(values)
    if values.get("expires_at") == "past":
        values["expires_at"] = datetime.now(timezone.utc) - timedelta(minutes=1)
    job = await _job(factory, world.subject, world.subject, **values)
    async with _client(factory, world.subject) as client:
        answer = await client.get(f"/api/v1/subject-data-jobs/{job.id}/download")
    assert answer.status_code == http
    assert answer.json() == {"detail": {"code": code, "message": ERROR_SENTENCES[code]}}
    assert await _audit_rows(factory) == []


async def test_a_storage_fault_is_502_and_the_attempt_stays_recorded(
    factory: Any, world: Any, storage: FakeStorage,
) -> None:
    now = datetime.now(timezone.utc)
    job = await _job(factory, world.subject, world.subject, status="succeeded", storage_key="subject-exports/k.zip",
                     bytes=3, finished_at=now, expires_at=now + timedelta(hours=72))
    storage.fail_stream = True
    async with _client(factory, world.subject) as client:
        answer = await client.get(f"/api/v1/subject-data-jobs/{job.id}/download")
    assert answer.status_code == 502
    assert answer.json() == {"detail": {"code": "archive_unreadable", "message": ERROR_SENTENCES["archive_unreadable"]}}
    audit, = await _audit_rows(factory)
    assert audit.action == "subject_export_downloaded" and audit.after_value == {"job_id": str(job.id)}


def test_the_download_route_is_a_streaming_response() -> None:
    from fastapi.responses import StreamingResponse

    route = next(r for r in routes.router.routes if getattr(r, "path", "").endswith("/download"))
    assert inspect.signature(route.endpoint).return_annotation in (StreamingResponse, "StreamingResponse")
    paths = sorted((sorted(r.methods)[0], r.path) for r in routes.router.routes)
    assert paths == [
        ("GET", "/api/v1/me/data-exports"), ("GET", "/api/v1/subject-data-jobs/{subject_job_id}"),
        ("GET", "/api/v1/subject-data-jobs/{subject_job_id}/download"),
        ("GET", "/api/v1/users/{user_id}/data-jobs"), ("POST", "/api/v1/me/data-export"),
        ("POST", "/api/v1/users/{user_id}/data-export"),
    ]
