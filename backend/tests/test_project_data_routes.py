"""W24 ADM-05 — the six project-data routes through the REAL router (contract §3.1, D-3).

An in-memory SQLite database holds the four tables the routes touch (projects, users, project_members,
project_data_jobs, with the two partial unique indexes rebuilt in SQLite syntax); ``get_db`` and
``get_current_active_user`` are overridden; every other dependency (``require_role``,
``require_project_access``, ``require_project_data_job_access``) is the real one. An engine listener
records every statement, so "a GET writes nothing" is measured, not assumed. The Celery ``apply_async``
is replaced by a recorder that BINDS the payload to the task's real signature.
"""
from __future__ import annotations

import inspect
import io
import uuid
import zipfile
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
    _bind_api_key_project,
    _bind_credential_kind,
    get_current_active_user,
)
from app.db.postgres import get_db
from app.models.postgres import Base, Project, ProjectDataJob, ProjectMember, Release, User, UserRole
from app.routers import project_data as routes
from app.services import project_export_vocab as vocab
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


_TABLES = [Project.__table__, User.__table__, ProjectMember.__table__, ProjectDataJob.__table__,
           Release.__table__]   # W37: the release-scoped export checks a release's project
_PARTIAL = (
    "CREATE UNIQUE INDEX uq_pdj_one_live_export ON project_data_jobs (project_id) "
    "WHERE kind = 'export' AND status IN ('queued', 'running')",
    "CREATE UNIQUE INDEX uq_pdj_import_token ON project_data_jobs (requested_by_id, client_token) "
    "WHERE kind = 'import' AND status <> 'failed'",
)
NOT_FOUND = {"detail": {"code": "job_not_found", "message": "Project data job not found."}}


class _NoRedis:
    async def get(self, *_: Any) -> None:
        return None

    async def set(self, *_: Any, **__: Any) -> None:
        return None

    async def delete(self, *_: Any) -> None:
        return None


class FakeStorage:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.fail_stream = False
        self.puts: list[tuple[str, str, str]] = []           # (key, bucket, content type) per write

    async def put_object_stream(self, key: str, fileobj: Any, *, size: int, content_type: str, bucket: str) -> None:
        fileobj.seek(0)
        self.objects[key] = fileobj.read()
        assert len(self.objects[key]) == size
        self.puts.append((key, bucket, content_type))

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

    # ``'{}'::jsonb`` is Postgres syntax: the rows always carry ``counts`` explicitly.
    monkeypatch.setattr(ProjectDataJob.__table__.c.counts, "server_default", None)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all, tables=_TABLES)
        for name in ("uq_pdj_one_live_export", "uq_pdj_import_token"):
            await conn.execute(text(f"DROP INDEX IF EXISTS {name}"))
        for ddl in _PARTIAL:
            await conn.execute(text(ddl))
    maker = async_sessionmaker(engine, expire_on_commit=False)
    maker.statements = statements  # type: ignore[attr-defined]
    monkeypatch.setattr("app.db.redis_client.get_redis", lambda: _NoRedis())
    yield maker
    await engine.dispose()


@pytest.fixture
def storage(monkeypatch: pytest.MonkeyPatch) -> FakeStorage:
    store = FakeStorage()
    monkeypatch.setattr("app.db.storage.get_storage_provider", lambda: store)
    return store


class _Dispatched(list):
    """The recorded ``(task name, *args)`` calls; ``options`` keeps each call's ``apply_async`` options (W30)."""

    def __init__(self) -> None:
        super().__init__()
        self.options: list[dict[str, Any]] = []


@pytest.fixture
def dispatched(monkeypatch: pytest.MonkeyPatch) -> _Dispatched:
    """``apply_async`` recorders that bind the REAL payload to each task's signature, and the per-call options to
    Celery's own ``Task.apply_async`` signature (an unknown option name would fail the bind)."""
    from celery.app.task import Task

    calls = _Dispatched()
    for task in (tasks.export_project_data, tasks.import_project_data):
        def apply_async(args: Any = None, kwargs: Any = None, _task: Any = task, **options: Any) -> Any:
            inspect.signature(_task.run).bind(*(args or ()), **(kwargs or {}))
            inspect.signature(Task.apply_async).bind(_task, args, kwargs, **options)
            calls.append((_task.name, *(args or ())))
            calls.options.append(dict(options))
            return SimpleNamespace(id="celery-id")
        monkeypatch.setattr(task, "apply_async", apply_async)
    return calls


def _user(role: UserRole, name: str) -> User:
    return User(id=uuid.uuid4(), username=name, email=f"{name}@example.test", full_name=name.title(),
                hashed_password="x", role=role.value, is_active=True)


@pytest.fixture
async def world(factory: Any) -> SimpleNamespace:
    project = Project(id=uuid.uuid4(), name="Payment Service", slug="payment-service", is_active=True)
    other = Project(id=uuid.uuid4(), name="Auth Service", slug="auth-service", is_active=True)
    admin, admin2 = _user(UserRole.ADMIN, "ada"), _user(UserRole.ADMIN, "abe")
    lead, outsider = _user(UserRole.QA_LEAD, "lee"), _user(UserRole.QA_LEAD, "olu")
    engineer, viewer = _user(UserRole.QA_ENGINEER, "eve"), _user(UserRole.VIEWER, "vic")
    async with factory() as db:
        db.add_all([project, other, admin, admin2, lead, outsider, engineer, viewer])
        await db.flush()
        for member, target in ((lead, project), (engineer, project), (viewer, project), (outsider, other)):
            db.add(ProjectMember(user_id=member.id, project_id=target.id, role=member.role))
        await db.commit()
    bound = SimpleNamespace(id=admin.id, role=UserRole.ADMIN.value, is_active=True, username="ci-key",
                            full_name=None, email="ci@example.test")
    _bind_credential_kind(_bind_api_key_project(bound, project.id), CREDENTIAL_KIND_API_KEY)
    return SimpleNamespace(project=project, other=other, admin=admin, admin2=admin2, lead=lead, outsider=outsider,
                           engineer=engineer, viewer=viewer, bound=bound)


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


async def _job(factory: Any, **values: Any) -> ProjectDataJob:
    now = datetime.now(timezone.utc)
    row = ProjectDataJob(id=uuid.uuid4(), counts={}, attempts=0, requested_at=now, **values)
    async with factory() as db:
        db.add(row)
        await db.commit()
    return row


async def _count_jobs(factory: Any) -> int:
    async with factory() as db:
        return int((await db.execute(select(func.count()).select_from(ProjectDataJob))).scalar_one())


def _writes(statements: list[str]) -> list[str]:
    return [s for s in statements if s.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE"))]


def _zip_bytes() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr("manifest.json", "{}")
    return buffer.getvalue()


# ── the export POST ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("who", ["admin", "lead"])
async def test_export_is_queued_committed_then_dispatched_with_its_real_payload(
    factory: Any, world: Any, dispatched: list, who: str,
) -> None:
    async with _client(factory, getattr(world, who)) as client:
        answer = await client.post(f"/api/v1/projects/{world.project.id}/export")
    assert answer.status_code == 202, answer.text
    body = answer.json()
    assert body["kind"] == "export" and body["status"] == "queued" and body["project_name"] == "Payment Service"
    assert body["download_path"] is None and "storage_key" not in body
    assert dispatched == [("app.worker.tasks.export_project_data", body["id"])]
    async with factory() as db:                  # committed before the enqueue: the worker can see it
        row = await db.get(ProjectDataJob, uuid.UUID(body["id"]))
    assert row is not None and row.status == "queued" and row.requested_by_id == getattr(world, who).id


@pytest.mark.parametrize(
    ("who", "detail"),
    [("outsider", "You do not have access to this project"),
     ("engineer", f"Requires at least {UserRole.QA_LEAD.value} role"),
     ("viewer", f"Requires at least {UserRole.QA_LEAD.value} role")],
)
async def test_export_refuses_a_non_member_and_a_role_below_qa_lead(
    factory: Any, world: Any, dispatched: list, who: str, detail: str,
) -> None:
    async with _client(factory, getattr(world, who)) as client:
        answer = await client.post(f"/api/v1/projects/{world.project.id}/export")
    assert answer.status_code == 403
    assert answer.json()["detail"] == detail
    assert dispatched == [] and await _count_jobs(factory) == 0


async def test_a_project_bound_key_is_refused_on_both_posts(factory: Any, world: Any, dispatched: list) -> None:
    async with _client(factory, world.bound) as client:
        export = await client.post(f"/api/v1/projects/{world.project.id}/export")
        imported = await client.post(
            "/api/v1/projects/import", data={"client_token": str(uuid.uuid4())},
            files={"file": ("x.zip", _zip_bytes(), "application/zip")},
        )
    assert export.status_code == 403 and imported.status_code == 403
    assert dispatched == [] and await _count_jobs(factory) == 0


async def test_a_second_export_while_one_is_live_is_409_naming_it(factory: Any, world: Any, dispatched: list) -> None:
    live = await _job(factory, kind="export", status="running", project_id=world.project.id)
    async with _client(factory, world.lead) as client:
        answer = await client.post(f"/api/v1/projects/{world.project.id}/export")
    assert answer.status_code == 409
    assert answer.json()["detail"] == {"code": "export_running", "message": vocab.ERROR_SENTENCES["export_running"],
                                       "job_id": str(live.id)}
    assert dispatched == []


async def test_a_missing_project_is_404(factory: Any, world: Any, dispatched: list) -> None:
    async with _client(factory, world.admin) as client:
        answer = await client.post(f"/api/v1/projects/{uuid.uuid4()}/export")
    assert answer.status_code == 404 and answer.json()["detail"] == "Project not found"


@pytest.mark.parametrize(("who", "expected"), [("admin", 202), ("lead", 202), ("outsider", 403)])
async def test_an_archived_project_keeps_require_project_access_unchanged(
    factory: Any, world: Any, dispatched: list, who: str, expected: int,
) -> None:
    """Open point 10: ``require_project_access`` does not read ``is_active`` / ``archived_at`` and archiving
    keeps the memberships, so an ADMIN and a QA lead who is still a member may export an archived project;
    a non-member is refused exactly as for an active one. The route neither widens nor narrows that."""
    async with factory() as db:
        project = await db.get(Project, world.project.id)
        project.is_active = False
        project.archived_at = datetime.now(timezone.utc)
        await db.commit()
    async with _client(factory, getattr(world, who)) as client:
        answer = await client.post(f"/api/v1/projects/{world.project.id}/export")
    assert answer.status_code == expected, answer.text


# ── the GETs: the authz matrix, one 404 body, no write ──────────────────────

async def _jobs_of_every_kind(factory: Any, world: Any, storage: FakeStorage) -> SimpleNamespace:
    now = datetime.now(timezone.utc)
    storage.objects["project-exports/x/ready.zip"] = b"PK\x03\x04" + b"z" * 2500
    ready = await _job(factory, kind="export", status="succeeded", project_id=world.project.id,
                       requested_by_id=world.lead.id, storage_key="project-exports/x/ready.zip", bytes=2504,
                       finished_at=now - timedelta(hours=1), expires_at=now + timedelta(hours=71))
    foreign = await _job(factory, kind="export", status="succeeded", project_id=world.other.id,
                         requested_by_id=world.outsider.id, finished_at=now, expires_at=now + timedelta(hours=72))
    mine = await _job(factory, kind="import", status="queued", requested_by_id=world.admin.id,
                      client_token=uuid.uuid4())
    theirs = await _job(factory, kind="import", status="queued", requested_by_id=world.admin2.id,
                        client_token=uuid.uuid4())
    return SimpleNamespace(ready=ready, foreign=foreign, mine=mine, theirs=theirs)


@pytest.mark.parametrize(
    ("who", "job", "expected"),
    [("admin", "ready", 200), ("lead", "ready", 200), ("outsider", "ready", 404), ("engineer", "ready", 404),
     ("viewer", "ready", 404), ("bound", "ready", 404), ("lead", "foreign", 404), ("admin", "mine", 200),
     ("admin", "theirs", 404), ("lead", "mine", 404), ("admin", "unknown", 404)],
)
async def test_the_job_get_and_download_share_one_404_and_write_nothing(
    factory: Any, world: Any, storage: FakeStorage, who: str, job: str, expected: int,
) -> None:
    rows = await _jobs_of_every_kind(factory, world, storage)
    job_id = uuid.uuid4() if job == "unknown" else getattr(rows, job).id
    before = len(factory.statements)
    async with _client(factory, getattr(world, who)) as client:
        read = await client.get(f"/api/v1/project-data-jobs/{job_id}")
        download = await client.get(f"/api/v1/project-data-jobs/{job_id}/download")
    assert _writes(factory.statements[before:]) == [], "a GET wrote"
    assert read.status_code == expected, read.text
    if expected == 404:
        assert read.json() == NOT_FOUND and download.status_code == 404 and download.json() == NOT_FOUND
    else:
        assert read.json()["id"] == str(job_id) and "storage_key" not in read.json()


async def test_the_lists_follow_the_same_rules_and_write_nothing(
    factory: Any, world: Any, storage: FakeStorage,
) -> None:
    rows = await _jobs_of_every_kind(factory, world, storage)
    before = len(factory.statements)
    async with _client(factory, world.lead) as client:
        exports = await client.get(f"/api/v1/projects/{world.project.id}/data-jobs")
        refused_imports = await client.get("/api/v1/project-imports")
        foreign = await client.get(f"/api/v1/projects/{world.other.id}/data-jobs")
    async with _client(factory, world.admin) as client:
        imports = await client.get("/api/v1/project-imports?limit=10")
        too_many = await client.get("/api/v1/project-imports?limit=11")
    async with _client(factory, world.viewer) as client:
        viewer = await client.get(f"/api/v1/projects/{world.project.id}/data-jobs")
    assert _writes(factory.statements[before:]) == []
    assert exports.status_code == 200
    assert [item["id"] for item in exports.json()["items"]] == [str(rows.ready.id)] and exports.json()["total"] == 1
    item = exports.json()["items"][0]
    assert item["download_path"] == f"/api/v1/project-data-jobs/{rows.ready.id}/download"
    assert item["requested_by_name"] == "Lee"
    assert refused_imports.status_code == 403 and foreign.status_code == 403 and viewer.status_code == 403
    assert imports.status_code == 200 and [i["id"] for i in imports.json()["items"]] == [str(rows.mine.id)]
    assert too_many.status_code == 422


# ── the download ────────────────────────────────────────────────────────────

async def test_the_download_streams_the_stored_bytes_with_its_length(
    factory: Any, world: Any, storage: FakeStorage,
) -> None:
    rows = await _jobs_of_every_kind(factory, world, storage)
    async with _client(factory, world.lead) as client:
        answer = await client.get(f"/api/v1/project-data-jobs/{rows.ready.id}/download")
    assert answer.status_code == 200
    assert answer.content == storage.objects["project-exports/x/ready.zip"]
    assert answer.headers["content-length"] == "2504"
    assert answer.headers["content-type"] == "application/zip"
    stamp = rows.ready.finished_at.astimezone(timezone.utc).strftime("%Y%m%d-%H%M")
    assert answer.headers["content-disposition"] == f'attachment; filename="payment-service-settings-catalog-{stamp}.zip"'


@pytest.mark.parametrize(
    ("values", "status_code", "code"),
    [({"kind": "import", "status": "succeeded"}, 409, "not_an_export"),
     ({"kind": "export", "status": "queued"}, 409, "export_not_ready"),
     ({"kind": "export", "status": "running"}, 409, "export_not_ready"),
     ({"kind": "export", "status": "failed"}, 409, "export_failed"),
     ({"kind": "export", "status": "expired"}, 410, "export_expired"),
     ({"kind": "export", "status": "succeeded", "past": True}, 410, "export_expired"),
     ({"kind": "export", "status": "succeeded", "no_key": True}, 502, "archive_unreadable")],
)
async def test_each_download_refusal_reads_its_sentence(
    factory: Any, world: Any, storage: FakeStorage, values: dict, status_code: int, code: str,
) -> None:
    now = datetime.now(timezone.utc)
    expires = now - timedelta(minutes=1) if values.pop("past", False) else now + timedelta(hours=1)
    key = None if values.pop("no_key", False) else "project-exports/x/a.zip"
    job = await _job(factory, project_id=world.project.id, requested_by_id=world.admin.id, storage_key=key,
                     expires_at=expires, **values)
    before = len(factory.statements)
    async with _client(factory, world.admin) as client:
        answer = await client.get(f"/api/v1/project-data-jobs/{job.id}/download")
    assert answer.status_code == status_code
    assert answer.json()["detail"] == {"code": code, "message": vocab.ERROR_SENTENCES[code]}
    assert _writes(factory.statements[before:]) == []


async def test_a_storage_fault_before_the_first_byte_is_502(factory: Any, world: Any, storage: FakeStorage) -> None:
    rows = await _jobs_of_every_kind(factory, world, storage)
    storage.fail_stream = True
    async with _client(factory, world.admin) as client:
        answer = await client.get(f"/api/v1/project-data-jobs/{rows.ready.id}/download")
    assert answer.status_code == 502
    assert answer.json()["detail"] == {"code": "archive_unreadable", "message": vocab.ERROR_SENTENCES["archive_unreadable"]}


# ── the import POST ─────────────────────────────────────────────────────────

async def _import(client: AsyncClient, *, token: Any = None, body: bytes | None = None, name: str | None = None) -> Any:
    data = {"client_token": str(token or uuid.uuid4())}
    if name is not None:
        data["name"] = name
    files = None if body is None else {"file": ("export.zip", body, "application/zip")}
    return await client.post("/api/v1/projects/import", data=data, files=files)


async def test_an_import_is_stored_committed_then_dispatched(
    factory: Any, world: Any, storage: FakeStorage, dispatched: list,
) -> None:
    async with _client(factory, world.admin) as client:
        answer = await _import(client, body=_zip_bytes())
    assert answer.status_code == 202, answer.text
    body = answer.json()
    assert body["kind"] == "import" and body["status"] == "queued" and body["project_id"] is None
    assert dispatched == [("app.worker.tasks.import_project_data", body["id"])]
    (key,) = storage.objects
    assert key.startswith(vocab.STORAGE_PREFIX_IMPORT) and key.endswith(f"/{body['id']}.zip")
    async with factory() as db:
        row = await db.get(ProjectDataJob, uuid.UUID(body["id"]))
    assert row is not None and row.storage_key == key and row.requested_by_id == world.admin.id


async def test_a_repeated_token_answers_200_with_the_first_job_and_reads_nothing(
    factory: Any, world: Any, storage: FakeStorage, dispatched: list,
) -> None:
    token = uuid.uuid4()
    async with _client(factory, world.admin) as client:
        first = await _import(client, token=token, body=_zip_bytes())
        second = await _import(client, token=token, body=_zip_bytes() + b"different bytes")
    assert first.status_code == 202 and second.status_code == 200
    assert second.json()["id"] == first.json()["id"]
    assert await _count_jobs(factory) == 1 and len(storage.objects) == 1
    assert len(dispatched) == 1


async def test_an_upload_over_the_cap_is_413_before_any_job_row(
    factory: Any, world: Any, storage: FakeStorage, dispatched: list, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(vocab, "IMPORT_UPLOAD_MAX_BYTES", 64)
    before = len(factory.statements)
    async with _client(factory, world.admin) as client:
        answer = await _import(client, body=_zip_bytes() + b"x" * 200)
    assert answer.status_code == 413
    assert answer.json()["detail"] == {"code": "upload_too_large", "message": vocab.ERROR_SENTENCES["upload_too_large"]}
    assert not [s for s in factory.statements[before:] if "project_data_jobs" in s and s.lstrip().upper().startswith("INSERT")]
    assert await _count_jobs(factory) == 0 and storage.objects == {} and dispatched == []


@pytest.mark.parametrize(
    ("case", "code"),
    [("no_file", "upload_empty"), ("empty_file", "upload_empty"), ("not_zip", "not_a_zip"),
     ("bad_token", "client_token_invalid"), ("short_name", "name_invalid")],
)
async def test_each_422_reads_its_sentence_and_stores_nothing(
    factory: Any, world: Any, storage: FakeStorage, dispatched: list, case: str, code: str,
) -> None:
    async with _client(factory, world.admin) as client:
        if case == "no_file":
            answer = await _import(client)
        elif case == "empty_file":
            answer = await _import(client, body=b"")
        elif case == "not_zip":
            answer = await _import(client, body=b"%PDF-1.7 not a zip")
        elif case == "bad_token":
            answer = await client.post("/api/v1/projects/import", data={"client_token": "not-a-uuid"},
                                       files={"file": ("x.zip", _zip_bytes(), "application/zip")})
        else:
            answer = await _import(client, body=_zip_bytes(), name="x")
    assert answer.status_code == 422, answer.text
    assert answer.json()["detail"] == {"code": code, "message": vocab.ERROR_SENTENCES[code]}
    assert await _count_jobs(factory) == 0 and storage.objects == {} and dispatched == []


async def test_a_name_an_active_project_has_is_409(
    factory: Any, world: Any, storage: FakeStorage, dispatched: list,
) -> None:
    async with _client(factory, world.admin) as client:
        answer = await _import(client, body=_zip_bytes(), name="  payment service ")
    assert answer.status_code == 409
    assert answer.json()["detail"]["code"] == "name_taken"
    assert await _count_jobs(factory) == 0 and storage.objects == {}


@pytest.mark.parametrize("who", ["lead", "engineer", "viewer", "outsider"])
async def test_only_an_admin_imports(factory: Any, world: Any, storage: FakeStorage, dispatched: list, who: str) -> None:
    async with _client(factory, getattr(world, who)) as client:
        answer = await _import(client, body=_zip_bytes())
    assert answer.status_code == 403
    assert await _count_jobs(factory) == 0 and storage.objects == {} and dispatched == []


# ── W30: the export body (§3.2) ─────────────────────────────────────────────

HISTORY_LIMITS = {"time_limit": vocab.HISTORY_TIME_LIMIT_SECONDS, "soft_time_limit": vocab.HISTORY_TIME_LIMIT_SECONDS - 300}


async def test_a_history_export_stores_its_options_and_carries_the_history_time_limits(
    factory: Any, world: Any, dispatched: _Dispatched,
) -> None:
    async with _client(factory, world.lead) as client:
        answer = await client.post(f"/api/v1/projects/{world.project.id}/export",
                                   json={"include_history": True, "history_days": 365, "max_runs": 2000})
    assert answer.status_code == 202, answer.text
    assert answer.json()["options"] == {"include_history": True, "history_days": 365, "max_runs": 2000}
    assert dispatched == [("app.worker.tasks.export_project_data", answer.json()["id"])]
    assert dispatched.options == [HISTORY_LIMITS]
    async with factory() as db:
        row = await db.get(ProjectDataJob, uuid.UUID(answer.json()["id"]))
    assert row.options == {"include_history": True, "history_days": 365, "max_runs": 2000}


@pytest.mark.parametrize("body", [None, {"include_history": False}, {"include_history": False, "history_days": 30}])
async def test_no_body_or_no_history_is_the_phase_1_export(
    factory: Any, world: Any, dispatched: _Dispatched, body: Any,
) -> None:
    async with _client(factory, world.admin) as client:
        answer = await client.post(f"/api/v1/projects/{world.project.id}/export", json=body)
    assert answer.status_code == 202, answer.text
    assert dispatched.options == [{}]                   # the phase-1 payload: no per-message limits
    async with factory() as db:
        row = await db.get(ProjectDataJob, uuid.UUID(answer.json()["id"]))
    assert row.options == {}


@pytest.mark.parametrize(("body", "code"), [
    ({"include_history": True, "history_days": 7}, "history_window_invalid"),
    ({"include_history": True, "history_days": "90"}, "history_window_invalid"),
    ({"include_history": True, "max_runs": 0}, "max_runs_invalid"),
    ({"include_history": True, "max_runs": 2001}, "max_runs_invalid"),
])
async def test_a_bad_export_body_reads_its_sentence_and_creates_nothing(
    factory: Any, world: Any, dispatched: _Dispatched, body: dict, code: str,
) -> None:
    async with _client(factory, world.lead) as client:
        answer = await client.post(f"/api/v1/projects/{world.project.id}/export", json=body)
    assert answer.status_code == 422
    assert answer.json()["detail"] == {"code": code, "message": vocab.ERROR_SENTENCES[code]}
    assert dispatched == [] and await _count_jobs(factory) == 0


async def test_a_history_export_download_is_named_project_data(
    factory: Any, world: Any, storage: FakeStorage,
) -> None:
    now = datetime.now(timezone.utc)
    storage.objects["project-exports/x/history.zip"] = b"PK\x03\x04" + b"h" * 100
    job = await _job(factory, kind="export", status="succeeded", project_id=world.project.id, bytes=104,
                     requested_by_id=world.lead.id, storage_key="project-exports/x/history.zip",
                     finished_at=now, expires_at=now + timedelta(hours=72),
                     options={"include_history": True, "history_days": None, "max_runs": 2000})
    async with _client(factory, world.lead) as client:
        answer = await client.get(f"/api/v1/project-data-jobs/{job.id}/download")
    assert answer.status_code == 200 and answer.headers["content-length"] == "104"
    stamp = now.strftime("%Y%m%d-%H%M")
    assert answer.headers["content-disposition"] == f'attachment; filename="payment-service-project-data-{stamp}.zip"'


# ── W30: the estimate GET (§3.1) ────────────────────────────────────────────

@pytest.fixture
def estimates(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """The route's ``estimate`` call recorded (the counting itself is ``test_project_export_history.py``'s): it
    runs one SELECT on the request session, so the listener sees what a real estimate's reads look like."""
    from app.models.schemas import ProjectExportEstimate

    calls: list[dict[str, Any]] = []

    async def estimate(db: Any, mongo: Any, storage: Any, *, project_id: Any, options: Any) -> Any:
        await db.execute(select(Project.id).where(Project.id == project_id))
        try:
            mongo["raw_allure_json"]
            mongo_ok = True
        except Exception:  # noqa: BLE001
            mongo_ok = False
        calls.append({"project_id": project_id, "options": dict(options), "mongo_ok": mongo_ok})
        return ProjectExportEstimate(include_history=True, history_days=options["history_days"], runs=3,
                                     documents=None if not mongo_ok else {"raw_allure_json": 4}, objects=2,
                                     object_bytes=10, measured={"postgres": True, "documents": mongo_ok,
                                                                "objects": True})

    monkeypatch.setattr("app.services.project_export_history.estimate", estimate)
    monkeypatch.setattr("app.db.mongo.get_mongo_db", lambda: {"raw_allure_json": object()})
    return calls


@pytest.mark.parametrize(
    ("who", "expected"),
    [("admin", 200), ("lead", 200), ("outsider", 403), ("engineer", 403), ("viewer", 403), ("bound", 403),
     ("admin2", 200)],
)
async def test_the_estimate_follows_the_export_rule_and_writes_nothing(
    factory: Any, world: Any, storage: FakeStorage, estimates: list, who: str, expected: int,
) -> None:
    before = len(factory.statements)
    async with _client(factory, getattr(world, who)) as client:
        answer = await client.get(f"/api/v1/projects/{world.project.id}/export-estimate",
                                  params={"include_history": "true", "history_days": 90})
    assert answer.status_code == expected, answer.text
    assert _writes(factory.statements[before:]) == [], "the estimate GET wrote"
    assert await _count_jobs(factory) == 0 and storage.objects == {}
    if expected == 200:
        assert answer.json()["runs"] == 3 and answer.json()["measured"]["postgres"] is True
        assert estimates == [{"project_id": world.project.id, "mongo_ok": True,
                              "options": {"include_history": True, "history_days": 90, "max_runs": 2000}}]
    else:
        assert estimates == []


async def test_the_estimate_of_a_missing_project_is_404_and_absent_days_mean_all(
    factory: Any, world: Any, storage: FakeStorage, estimates: list,
) -> None:
    async with _client(factory, world.admin) as client:
        missing = await client.get(f"/api/v1/projects/{uuid.uuid4()}/export-estimate")
        everything = await client.get(f"/api/v1/projects/{world.project.id}/export-estimate")
    assert missing.status_code == 404 and missing.json()["detail"] == "Project not found"
    assert everything.status_code == 200
    assert estimates[0]["options"] == {"include_history": True, "history_days": None, "max_runs": 2000}


@pytest.mark.parametrize(("params", "code"), [
    ({"history_days": 7}, "history_window_invalid"), ({"history_days": "ninety"}, "history_window_invalid"),
    ({"max_runs": 0}, "max_runs_invalid"), ({"max_runs": 2001}, "max_runs_invalid"),
    ({"max_runs": "many"}, "max_runs_invalid"),
])
async def test_a_bad_estimate_query_reads_its_sentence(
    factory: Any, world: Any, estimates: list, params: dict, code: str,
) -> None:
    async with _client(factory, world.lead) as client:
        answer = await client.get(f"/api/v1/projects/{world.project.id}/export-estimate", params=params)
    assert answer.status_code == 422
    assert answer.json()["detail"] == {"code": code, "message": vocab.ERROR_SENTENCES[code]}
    assert estimates == []


# ── W37 F-W30-1: a release-scoped history export (§2.1) — the ratchet skips {project_id} routes: these ARE the guard ──

RELEASE_INVALID = {"code": "history_release_invalid", "message": "Choose a release of this project."}


@pytest.fixture
async def releases(factory: Any, world: Any) -> SimpleNamespace:
    """One release of the path project and one of ANOTHER project (one each: SQLite drops the partial WHEREs)."""
    own = Release(id=uuid.uuid4(), project_id=world.project.id, name="2026.11")
    foreign = Release(id=uuid.uuid4(), project_id=world.other.id, name="auth-1")
    async with factory() as db:
        db.add_all([own, foreign])
        await db.commit()
    return SimpleNamespace(own=own, foreign=foreign)


async def test_a_release_scoped_export_stores_and_echoes_only_the_keys_that_are_set(
    factory: Any, world: Any, releases: Any, dispatched: _Dispatched,
) -> None:
    async with _client(factory, world.lead) as client:
        answer = await client.post(f"/api/v1/projects/{world.project.id}/export", json={
            "include_history": True, "history_release_id": str(releases.own.id), "max_runs": 2000})
    assert answer.status_code == 202, answer.text
    expected = {"include_history": True, "history_days": None, "max_runs": 2000,
                "history_release_id": str(releases.own.id)}
    assert answer.json()["options"] == expected
    assert dispatched.options == [HISTORY_LIMITS]
    async with factory() as db:
        row = await db.get(ProjectDataJob, uuid.UUID(answer.json()["id"]))
    assert row.options == expected
    from app.services.project_data_job_service import job_options

    assert job_options(row).history_release_id == releases.own.id     # what the worker reads back


@pytest.mark.parametrize("which", ["another_projects_release", "a_random_uuid"])
async def test_a_release_outside_the_path_project_is_one_refusal_on_both_routes_and_writes_nothing(
    factory: Any, world: Any, releases: Any, dispatched: _Dispatched, estimates: list, storage: FakeStorage,
    which: str,
) -> None:
    release = releases.foreign.id if which == "another_projects_release" else uuid.uuid4()
    before = len(factory.statements)
    async with _client(factory, world.admin) as client:      # an ADMIN: access to every project, still refused
        post = await client.post(f"/api/v1/projects/{world.project.id}/export",
                                 json={"include_history": True, "history_release_id": str(release)})
        estimate = await client.get(f"/api/v1/projects/{world.project.id}/export-estimate",
                                    params={"history_release_id": str(release)})
    assert (post.status_code, post.json()["detail"]) == (422, RELEASE_INVALID)          # no existence oracle:
    assert (estimate.status_code, estimate.json()["detail"]) == (422, RELEASE_INVALID)  # the same body for both
    assert dispatched == [] and estimates == [] and await _count_jobs(factory) == 0
    assert _writes(factory.statements[before:]) == []
    checks = [s for s in factory.statements[before:] if "FROM releases" in s]
    assert len(checks) == 2 and all("releases.project_id = " in s for s in checks)     # scoped by BOTH ids


@pytest.mark.parametrize("body", [
    {"history_release_id": "RELEASE"},                                             # include_history absent
    {"include_history": False, "history_release_id": "RELEASE"},
    {"include_history": True, "history_days": 90, "history_release_id": "RELEASE"},  # a day window AND a release
    {"include_history": True, "history_release_id": "not-a-uuid"},
    {"include_history": True, "history_release_id": 7},
])
async def test_a_release_without_history_with_a_day_window_or_not_an_id_is_refused(
    factory: Any, world: Any, releases: Any, dispatched: _Dispatched, body: dict,
) -> None:
    sent = {k: (str(releases.own.id) if v == "RELEASE" else v) for k, v in body.items()}
    async with _client(factory, world.lead) as client:
        answer = await client.post(f"/api/v1/projects/{world.project.id}/export", json=sent)
    assert (answer.status_code, answer.json()["detail"]) == (422, RELEASE_INVALID)
    assert dispatched == [] and await _count_jobs(factory) == 0


async def test_the_estimate_of_a_release_passes_it_on_and_refuses_it_with_days_or_without_history(
    factory: Any, world: Any, releases: Any, estimates: list,
) -> None:
    own = str(releases.own.id)
    before = len(factory.statements)
    async with _client(factory, world.lead) as client:
        ok = await client.get(f"/api/v1/projects/{world.project.id}/export-estimate",
                              params={"history_release_id": own})
        with_days = await client.get(f"/api/v1/projects/{world.project.id}/export-estimate",
                                     params={"history_release_id": own, "history_days": 90})
        no_history = await client.get(f"/api/v1/projects/{world.project.id}/export-estimate",
                                      params={"history_release_id": own, "include_history": "false"})
        not_an_id = await client.get(f"/api/v1/projects/{world.project.id}/export-estimate",
                                     params={"history_release_id": "nope"})
    assert ok.status_code == 200, ok.text
    assert estimates == [{"project_id": world.project.id, "mongo_ok": True, "options": {
        "include_history": True, "history_days": None, "max_runs": 2000, "history_release_id": releases.own.id}}]
    for refused in (with_days, no_history, not_an_id):
        assert (refused.status_code, refused.json()["detail"]) == (422, RELEASE_INVALID)
    assert _writes(factory.statements[before:]) == [], "the estimate GET wrote"


async def test_an_unconfigurable_mongo_is_unmeasured_not_a_500(
    factory: Any, world: Any, storage: FakeStorage, estimates: list, monkeypatch: pytest.MonkeyPatch,
) -> None:
    def broken() -> Any:
        raise RuntimeError("no MONGO_URI")

    monkeypatch.setattr("app.db.mongo.get_mongo_db", broken)
    async with _client(factory, world.lead) as client:
        answer = await client.get(f"/api/v1/projects/{world.project.id}/export-estimate")
    assert answer.status_code == 200
    assert answer.json()["measured"]["documents"] is False and answer.json()["documents"] is None
    assert estimates[0]["mongo_ok"] is False


# ── W30: the parted import upload (§3.1) ────────────────────────────────────

UPLOADS = "/api/v1/project-imports/uploads"
PART = 64          # the test-sized IMPORT_PART_MAX_BYTES (the 413 test uses the real 32 MiB)


def _sha(data: bytes) -> str:
    import hashlib

    return hashlib.sha256(data).hexdigest()


@pytest.fixture
def small_parts(monkeypatch: pytest.MonkeyPatch) -> int:
    from app.services import project_data_job_service

    monkeypatch.setattr(vocab, "IMPORT_PART_MAX_BYTES", PART)
    monkeypatch.setattr(project_data_job_service, "IMPORT_PART_MAX_BYTES", PART)
    return PART


def _archive(size: int) -> bytes:
    return bytes((n * 7 + 3) % 251 for n in range(size))


async def _start(client: AsyncClient, data: bytes, *, token: Any = None, name: Any = None, part: int = PART) -> Any:
    return await client.post(UPLOADS, json={
        "client_token": str(token or uuid.uuid4()), "name": name, "bytes": len(data),
        "parts": -(-len(data) // part), "sha256": _sha(data),
    })


async def _put(client: AsyncClient, job_id: Any, number: int, chunk: Any, *, digest: Any = "auto") -> Any:
    headers = {"Content-Type": "application/octet-stream"}
    if digest == "auto":
        headers["X-Part-Sha256"] = _sha(chunk)
    elif digest is not None:
        headers["X-Part-Sha256"] = digest
    return await client.put(f"{UPLOADS}/{job_id}/parts/{number}", content=chunk, headers=headers)


def _parts_of(data: bytes, part: int = PART) -> list[bytes]:
    return [data[start:start + part] for start in range(0, len(data), part)]


async def test_a_parted_upload_starts_takes_its_parts_and_completes(
    factory: Any, world: Any, storage: FakeStorage, dispatched: _Dispatched, small_parts: int,
) -> None:
    from app.services.compliance_pack_service import COMPLIANCE_BUCKET

    data = _archive(PART * 2 + 10)
    async with _client(factory, world.admin) as client:
        started = await _start(client, data, name="Payment copy")
        assert started.status_code == 201, started.text
        job = started.json()
        assert job["status"] == "uploading" and job["kind"] == "import" and job["project_name"] == "Payment copy"
        assert job["upload"] == {"parts": 3, "bytes": len(data), "received": [], "received_bytes": 0}
        receipts = [await _put(client, job["id"], n, chunk) for n, chunk in enumerate(_parts_of(data), start=1)]
        before = len(factory.statements)
        polled = await client.get(f"/api/v1/project-data-jobs/{job['id']}")
        assert _writes(factory.statements[before:]) == []
        completed = await client.post(f"{UPLOADS}/{job['id']}/complete")
        again = await client.post(f"{UPLOADS}/{job['id']}/complete")
    assert [r.status_code for r in receipts] == [200, 200, 200]
    assert receipts[-1].json() == {"part": 3, "bytes": 10, "received": [1, 2, 3], "complete": True}
    assert polled.json()["upload"]["received"] == [1, 2, 3]
    assert storage.puts == [(f"project-imports/parts/{job['id']}/part-{n:04d}", COMPLIANCE_BUCKET,
                             "application/octet-stream") for n in (1, 2, 3)]
    assert b"".join(storage.objects[f"project-imports/parts/{job['id']}/part-{n:04d}"] for n in (1, 2, 3)) == data
    assert completed.status_code == 202 and completed.json()["status"] == "queued"
    assert again.status_code == 200 and again.json()["status"] == "queued"
    assert dispatched == [("app.worker.tasks.import_project_data", job["id"])]     # ONE enqueue
    assert dispatched.options == [HISTORY_LIMITS]


async def test_a_re_sent_start_answers_the_open_upload_with_its_received_parts(
    factory: Any, world: Any, storage: FakeStorage, dispatched: _Dispatched, small_parts: int,
) -> None:
    data, token = _archive(PART * 2), uuid.uuid4()
    async with _client(factory, world.admin) as client:
        first = await _start(client, data, token=token)
        await _put(client, first.json()["id"], 1, data[:PART])
        second = await _start(client, data, token=token)
        other_admin = await _start(_client(factory, world.admin2), data, token=token)
    assert first.status_code == 201 and second.status_code == 200
    assert second.json()["id"] == first.json()["id"] and second.json()["upload"]["received"] == [1]
    assert other_admin.status_code == 201 and other_admin.json()["id"] != first.json()["id"]   # tokens are per requester
    assert await _count_jobs(factory) == 2


async def test_the_name_is_checked_after_the_token(
    factory: Any, world: Any, storage: FakeStorage, small_parts: int,
) -> None:
    data, token = _archive(PART), uuid.uuid4()
    async with _client(factory, world.admin) as client:
        taken = await _start(client, data, name="  payment service ")
        invalid = await _start(client, data, name="x")
        started = await _start(client, data, token=token, name="Billing copy")
        async with factory() as db:              # the name now exists (say, the first import created it)
            db.add(Project(id=uuid.uuid4(), name="Billing copy", slug="billing-copy", is_active=True))
            await db.commit()
        resent = await _start(client, data, token=token, name="Billing copy")
    assert taken.status_code == 409 and taken.json()["detail"]["code"] == "name_taken"
    assert invalid.status_code == 422 and invalid.json()["detail"] == {
        "code": "name_invalid", "message": vocab.ERROR_SENTENCES["name_invalid"]}
    assert started.status_code == 201 and resent.status_code == 200      # the token answers before the name
    assert await _count_jobs(factory) == 1


@pytest.mark.parametrize("body", [
    {"bytes": PART + 1, "parts": 1}, {"bytes": PART, "parts": 2}, {"bytes": PART * 65, "parts": 65},
])
async def test_a_bad_part_plan_reads_its_sentence(
    factory: Any, world: Any, small_parts: int, body: dict,
) -> None:
    async with _client(factory, world.admin) as client:
        answer = await client.post(UPLOADS, json={"client_token": str(uuid.uuid4()), "sha256": "a" * 64, **body})
    assert answer.status_code == 422
    assert answer.json()["detail"] == {"code": "upload_parts_invalid",
                                       "message": vocab.ERROR_SENTENCES["upload_parts_invalid"]}
    assert await _count_jobs(factory) == 0


@pytest.mark.parametrize("who", ["lead", "engineer", "viewer", "outsider", "bound"])
async def test_only_an_admin_starts_an_upload(factory: Any, world: Any, small_parts: int, who: str) -> None:
    async with _client(factory, getattr(world, who)) as client:
        answer = await _start(client, _archive(PART))
    assert answer.status_code == 403
    assert await _count_jobs(factory) == 0


async def _open_upload(factory: Any, world: Any, data: bytes) -> str:
    async with _client(factory, world.admin) as client:
        started = await _start(client, data)
    assert started.status_code == 201, started.text
    return str(started.json()["id"])


@pytest.mark.parametrize(
    ("who", "target", "expected"),
    [("admin", "mine", 200), ("admin2", "mine", 404), ("admin", "unknown", 404), ("admin", "export", 404),
     ("lead", "mine", 403), ("engineer", "mine", 403), ("viewer", "mine", 403), ("outsider", "mine", 403),
     ("bound", "mine", 403), ("lead", "export", 403)],
)
async def test_the_part_and_complete_routes_share_one_404(
    factory: Any, world: Any, storage: FakeStorage, dispatched: _Dispatched, small_parts: int,
    who: str, target: str, expected: int,
) -> None:
    """Another admin's upload, an unknown id and an EXPORT job (visible to its project's leads through
    ``load_job_for``) read the SAME 404 body; a role below ADMIN (and a project-bound key) is refused by the role
    guard first, with the guard's own text and the same answer for every id."""
    data = _archive(PART)
    mine = await _open_upload(factory, world, data)
    export = await _job(factory, kind="export", status="succeeded", project_id=world.project.id,
                        requested_by_id=world.lead.id)
    job_id = {"mine": mine, "unknown": str(uuid.uuid4()), "export": str(export.id)}[target]
    async with _client(factory, getattr(world, who)) as client:
        put = await _put(client, job_id, 1, data)
        complete = await client.post(f"{UPLOADS}/{job_id}/complete")
    assert put.status_code == expected, put.text
    if expected == 404:
        assert put.json() == NOT_FOUND and complete.status_code == 404 and complete.json() == NOT_FOUND
        assert storage.objects == {}
    elif expected == 403:
        guard = ("This API key is bound to one project; this endpoint spans every project and needs an instance "
                 "administrator") if who == "bound" else f"Requires at least {UserRole.ADMIN.value} role"
        assert put.json()["detail"] == complete.json()["detail"] == guard
        assert storage.objects == {}
    else:
        assert complete.status_code == 202
    assert dispatched == ([] if expected != 200 else [("app.worker.tasks.import_project_data", mine)])


async def test_a_part_over_32_mib_is_413_and_nothing_is_stored(
    factory: Any, world: Any, storage: FakeStorage,
) -> None:
    """The REAL cap: 32 MiB + 1 byte, sent with its Content-Length AND streamed without one (counted by the route
    itself — the urlencoded-form cap of test_request_hardening does not apply to a raw octet-stream body)."""
    cap = vocab.IMPORT_PART_MAX_BYTES
    assert cap == 32 * 1024 * 1024
    data_size = cap + 10
    async with _client(factory, world.admin) as client:
        started = await client.post(UPLOADS, json={"client_token": str(uuid.uuid4()), "bytes": data_size, "parts": 2,
                                                   "sha256": "b" * 64})
        job_id = started.json()["id"]
        oversized = bytes(cap + 1)
        declared = await _put(client, job_id, 1, oversized)

        async def stream() -> Any:
            for _ in range(32):
                yield bytes(1024 * 1024)
            yield b"\x00"

        streamed = await client.put(f"{UPLOADS}/{job_id}/parts/1", content=stream(),
                                    headers={"Content-Type": "application/octet-stream", "X-Part-Sha256": "c" * 64})
        polled = await client.get(f"/api/v1/project-data-jobs/{job_id}")
    expected = {"code": "part_too_large", "message": vocab.ERROR_SENTENCES["part_too_large"]}
    assert declared.status_code == 413 and declared.json()["detail"] == expected
    assert streamed.status_code == 413 and streamed.json()["detail"] == expected
    assert storage.objects == {} and storage.puts == []
    assert polled.json()["upload"]["received"] == []


@pytest.mark.parametrize("digest", ["wrong", None, "not-hex", "A" * 63])
async def test_a_digest_mismatch_or_a_bad_header_stores_nothing(
    factory: Any, world: Any, storage: FakeStorage, small_parts: int, digest: Any,
) -> None:
    data = _archive(PART * 2)
    job_id = await _open_upload(factory, world, data)
    header = _sha(b"some other bytes") if digest == "wrong" else digest
    async with _client(factory, world.admin) as client:
        answer = await _put(client, job_id, 1, data[:PART], digest=header)
        polled = await client.get(f"/api/v1/project-data-jobs/{job_id}")
    assert answer.status_code == 422
    assert answer.json()["detail"] == {"code": "part_digest_mismatch",
                                       "message": "Part 1 does not match its SHA-256; send it again."}
    assert storage.objects == {} and polled.json()["upload"]["received"] == []


@pytest.mark.parametrize("number", [0, 3, 64])
async def test_a_part_number_outside_the_plan_is_422(
    factory: Any, world: Any, storage: FakeStorage, small_parts: int, number: int,
) -> None:
    data = _archive(PART * 2)
    job_id = await _open_upload(factory, world, data)
    async with _client(factory, world.admin) as client:
        answer = await _put(client, job_id, number, data[:PART])
    assert answer.status_code == 422
    assert answer.json()["detail"] == {"code": "part_number_invalid",
                                       "message": f"Part {number} is not part of this upload (1 to 2)."}
    assert storage.objects == {}


async def test_a_repeated_part_is_a_no_op_and_other_content_conflicts(
    factory: Any, world: Any, storage: FakeStorage, small_parts: int,
) -> None:
    data = _archive(PART * 2)
    job_id = await _open_upload(factory, world, data)
    async with _client(factory, world.admin) as client:
        first = await _put(client, job_id, 1, data[:PART])
        repeat = await _put(client, job_id, 1, data[:PART])
        other = await _put(client, job_id, 1, bytes(reversed(data[:PART])))
    assert first.status_code == repeat.status_code == 200
    assert repeat.json() == {"part": 1, "bytes": PART, "received": [1], "complete": False}
    assert len(storage.puts) == 1                                   # the repeat rewrote nothing
    assert other.status_code == 409
    assert other.json()["detail"] == {"code": "part_conflict",
                                      "message": "Part 1 was already received with different content."}
    assert storage.objects[f"project-imports/parts/{job_id}/part-0001"] == data[:PART]


async def test_a_part_of_the_wrong_size_is_refused_and_not_stored(
    factory: Any, world: Any, storage: FakeStorage, small_parts: int,
) -> None:
    data = _archive(PART * 2)
    job_id = await _open_upload(factory, world, data)
    async with _client(factory, world.admin) as client:
        short = await _put(client, job_id, 1, data[:PART - 1])
    assert short.status_code == 422 and short.json()["detail"]["code"] == "upload_parts_invalid"
    assert storage.objects == {}


async def test_complete_names_the_missing_parts_and_a_closed_upload_refuses(
    factory: Any, world: Any, storage: FakeStorage, dispatched: _Dispatched, small_parts: int,
) -> None:
    data = _archive(PART * 3)
    job_id = await _open_upload(factory, world, data)
    closed = await _job(factory, kind="import", status="failed", requested_by_id=world.admin.id,
                        client_token=uuid.uuid4(), options={"parted": True, "parts": 1, "bytes": PART,
                                                            "sha256": "d" * 64})
    async with _client(factory, world.admin) as client:
        await _put(client, job_id, 2, data[PART:PART * 2])
        incomplete = await client.post(f"{UPLOADS}/{job_id}/complete")
        late_part = await _put(client, closed.id, 1, data[:PART])
        late_complete = await client.post(f"{UPLOADS}/{closed.id}/complete")
        polled = await client.get(f"/api/v1/project-data-jobs/{job_id}")
    assert incomplete.status_code == 409
    assert incomplete.json()["detail"] == {"code": "upload_incomplete",
                                           "message": "The upload is missing parts 1, 3.", "missing": [1, 3]}
    assert polled.json()["status"] == "uploading" and dispatched == []
    not_open = {"code": "upload_not_open", "message": vocab.ERROR_SENTENCES["upload_not_open"]}
    assert late_part.status_code == 409 and late_part.json()["detail"] == not_open
    assert late_complete.status_code == 409 and late_complete.json()["detail"] == not_open
    assert set(storage.objects) == {f"project-imports/parts/{job_id}/part-0002"}
