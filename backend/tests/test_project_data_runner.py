"""W24 ADM-05 — the project-data worker side (contract §2.2 E-2 / E-5 / E-6 / E-7, §2.3 I-1 / I-8).

On an in-memory SQLite database (projects, users, project_data_jobs): the claim makes a redelivery a
no-op; a failure is recorded on a FRESH session after the work's rollback; the success commits the outcome
with ``project.exported``; the janitor expires / removes / fails stale / re-dispatches queued jobs, the
re-dispatch through ``apply_async`` recorders that BIND the payload to the task's real signature (a mocked
``apply_async`` that accepted anything would hide Celery's argument check). The counter is read before /
after each case: Celery reports success for a job that caught its own failure.
"""
from __future__ import annotations

import inspect
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import event, text
from sqlalchemy import types as sqltypes
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.sqlite import DATETIME as SQLITE_DATETIME
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.pool import StaticPool

from app.core.metrics import project_data_jobs_total
from app.models.postgres import Base, Project, ProjectDataJob, User, UserRole
from app.services import project_export_service, project_import_service
from app.services import project_export_vocab as vocab
from app.worker import project_data_runner as runner
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


class FakeStorage:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.deleted: list[str] = []
        self.listing: list[dict] = []
        self.fail_delete = False

    async def delete_object(self, key: str, bucket: str | None = None) -> None:
        if self.fail_delete:
            raise ConnectionRefusedError("storage down")
        self.deleted.append(key)
        self.objects.pop(key, None)

    async def list_objects(self, prefix: str, bucket: str | None = None) -> list[dict]:
        return [entry for entry in self.listing if str(entry["Key"]).startswith(prefix)]


class SessionLog:
    """Wraps the factory: every session opened, in order, with the statements it ran."""

    def __init__(self, maker: Any) -> None:
        self.maker = maker
        self.sessions: list[list[str]] = []

    def __call__(self) -> Any:
        session = self.maker()
        log: list[str] = []
        self.sessions.append(log)
        session.info["log"] = log
        return session


@pytest.fixture
async def factory(monkeypatch: pytest.MonkeyPatch) -> Any:
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:", poolclass=StaticPool, connect_args={"check_same_thread": False},
    )
    dialect = engine.sync_engine.dialect
    dialect.colspecs = {**dialect.colspecs, sqltypes.DateTime: _AwareDateTime}
    # ONE shared connection with the driver's implicit transactions: the runner opens fresh sessions
    # while its work session is open (progress, the stored key), as it does on Postgres.
    monkeypatch.setattr(ProjectDataJob.__table__.c.counts, "server_default", None)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all,
                            tables=[Project.__table__, User.__table__, ProjectDataJob.__table__])
        for name in ("uq_pdj_one_live_export", "uq_pdj_import_token"):
            await conn.execute(text(f"DROP INDEX IF EXISTS {name}"))
    log = SessionLog(async_sessionmaker(engine, expire_on_commit=False))

    from sqlalchemy.orm import Session

    @event.listens_for(Session, "do_orm_execute")
    def _orm(state: Any) -> None:
        target = state.session.info.get("log")
        if target is not None:
            target.append(str(state.statement).split()[0].upper())

    yield log
    event.remove(Session, "do_orm_execute", _orm)
    await engine.dispose()


@pytest.fixture
async def world(factory: Any) -> SimpleNamespace:
    project = Project(id=uuid.uuid4(), name="Payment Service", slug="payment-service", is_active=True)
    admin = User(id=uuid.uuid4(), username="ada", email="ada@example.test", full_name="Ada",
                 hashed_password="x", role=UserRole.ADMIN.value, is_active=True)
    async with factory.maker() as db:
        db.add_all([project, admin])
        await db.commit()
    return SimpleNamespace(project=project, admin=admin)


async def _job(factory: Any, **values: Any) -> ProjectDataJob:
    values.setdefault("requested_at", datetime.now(timezone.utc))
    row = ProjectDataJob(id=uuid.uuid4(), counts={}, attempts=values.pop("attempts", 0), **values)
    async with factory.maker() as db:
        db.add(row)
        await db.commit()
    return row


async def _read(factory: Any, job_id: uuid.UUID) -> ProjectDataJob:
    async with factory.maker() as db:
        row = await db.get(ProjectDataJob, job_id)
        assert row is not None
        return row


def _count(kind: str, outcome: str) -> float:
    return project_data_jobs_total.labels(kind=kind, outcome=outcome)._value.get()


@pytest.fixture
def recorded(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    calls: list[tuple[str, str]] = []
    for task in (tasks.export_project_data, tasks.import_project_data):
        def apply_async(args: Any = None, kwargs: Any = None, _task: Any = task, **_options: Any) -> Any:
            inspect.signature(_task.run).bind(*(args or ()), **(kwargs or {}))
            calls.append((_task.name, *(args or ())))
            return SimpleNamespace(id="celery-id")
        monkeypatch.setattr(task, "apply_async", apply_async)
    return calls


@pytest.fixture
def activity(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []

    async def record(db: Any, **kwargs: Any) -> None:
        events.append(kwargs)

    monkeypatch.setattr("app.services.activity.service.record", record)
    return events


def _outcome(**extra: Any) -> project_export_service.ProjectExportOutcome:
    return project_export_service.ProjectExportOutcome(
        key="project-exports/2026/10/30/p/j.zip", bytes=4096, file_count=12, manifest_sha256="m" * 64,
        archive_sha256="a" * 64, signature="signed", signing_key_id="k-1", counts={"test_suites": 3, "projects": 1},
        **extra,
    )


# ── export ──────────────────────────────────────────────────────────────────

async def test_a_redelivered_export_is_a_no_op(
    factory: Any, world: Any, monkeypatch: pytest.MonkeyPatch, activity: list,
) -> None:
    job = await _job(factory, kind="export", status="running", project_id=world.project.id,
                     started_at=datetime.now(timezone.utc))

    async def must_not_run(*_: Any, **__: Any) -> Any:
        raise AssertionError("a redelivery rebuilt the export")

    monkeypatch.setattr(project_export_service, "run_project_export", must_not_run)
    before = (_count("export", "succeeded"), _count("export", "failed"))
    assert await runner.run_export_job(job.id, storage=FakeStorage(), session_factory=factory) == "skipped"
    assert (await _read(factory, job.id)).status == "running"
    assert (_count("export", "succeeded"), _count("export", "failed")) == before
    assert activity == []


async def test_an_export_succeeds_with_one_outcome_commit_and_its_activity(
    factory: Any, world: Any, monkeypatch: pytest.MonkeyPatch, activity: list,
) -> None:
    job = await _job(factory, kind="export", status="queued", project_id=world.project.id,
                     requested_by_id=world.admin.id)
    seen: dict[str, Any] = {}

    async def fake_export(db: Any, **kwargs: Any) -> Any:
        seen.update(kwargs, project_id=kwargs["project"].id, same_session=kwargs["sign_db"] is db)
        assert (await _read(factory, job.id)).status == "running", "the claim was not committed first"
        await kwargs["on_table"](1, 30)
        await kwargs["on_written"](_outcome().key)
        return _outcome()

    monkeypatch.setattr(project_export_service, "run_project_export", fake_export)
    before = _count("export", "succeeded")
    assert await runner.run_export_job(job.id, storage=FakeStorage(), session_factory=factory) == "succeeded"
    row = await _read(factory, job.id)
    assert row.status == "succeeded" and row.counts == {"test_suites": 3, "projects": 1}
    assert row.signature == "signed" and row.bytes == 4096 and row.storage_key == _outcome().key
    assert row.expires_at - row.finished_at == timedelta(hours=vocab.EXPORT_TTL_HOURS)
    assert seen["sign_db"] is not None and seen["same_session"] is False   # the signing key never on the snapshot
    assert seen["project_id"] == world.project.id
    assert [e["event_type"] for e in activity] == ["project.exported"]
    assert activity[0]["entity_label"] == "Payment Service" and activity[0]["context"] == {"rows": 4}
    assert _count("export", "succeeded") == before + 1


async def test_a_failure_is_recorded_on_a_fresh_session_after_the_rollback(
    factory: Any, world: Any, monkeypatch: pytest.MonkeyPatch, activity: list,
) -> None:
    job = await _job(factory, kind="export", status="queued", project_id=world.project.id,
                     requested_by_id=world.admin.id)
    snapshot_index: list[int] = []

    async def failing_export(db: Any, **kwargs: Any) -> Any:
        snapshot_index.append(len(factory.sessions) - 2)       # the snapshot, then sign_db, were opened last
        raise project_export_service.ProjectExportFailed("export_unverified", reason="digest mismatch")

    monkeypatch.setattr(project_export_service, "run_project_export", failing_export)
    before = _count("export", "failed")
    assert await runner.run_export_job(job.id, storage=FakeStorage(), session_factory=factory) == "failed"
    row = await _read(factory, job.id)
    assert row.status == "failed" and row.error_code == "export_unverified"
    assert row.error == "The stored export could not be verified (digest mismatch)."
    failing = [i for i, log in enumerate(factory.sessions) if "UPDATE" in log and i > snapshot_index[0]]
    assert failing, "the failure was not written on a session opened after the work's"
    assert "UPDATE" not in factory.sessions[snapshot_index[0]], "the snapshot session wrote"
    assert activity == [] and _count("export", "failed") == before + 1


async def test_an_unexpected_error_records_its_type_name_only(
    factory: Any, world: Any, monkeypatch: pytest.MonkeyPatch, activity: list,
) -> None:
    job = await _job(factory, kind="export", status="queued", project_id=world.project.id)

    async def boom(db: Any, **kwargs: Any) -> Any:
        raise RuntimeError("row value SENTINELrowValue must not travel")

    monkeypatch.setattr(project_export_service, "run_project_export", boom)
    assert await runner.run_export_job(job.id, storage=FakeStorage(), session_factory=factory) == "failed"
    row = await _read(factory, job.id)
    assert row.error == "The export could not be built (RuntimeError)."
    assert "SENTINEL" not in (row.error or "")


# ── import ──────────────────────────────────────────────────────────────────

async def test_an_import_failure_rolls_back_records_and_removes_the_upload(
    factory: Any, world: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    key = "project-imports/2026/10/30/upload.zip"
    store = FakeStorage()
    store.objects[key] = b"PK"
    job = await _job(factory, kind="import", status="queued", requested_by_id=world.admin.id,
                     client_token=uuid.uuid4(), storage_key=key)

    async def refused(db: Any, **kwargs: Any) -> Any:
        db.add(Project(id=uuid.uuid4(), name="half", slug="half-created"))
        await db.flush()
        raise project_import_service.ProjectImportFailed("tampered", reason="a listed part is missing")

    monkeypatch.setattr(project_import_service, "run_import", refused)
    before = _count("import", "failed")
    assert await runner.run_import_job(job.id, storage=store, session_factory=factory) == "failed"
    row = await _read(factory, job.id)
    assert row.status == "failed" and row.error_code == "tampered"
    assert row.error == vocab.ERROR_SENTENCES["tampered"].format(reason="a listed part is missing")
    assert store.deleted == [key] and row.storage_key is None
    async with factory.maker() as db:
        assert (await db.execute(text("SELECT count(*) FROM projects WHERE slug = 'half-created'"))).scalar() == 0
    assert _count("import", "failed") == before + 1


async def test_an_import_success_commits_invalidates_caches_and_removes_the_upload(
    factory: Any, world: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    key = "project-imports/2026/10/30/ok.zip"
    store = FakeStorage()
    job = await _job(factory, kind="import", status="queued", requested_by_id=world.admin.id,
                     client_token=uuid.uuid4(), storage_key=key)
    service_account = uuid.uuid4()
    invalidated: list[uuid.UUID] = []

    async def imported(db: Any, *, job: Any, outcome: dict, **kwargs: Any) -> Any:
        await kwargs["on_manifest"]("Payment Service", 30)
        row = await db.get(ProjectDataJob, job.id)
        row.status = "succeeded"
        outcome["enrolled_service_account_ids"] = [service_account]
        return SimpleNamespace(project_id=uuid.uuid4(), inserted={"projects": 1, "test_suites": 2})

    async def invalidate(user_id: uuid.UUID) -> None:
        invalidated.append(user_id)

    monkeypatch.setattr(project_import_service, "run_import", imported)
    monkeypatch.setattr("app.core.deps.invalidate_membership_cache", invalidate)
    before = _count("import", "succeeded")
    assert await runner.run_import_job(job.id, storage=store, session_factory=factory) == "succeeded"
    row = await _read(factory, job.id)
    assert row.status == "succeeded" and row.project_name == "Payment Service"
    assert invalidated == [world.admin.id, service_account]
    assert store.deleted == [key] and row.storage_key is None
    assert _count("import", "succeeded") == before + 1


async def test_a_redelivered_import_is_a_no_op(factory: Any, world: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    job = await _job(factory, kind="import", status="succeeded", requested_by_id=world.admin.id,
                     client_token=uuid.uuid4())

    async def must_not_run(*_: Any, **__: Any) -> Any:
        raise AssertionError("a redelivery re-imported")

    monkeypatch.setattr(project_import_service, "run_import", must_not_run)
    assert await runner.run_import_job(job.id, storage=FakeStorage(), session_factory=factory) == "skipped"


# ── the janitor ─────────────────────────────────────────────────────────────

async def test_the_janitor_expires_removes_fails_stale_and_redispatches(
    factory: Any, world: Any, recorded: list,
) -> None:
    now = datetime.now(timezone.utc)
    store = FakeStorage()
    for key in ("exp.zip", "fresh.zip", "upload-done.zip", "failed-export.zip", "stale.zip"):
        store.objects[key] = b"PK"
    expired = await _job(factory, kind="export", status="succeeded", project_id=world.project.id,
                         storage_key="exp.zip", finished_at=now - timedelta(hours=73), expires_at=now - timedelta(hours=1))
    fresh = await _job(factory, kind="export", status="succeeded", project_id=world.project.id,
                       storage_key="fresh.zip", finished_at=now, expires_at=now + timedelta(hours=72))
    upload_done = await _job(factory, kind="import", status="succeeded", requested_by_id=world.admin.id,
                             client_token=uuid.uuid4(), storage_key="upload-done.zip")
    failed_export = await _job(factory, kind="export", status="failed", project_id=world.project.id,
                               storage_key="failed-export.zip")
    stale = await _job(factory, kind="export", status="running", project_id=world.project.id, storage_key="stale.zip",
                       started_at=now - timedelta(minutes=vocab.JOB_STALE_MINUTES + 1))
    slow = await _job(factory, kind="import", status="running", requested_by_id=world.admin.id,
                      client_token=uuid.uuid4(), started_at=now - timedelta(minutes=vocab.JOB_STALE_MINUTES - 5))
    lost = await _job(factory, kind="import", status="queued", requested_by_id=world.admin.id, client_token=uuid.uuid4(),
                      requested_at=now - timedelta(minutes=vocab.QUEUED_REDISPATCH_MINUTES + 1))
    lost_export = await _job(factory, kind="export", status="queued", project_id=world.project.id, attempts=1,
                             requested_at=now - timedelta(minutes=vocab.QUEUED_REDISPATCH_MINUTES + 1))
    spent = await _job(factory, kind="export", status="queued", project_id=world.project.id, attempts=runner.REDISPATCH_MAX,
                       requested_at=now - timedelta(hours=3))
    recent = await _job(factory, kind="export", status="queued", project_id=world.project.id, requested_at=now)
    store.listing = [
        {"Key": "project-imports/2026/10/30/stray.zip", "LastModified": (now - timedelta(hours=2)).timestamp()},
        {"Key": "project-imports/2026/10/30/young.zip", "LastModified": now.timestamp()},
    ]
    counters = {(k, o): _count(k, o) for k in ("export", "import") for o in ("expired", "interrupted")}

    summary = await runner.run_sweep(now=now, storage=store, session_factory=factory)

    states = {name: await _read(factory, row.id) for name, row in {
        "expired": expired, "fresh": fresh, "upload_done": upload_done, "failed_export": failed_export,
        "stale": stale, "slow": slow, "lost": lost, "lost_export": lost_export, "spent": spent, "recent": recent,
    }.items()}
    assert states["expired"].status == "expired" and states["expired"].storage_key is None
    assert states["fresh"].status == "succeeded" and states["fresh"].storage_key == "fresh.zip"
    assert states["upload_done"].storage_key is None and states["failed_export"].storage_key is None
    assert states["stale"].status == "failed" and states["stale"].error_code == "interrupted"
    assert states["stale"].error == vocab.ERROR_SENTENCES["interrupted"] and states["stale"].storage_key is None
    assert states["slow"].status == "running", "a slow job is not a failure before JOB_STALE_MINUTES"
    assert states["lost"].status == "queued" and states["lost"].attempts == 1
    assert states["lost_export"].attempts == 2
    assert states["spent"].status == "failed" and states["spent"].error_code == "interrupted"
    assert states["recent"].status == "queued" and states["recent"].attempts == 0
    assert set(store.deleted) == {"exp.zip", "upload-done.zip", "failed-export.zip", "stale.zip",
                                  "project-imports/2026/10/30/stray.zip"}
    assert sorted(recorded) == sorted([("app.worker.tasks.import_project_data", str(lost.id)),
                                       ("app.worker.tasks.export_project_data", str(lost_export.id))])
    assert summary["expired"] == 1 and summary["redispatched"] == 2 and summary["strays_removed"] == 1
    assert summary["interrupted_export"] == 2 and summary["interrupted_import"] == 0
    assert _count("export", "expired") == counters[("export", "expired")] + 1
    assert _count("export", "interrupted") == counters[("export", "interrupted")] + 2


async def test_the_janitor_is_idempotent_and_a_storage_fault_leaves_the_row(
    factory: Any, world: Any, recorded: list,
) -> None:
    now = datetime.now(timezone.utc)
    store = FakeStorage()
    store.fail_delete = True
    job = await _job(factory, kind="export", status="succeeded", project_id=world.project.id, storage_key="k.zip",
                     finished_at=now - timedelta(hours=80), expires_at=now - timedelta(hours=8))
    first = await runner.run_sweep(now=now, storage=store, session_factory=factory)
    assert (await _read(factory, job.id)).status == "succeeded" and first["remove_failed"] >= 1
    store.fail_delete = False
    await runner.run_sweep(now=now, storage=store, session_factory=factory)
    again = await runner.run_sweep(now=now, storage=store, session_factory=factory)
    assert (await _read(factory, job.id)).status == "expired"
    assert again["expired"] == 0 and store.deleted == ["k.zip"]


async def test_the_janitor_never_raises_out(factory: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    async def broken(*_: Any, **__: Any) -> Any:
        raise RuntimeError("database gone")

    monkeypatch.setattr(project_export_service, "sweep", broken)
    out = await runner.run_sweep(now=datetime.now(timezone.utc), storage=FakeStorage(), session_factory=factory)
    assert out["redispatched"] == 0


def test_the_three_tasks_take_exactly_the_payloads_the_callers_send() -> None:
    """The route and the janitor send ``args=[str(job_id)]``; the beat sends nothing."""
    job_id = str(uuid.uuid4())
    inspect.signature(tasks.export_project_data.run).bind(job_id)
    inspect.signature(tasks.import_project_data.run).bind(job_id)
    inspect.signature(tasks.sweep_project_data_jobs.run).bind()
    with pytest.raises(TypeError):
        inspect.signature(tasks.export_project_data.run).bind()


def test_the_beat_entry_runs_hourly_at_35_on_default() -> None:
    from app.worker.celery_app import celery_app

    entry = celery_app.conf.beat_schedule["sweep-project-data-jobs"]
    assert entry["task"] == "app.worker.tasks.sweep_project_data_jobs" == tasks.sweep_project_data_jobs.name
    assert entry["schedule"].minute == {35} and len(entry["schedule"].hour) == 24
    assert entry["options"]["queue"] == "default" and entry["options"]["ignore_result"] is True
