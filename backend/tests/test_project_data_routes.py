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
from app.models.postgres import Base, Project, ProjectDataJob, ProjectMember, User, UserRole
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


_TABLES = [Project.__table__, User.__table__, ProjectMember.__table__, ProjectDataJob.__table__]
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

    async def put_object_stream(self, key: str, fileobj: Any, *, size: int, content_type: str, bucket: str) -> None:
        fileobj.seek(0)
        self.objects[key] = fileobj.read()

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


@pytest.fixture
def dispatched(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    """``apply_async`` recorders that bind the REAL payload to each task's signature."""
    calls: list[tuple[str, str]] = []
    for task in (tasks.export_project_data, tasks.import_project_data):
        def apply_async(args: Any = None, kwargs: Any = None, _task: Any = task, **_options: Any) -> Any:
            inspect.signature(_task.run).bind(*(args or ()), **(kwargs or {}))
            calls.append((_task.name, *(args or ())))
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
