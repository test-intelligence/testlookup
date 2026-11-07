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

    async def delete_prefix(self, prefix: str, bucket: str | None = None) -> int:
        if self.fail_delete:
            raise ConnectionRefusedError("storage down")
        self.deleted.append(prefix)
        doomed = [key for key in self.objects if key.startswith(prefix)]
        for key in doomed:
            self.objects.pop(key)
        self.listing = [entry for entry in self.listing if not str(entry["Key"]).startswith(prefix)]
        return len(doomed)


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


class _Recorded(list):
    """``(task name, *args)`` per call; ``options`` keeps each call's ``apply_async`` options (W30)."""

    def __init__(self) -> None:
        super().__init__()
        self.options: list[dict[str, Any]] = []


@pytest.fixture
def recorded(monkeypatch: pytest.MonkeyPatch) -> _Recorded:
    """REAL payloads bound to each task's signature (never a bare mock), the options to Celery's own
    ``Task.apply_async`` signature; ``reindex_search`` too (its kwargs bound to ``reindex_search.run``)."""
    from celery.app.task import Task

    calls = _Recorded()
    for task in (tasks.export_project_data, tasks.import_project_data, tasks.reindex_search):
        def apply_async(args: Any = None, kwargs: Any = None, _task: Any = task, **options: Any) -> Any:
            inspect.signature(_task.run).bind(*(args or ()), **(kwargs or {}))
            inspect.signature(Task.apply_async).bind(_task, args, kwargs, **options)
            calls.append((_task.name, *(args or ()), *(sorted((kwargs or {}).items()))))
            calls.options.append(dict(options))
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


@pytest.mark.parametrize(("options", "flags"), [
    ({"add_members": True, "keep_reviews": True}, {"add_members": True, "keep_reviews": True}),
    ({"keep_reviews": True}, {"keep_reviews": True}),
    ({}, {}),
])
async def test_the_import_options_reach_run_import_and_every_added_member_is_invalidated_after_commit(
    factory: Any, world: Any, monkeypatch: pytest.MonkeyPatch, options: dict, flags: dict,
) -> None:
    """W37 §2.2: the job's Add members / Keep reviews are passed to ``run_import`` (nothing when unticked — the
    W30 call exactly); after the commit the runner invalidates the cache of EVERY member the import added."""
    key = "project-imports/2026/11/07/people.zip"
    job = await _job(factory, kind="import", status="queued", requested_by_id=world.admin.id,
                     client_token=uuid.uuid4(), storage_key=key, options=options)
    added = [uuid.uuid4(), uuid.uuid4()] if options.get("add_members") else []
    seen: dict[str, Any] = {}
    invalidated: list[uuid.UUID] = []

    async def imported(db: Any, *, job: Any, outcome: dict, **kwargs: Any) -> Any:
        seen.update({name: kwargs[name] for name in ("add_members", "keep_reviews") if name in kwargs})
        row = await db.get(ProjectDataJob, job.id)
        row.status = "succeeded"
        outcome["enrolled_service_account_ids"] = []
        if added:
            outcome["added_member_ids"] = list(added)
        return SimpleNamespace(project_id=uuid.uuid4(), inserted={"projects": 1})

    async def invalidate(user_id: uuid.UUID) -> None:
        row = await _read(factory, job.id)             # after the commit: the job row already reads succeeded
        invalidated.append((user_id, row.status))

    monkeypatch.setattr(project_import_service, "run_import", imported)
    monkeypatch.setattr("app.core.deps.invalidate_membership_cache", invalidate)
    assert await runner.run_import_job(job.id, storage=FakeStorage(), session_factory=factory) == "succeeded"
    assert seen == flags
    assert invalidated == [(world.admin.id, "succeeded")] + [(user, "succeeded") for user in added]


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


# ── W30: history export / parted import / reindex / ledger ──────────────────

HISTORY_LIMITS = {"time_limit": vocab.HISTORY_TIME_LIMIT_SECONDS, "soft_time_limit": vocab.HISTORY_TIME_LIMIT_SECONDS - 300}
HISTORY_OPTIONS = {"include_history": True, "history_days": 90, "max_runs": 2000}


async def _cleanup_of(factory: Any, job_id: uuid.UUID) -> Any:
    from sqlalchemy import select

    async with factory.maker() as db:
        return (await db.execute(select(ProjectDataJob.cleanup).where(ProjectDataJob.id == job_id))).scalar_one()


async def test_a_history_export_passes_its_options_writes_phases_and_counts_its_history(
    factory: Any, world: Any, monkeypatch: pytest.MonkeyPatch, activity: list,
) -> None:
    job = await _job(factory, kind="export", status="queued", project_id=world.project.id,
                     requested_by_id=world.admin.id, options=dict(HISTORY_OPTIONS))
    seen: dict[str, Any] = {}
    sentinel_mongo = object()

    async def fake_export(db: Any, **kwargs: Any) -> Any:
        seen.update(kwargs)
        await kwargs["on_progress"]({"done": 56, "total": 56, "phase": "documents", "phase_done": 5000,
                                     "phase_total": 9120})
        seen["progress"] = (await _read(factory, job.id)).progress
        return _outcome().__class__(**{**_outcome().__dict__, "counts": {
            "projects": 1, "test_runs": 3, "test_cases": 40, "documents.raw_allure_json": 12, "objects": 2}})

    monkeypatch.setattr(project_export_service, "run_project_export", fake_export)
    assert await runner.run_export_job(job.id, storage=FakeStorage(), session_factory=factory,
                                       mongo=sentinel_mongo) == "succeeded"
    assert seen["options"].include_history is True and seen["options"].history_days == 90
    assert seen["mongo"] is sentinel_mongo
    assert seen["progress"] == {"done": 56, "total": 56, "phase": "documents", "phase_done": 5000,
                                "phase_total": 9120}
    assert activity[0]["context"] == {"rows": 44, "runs": 3, "documents": 12, "objects": 2}


async def test_a_catalog_export_passes_no_history_arguments(
    factory: Any, world: Any, monkeypatch: pytest.MonkeyPatch, activity: list,
) -> None:
    job = await _job(factory, kind="export", status="queued", project_id=world.project.id,
                     requested_by_id=world.admin.id)
    seen: dict[str, Any] = {}

    async def fake_export(db: Any, **kwargs: Any) -> Any:
        seen.update(kwargs)
        return _outcome()

    monkeypatch.setattr(project_export_service, "run_project_export", fake_export)
    assert await runner.run_export_job(job.id, storage=FakeStorage(), session_factory=factory) == "succeeded"
    assert seen["options"] is None and seen["mongo"] is None and seen["on_progress"] is None
    assert activity[0]["context"] == {"rows": 4}


@pytest.mark.parametrize("stored", [{"include_members": True}, {"include_reviews": True}])
async def test_a_members_or_reviews_export_passes_its_options_but_no_history_arguments(
    factory: Any, world: Any, monkeypatch: pytest.MonkeyPatch, activity: list, stored: dict,
) -> None:
    """W37 F-2 / F-7: the flags reach ``run_project_export`` without history (no Mongo, no phase writer)."""
    job = await _job(factory, kind="export", status="queued", project_id=world.project.id,
                     requested_by_id=world.admin.id, options=dict(stored))
    seen: dict[str, Any] = {}

    async def fake_export(db: Any, **kwargs: Any) -> Any:
        seen.update(kwargs)
        return _outcome()

    monkeypatch.setattr(project_export_service, "run_project_export", fake_export)
    assert await runner.run_export_job(job.id, storage=FakeStorage(), session_factory=factory) == "succeeded"
    options = seen["options"]
    assert options.include_history is False and seen["mongo"] is None and seen["on_progress"] is None
    assert (options.include_members, options.include_reviews) == (
        bool(stored.get("include_members")), bool(stored.get("include_reviews")))


def _parted_job_values(world: Any) -> dict[str, Any]:
    return {"kind": "import", "status": "queued", "requested_by_id": world.admin.id, "client_token": uuid.uuid4(),
            "options": {"parted": True, "parts": 2, "bytes": 40, "sha256": "e" * 64}}


def _history_report(project_id: uuid.UUID) -> Any:
    return SimpleNamespace(project_id=project_id, inserted={"projects": 1, "test_runs": 2},
                           history=SimpleNamespace(search_reindex="queued"))


async def test_a_parted_history_import_assembles_writes_its_ledger_then_reindexes(
    factory: Any, world: Any, monkeypatch: pytest.MonkeyPatch, recorded: _Recorded,
) -> None:
    from app.services import project_import_history

    store = FakeStorage()
    job = await _job(factory, **_parted_job_values(world))
    new_project = uuid.uuid4()
    ledger = {"project_id": str(new_project), "run_ids": [str(uuid.uuid4())], "pipeline_ids": []}
    seen: dict[str, Any] = {}

    async def assemble(storage: Any, job_row: Any, *, spool: Any) -> str:
        spool.write(b"PK\x03\x04assembled")
        seen["assembled_for"] = job_row.id
        return "f" * 64

    async def imported(db: Any, *, job: Any, outcome: dict, **kwargs: Any) -> Any:
        kwargs["spool"].seek(0)
        seen["spool"] = kwargs["spool"].read()
        outcome["ledger"] = ledger
        await kwargs["on_ledger"](ledger)
        seen["ledger_committed"] = await _cleanup_of(factory, job.id)     # visible on ANOTHER session
        await kwargs["on_progress"]("objects", 1, 2)
        seen["progress"] = (await _read(factory, job.id)).progress
        row = await db.get(ProjectDataJob, job.id)
        row.status = "succeeded"
        row.report = {"history": {"search_reindex": "queued"}}
        return _history_report(new_project)

    bumped: list[Any] = []

    async def bump(project_id: Any) -> None:
        bumped.append(project_id)

    monkeypatch.setattr(project_import_history, "assemble_parts", assemble)
    monkeypatch.setattr(project_import_service, "run_import", imported)
    monkeypatch.setattr("app.core.deps.invalidate_membership_cache", lambda _uid: _noop())
    monkeypatch.setattr("app.services.cache_service.bump_analytics_epoch", bump)
    assert await runner.run_import_job(job.id, storage=store, session_factory=factory, mongo=object()) == "succeeded"
    assert seen["assembled_for"] == job.id and seen["spool"] == b"PK\x03\x04assembled"
    assert seen["ledger_committed"] == ledger
    assert seen["progress"]["phase"] == "objects" and seen["progress"]["phase_done"] == 1
    assert await _cleanup_of(factory, job.id) is None
    assert store.deleted == [f"project-imports/parts/{job.id}/"]          # the parts, once the job ended
    assert list(recorded) == [("app.worker.tasks.reindex_search", ("full", True), ("project_id", str(new_project)))]
    assert recorded.options == [{}]
    assert bumped == [new_project]
    assert (await _read(factory, job.id)).report == {"history": {"search_reindex": "queued"}}


async def _noop() -> None:
    return None


async def test_a_reindex_that_cannot_be_enqueued_is_said_in_the_report(
    factory: Any, world: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    job = await _job(factory, kind="import", status="queued", requested_by_id=world.admin.id,
                     client_token=uuid.uuid4(), storage_key="project-imports/one.zip")

    async def imported(db: Any, *, job: Any, outcome: dict, **kwargs: Any) -> Any:
        row = await db.get(ProjectDataJob, job.id)
        row.status = "succeeded"
        row.report = {"history": {"search_reindex": "queued", "runs": 2}}
        return _history_report(uuid.uuid4())

    def broker_down(*_: Any, **__: Any) -> Any:
        raise ConnectionRefusedError("broker down")

    async def bump(project_id: Any) -> None:
        return None

    monkeypatch.setattr(project_import_service, "run_import", imported)
    monkeypatch.setattr(tasks.reindex_search, "apply_async", broker_down)
    monkeypatch.setattr("app.core.deps.invalidate_membership_cache", lambda _uid: _noop())
    monkeypatch.setattr("app.services.cache_service.bump_analytics_epoch", bump)
    assert await runner.run_import_job(job.id, storage=FakeStorage(), session_factory=factory) == "succeeded"
    row = await _read(factory, job.id)
    assert row.status == "succeeded" and row.report == {"history": {"search_reindex": "not_queued", "runs": 2}}


async def test_a_failed_history_import_removes_what_it_wrote_by_the_ledger(
    factory: Any, world: Any, monkeypatch: pytest.MonkeyPatch, recorded: _Recorded,
) -> None:
    from app.services import project_import_history

    store = FakeStorage()
    job = await _job(factory, **_parted_job_values(world))
    ledger = {"project_id": str(uuid.uuid4()), "run_ids": [str(uuid.uuid4())], "pipeline_ids": []}
    cleaned: list[Any] = []

    async def assemble(storage: Any, job_row: Any, *, spool: Any) -> str:
        return "f" * 64

    async def imported(db: Any, *, job: Any, outcome: dict, **kwargs: Any) -> Any:
        outcome["ledger"] = ledger
        await kwargs["on_ledger"](ledger)
        raise project_import_service.ProjectImportFailed("documents_unwritable", error_type="BulkWriteError")

    async def cleanup(mongo: Any, storage: Any, given: Any) -> dict:
        cleaned.append(given)
        return {}

    monkeypatch.setattr(project_import_history, "assemble_parts", assemble)
    monkeypatch.setattr(project_import_history, "cleanup_outside_postgres", cleanup)
    monkeypatch.setattr(project_import_service, "run_import", imported)
    assert await runner.run_import_job(job.id, storage=store, session_factory=factory, mongo=object()) == "failed"
    row = await _read(factory, job.id)
    assert row.status == "failed" and row.error_code == "documents_unwritable"
    assert cleaned == [ledger] and await _cleanup_of(factory, job.id) is None
    assert store.deleted == [f"project-imports/parts/{job.id}/"] and list(recorded) == []   # no reindex


async def test_an_assembled_upload_that_does_not_match_fails_before_the_import(
    factory: Any, world: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services import project_import_history

    store = FakeStorage()
    job = await _job(factory, **_parted_job_values(world))

    async def assemble(storage: Any, job_row: Any, *, spool: Any) -> str:
        raise project_import_service.ProjectImportFailed("upload_digest_mismatch")

    async def must_not_run(*_: Any, **__: Any) -> Any:
        raise AssertionError("the import ran on a mismatched upload")

    monkeypatch.setattr(project_import_history, "assemble_parts", assemble)
    monkeypatch.setattr(project_import_service, "run_import", must_not_run)
    assert await runner.run_import_job(job.id, storage=store, session_factory=factory) == "failed"
    row = await _read(factory, job.id)
    assert row.error_code == "upload_digest_mismatch" and store.deleted == [f"project-imports/parts/{job.id}/"]


# ── W30: the janitor ────────────────────────────────────────────────────────

async def test_history_jobs_are_redispatched_with_their_time_limits(
    factory: Any, world: Any, recorded: _Recorded,
) -> None:
    lost_at = datetime.now(timezone.utc) - timedelta(minutes=vocab.QUEUED_REDISPATCH_MINUTES + 1)
    history = await _job(factory, kind="export", status="queued", project_id=world.project.id, requested_at=lost_at,
                         options=dict(HISTORY_OPTIONS))
    parted = await _job(factory, **{**_parted_job_values(world), "requested_at": lost_at})
    catalog = await _job(factory, kind="import", status="queued", requested_by_id=world.admin.id,
                         client_token=uuid.uuid4(), requested_at=lost_at)
    await runner.run_sweep(storage=FakeStorage(), session_factory=factory)
    sent = dict(zip((call[1] for call in recorded), recorded.options))
    assert sent == {str(history.id): HISTORY_LIMITS, str(parted.id): HISTORY_LIMITS, str(catalog.id): {}}


async def test_a_history_job_is_stale_only_past_its_own_threshold(factory: Any, world: Any) -> None:
    now = datetime.now(timezone.utc)
    slow = await _job(factory, kind="export", status="running", project_id=world.project.id,
                      options=dict(HISTORY_OPTIONS), started_at=now - timedelta(minutes=vocab.JOB_STALE_MINUTES + 30))
    dead = await _job(factory, **{**_parted_job_values(world), "status": "running",
                                  "started_at": now - timedelta(minutes=vocab.HISTORY_JOB_STALE_MINUTES + 1)})
    catalog = await _job(factory, kind="import", status="running", requested_by_id=world.admin.id,
                         client_token=uuid.uuid4(), started_at=now - timedelta(minutes=vocab.JOB_STALE_MINUTES + 1))
    summary = await runner.run_sweep(now=now, storage=FakeStorage(), session_factory=factory, mongo=object())
    assert (await _read(factory, slow.id)).status == "running", "a 3 h history export is not stale at 70 min"
    assert (await _read(factory, dead.id)).error_code == "interrupted"
    assert (await _read(factory, catalog.id)).error_code == "interrupted"
    assert summary["interrupted_import"] == 2 and summary["interrupted_export"] == 0


async def test_the_janitor_fails_an_abandoned_upload_and_removes_parts_no_job_keeps(
    factory: Any, world: Any,
) -> None:
    now = datetime.now(timezone.utc)
    store = FakeStorage()
    abandoned = await _job(factory, **{**_parted_job_values(world), "status": "uploading",
                                       "requested_at": now - timedelta(hours=vocab.UPLOAD_STALE_HOURS + 1),
                                       "upload": {"parts": 2, "bytes": 40, "received": [1]}})
    open_upload = await _job(factory, **{**_parted_job_values(world), "status": "uploading",
                                         "requested_at": now - timedelta(hours=2)})
    finished = await _job(factory, **{**_parted_job_values(world), "status": "succeeded"})
    old = (now - timedelta(hours=2)).timestamp()
    for owner in (abandoned.id, open_upload.id, finished.id, uuid.uuid4()):
        key = f"project-imports/parts/{owner}/part-0001"
        store.objects[key] = b"part"
        store.listing.append({"Key": key, "LastModified": old})
    summary = await runner.run_sweep(now=now, storage=store, session_factory=factory)
    gone = await _read(factory, abandoned.id)
    assert gone.status == "failed" and gone.error_code == "interrupted" and gone.upload is None
    assert (await _read(factory, open_upload.id)).status == "uploading"
    assert set(store.objects) == {f"project-imports/parts/{open_upload.id}/part-0001"}   # only the open upload keeps
    assert summary["uploads_interrupted"] == 1 and summary["parts_removed"] == 2
    assert f"project-imports/parts/{open_upload.id}/" not in store.deleted
    # the phase-1 stray sweep never takes a part (it would have removed the open upload's after 1 h)
    assert summary["strays_removed"] == 0


async def test_a_crashed_history_import_is_cleaned_by_its_ledger_and_interrupted(
    factory: Any, world: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services import project_import_history

    now = datetime.now(timezone.utc)
    ledger = {"project_id": str(uuid.uuid4()), "run_ids": [str(uuid.uuid4())], "pipeline_ids": []}
    crashed = await _job(factory, **{**_parted_job_values(world), "status": "running", "cleanup": ledger,
                                     "started_at": now - timedelta(minutes=vocab.HISTORY_JOB_STALE_MINUTES + 1)})
    committed = await _job(factory, **{**_parted_job_values(world), "status": "succeeded",
                                       "cleanup": {**ledger, "project_id": str(world.project.id)}})
    cleaned: list[Any] = []
    mongo = object()

    async def cleanup(given_mongo: Any, storage: Any, given: Any) -> dict:
        assert given_mongo is mongo
        cleaned.append(given)
        return {}

    monkeypatch.setattr(project_import_history, "cleanup_outside_postgres", cleanup)
    summary = await runner.run_sweep(now=now, storage=FakeStorage(), session_factory=factory, mongo=mongo)
    row = await _read(factory, crashed.id)
    assert row.status == "failed" and row.error_code == "interrupted"
    assert cleaned == [ledger], "a project that EXISTS (the commit happened) must never be cleaned"
    assert await _cleanup_of(factory, crashed.id) is None and await _cleanup_of(factory, committed.id) is None
    assert summary["ledgers_cleaned"] == 1 and summary["ledgers_cleared"] == 1


async def test_a_cleanup_that_fails_keeps_the_ledger_for_the_next_hour(
    factory: Any, world: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services import project_import_history

    ledger = {"project_id": str(uuid.uuid4()), "run_ids": [], "pipeline_ids": []}
    job = await _job(factory, **{**_parted_job_values(world), "status": "failed", "cleanup": ledger})
    attempts: list[int] = []

    async def cleanup(mongo: Any, storage: Any, given: Any) -> dict:
        attempts.append(1)
        if len(attempts) == 1:
            raise ConnectionRefusedError("mongo down")
        return {}

    monkeypatch.setattr(project_import_history, "cleanup_outside_postgres", cleanup)
    first = await runner.run_sweep(storage=FakeStorage(), session_factory=factory, mongo=object())
    assert first["ledger_failed"] == 1 and await _cleanup_of(factory, job.id) == ledger
    second = await runner.run_sweep(storage=FakeStorage(), session_factory=factory, mongo=object())
    assert second["ledgers_cleaned"] == 1 and await _cleanup_of(factory, job.id) is None
    assert (await _read(factory, job.id)).status == "failed"
