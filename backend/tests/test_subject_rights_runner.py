"""W31 STRAT-12 — the subject export worker and the subject-rights janitor (``worker/subject_rights_runner.py``).

On an in-memory SQLite database (users, subject_rights_jobs): the claim makes a redelivery a no-op; a failure is
recorded with its sentence; the success commits the outcome (+72 h). The janitor expires / removes / fails a stale
export ``interrupted``, re-sends a queued export at most 3 times then fails it, re-sends an erasure WITHOUT a cap
and never fails it — through ``apply_async`` recorders that BIND each payload to the task's real signature (a mock
that accepted anything would hide Celery's argument check). ``remove_exports_of`` (G-C's object step) too.
"""
from __future__ import annotations

import ast
import inspect
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy import types as sqltypes
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.sqlite import DATETIME as SQLITE_DATETIME
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.pool import StaticPool

from app.models.postgres import Base, SubjectRightsJob, User, UserRole
from app.services import subject_export_service
from app.worker import subject_rights_runner as runner
from app.worker import tasks

RUNNER_PY = Path(__file__).resolve().parents[1] / "app" / "worker" / "subject_rights_runner.py"


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
        self.fail_delete = False

    async def delete_object(self, key: str, bucket: str | None = None) -> None:
        if self.fail_delete:
            raise ConnectionRefusedError("storage down")
        self.deleted.append(key)
        self.objects.pop(key, None)


@pytest.fixture
async def maker(monkeypatch: pytest.MonkeyPatch) -> Any:
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:", poolclass=StaticPool, connect_args={"check_same_thread": False},
    )
    dialect = engine.sync_engine.dialect
    dialect.colspecs = {**dialect.colspecs, sqltypes.DateTime: _AwareDateTime}
    monkeypatch.setattr(SubjectRightsJob.__table__.c.counts, "server_default", None)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all, tables=[User.__table__, SubjectRightsJob.__table__])
        for kind in ("export", "erasure"):      # the REAL partial uniques (SQLite built them as plain uniques)
            await conn.execute(text(f"DROP INDEX IF EXISTS uq_srj_one_live_{kind}"))
            await conn.execute(text(
                f"CREATE UNIQUE INDEX uq_srj_one_live_{kind} ON subject_rights_jobs (subject_user_id) "
                f"WHERE kind = '{kind}' AND status IN ('queued', 'running')"
            ))
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


def _user(name: str, role: UserRole = UserRole.TESTER, **extra: Any) -> User:
    return User(id=uuid.uuid4(), username=name, email=f"{name}@example.test", full_name=name.title(),
                hashed_password="x", role=role.value, is_active=True, **extra)


@pytest.fixture
async def world(maker: Any) -> SimpleNamespace:
    people = SimpleNamespace(admin=_user("zqxwadmin", UserRole.ADMIN), subject=_user("zqxwsubject"),
                             other=_user("zqxwother"))
    async with maker() as db:
        db.add_all(vars(people).values())
        await db.commit()
    return people


async def _job(maker: Any, subject: User, **values: Any) -> SubjectRightsJob:
    values.setdefault("kind", "export")
    values.setdefault("status", "queued")
    values.setdefault("source", "self" if values["kind"] == "export" else "admin")
    values.setdefault("requested_at", datetime.now(timezone.utc))
    row = SubjectRightsJob(id=uuid.uuid4(), subject_user_id=subject.id, requested_by_id=subject.id, counts={},
                           attempts=values.pop("attempts", 0), **values)
    async with maker() as db:
        db.add(row)
        await db.commit()
    return row


async def _read(maker: Any, job_id: uuid.UUID) -> SubjectRightsJob:
    async with maker() as db:
        row = await db.get(SubjectRightsJob, job_id)
        assert row is not None
        return row


@pytest.fixture
def recorded(monkeypatch: pytest.MonkeyPatch) -> list[tuple]:
    """REAL payloads bound to each task's signature; the options to Celery's own ``Task.apply_async``."""
    from celery.app.task import Task

    calls: list[tuple] = []
    for task in (tasks.export_subject_data, tasks.erase_subject_data):
        def apply_async(args: Any = None, kwargs: Any = None, _task: Any = task, **options: Any) -> Any:
            inspect.signature(_task.run).bind(*(args or ()), **(kwargs or {}))
            inspect.signature(Task.apply_async).bind(_task, args, kwargs, **options)
            calls.append((_task.name, *(args or ())))
            return SimpleNamespace(id="celery-id")
        monkeypatch.setattr(task, "apply_async", apply_async)
    return calls


def _outcome(**extra: Any) -> subject_export_service.SubjectExportOutcome:
    return subject_export_service.SubjectExportOutcome(
        key="subject-exports/2026/11/04/j.zip", bytes=4096, file_count=9, manifest_sha256="m" * 64,
        archive_sha256="a" * 64, signature="signed", signing_key_id="k-1", counts={"profile": 1, "comments": 3},
        **extra,
    )


# ── the export job ──────────────────────────────────────────────────────────

async def test_a_claimed_export_succeeds_with_the_outcome_and_72_hours(
    maker: Any, world: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    job = await _job(maker, world.subject, source="admin")
    seen: dict[str, Any] = {}

    async def fake_export(db: Any, **kwargs: Any) -> Any:
        seen.update(kwargs)
        seen["subject_id"] = kwargs["subject"].id
        seen["status_during_build"] = (await _read(maker, job.id)).status
        await kwargs["on_written"]("subject-exports/2026/11/04/j.zip")
        seen["key_after_write"] = (await _read(maker, job.id)).storage_key
        return _outcome()

    monkeypatch.setattr(subject_export_service, "run_subject_export", fake_export)
    assert await runner.run_export_job(str(job.id), storage=FakeStorage(), session_factory=maker) == "succeeded"
    row = await _read(maker, job.id)
    assert row.status == "succeeded" and row.attempts == 1 and row.storage_key == "subject-exports/2026/11/04/j.zip"
    assert row.counts == {"profile": 1, "comments": 3} and row.bytes == 4096 and row.signature == "signed"
    assert row.expires_at - row.finished_at == timedelta(hours=72)
    assert seen["status_during_build"] == "running" and seen["key_after_write"] == row.storage_key
    assert seen["subject_id"] == world.subject.id and seen["source"] == "admin"
    assert seen["requested_at"] == job.requested_at


async def test_a_redelivery_or_the_other_kind_is_skipped(
    maker: Any, world: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def never(db: Any, **kwargs: Any) -> Any:  # pragma: no cover - a skipped job builds nothing
        raise AssertionError("built a skipped job")

    monkeypatch.setattr(subject_export_service, "run_subject_export", never)
    running = await _job(maker, world.subject, status="running")
    erasure = await _job(maker, world.other, kind="erasure", phase="lockout")
    for job_id in (running.id, erasure.id, uuid.uuid4()):
        assert await runner.run_export_job(job_id, storage=FakeStorage(), session_factory=maker) == "skipped"
    assert (await _read(maker, running.id)).attempts == 0
    assert (await _read(maker, erasure.id)).status == "queued"


async def test_a_failed_export_records_its_sentence(
    maker: Any, world: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def refused(db: Any, **kwargs: Any) -> Any:
        raise subject_export_service.SubjectExportFailed("export_write", error_type="ConnectionRefusedError")

    monkeypatch.setattr(subject_export_service, "run_subject_export", refused)
    job = await _job(maker, world.subject)
    assert await runner.run_export_job(job.id, storage=FakeStorage(), session_factory=maker) == "failed"
    row = await _read(maker, job.id)
    assert row.status == "failed" and row.error_code == "export_write" and row.finished_at is not None
    assert row.error == "The export could not be written to storage (ConnectionRefusedError)."


async def test_an_unexpected_error_is_export_build_with_the_type_name_only(
    maker: Any, world: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def broken(db: Any, **kwargs: Any) -> Any:
        raise KeyError("zqxw.subj@example.org")       # the message must never reach the row

    monkeypatch.setattr(subject_export_service, "run_subject_export", broken)
    job = await _job(maker, world.subject)
    assert await runner.run_export_job(job.id, storage=FakeStorage(), session_factory=maker) == "failed"
    row = await _read(maker, job.id)
    assert row.error_code == "export_build" and row.error == "The export could not be built (KeyError)."


async def test_an_erased_subject_is_never_exported(
    maker: Any, world: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def never(db: Any, **kwargs: Any) -> Any:  # pragma: no cover
        raise AssertionError("exported an erased person")

    monkeypatch.setattr(subject_export_service, "run_subject_export", never)
    async with maker() as db:
        subject = await db.get(User, world.subject.id)
        subject.erased_at = datetime.now(timezone.utc)
        await db.commit()
    job = await _job(maker, world.subject)
    assert await runner.run_export_job(job.id, storage=FakeStorage(), session_factory=maker) == "failed"
    assert (await _read(maker, job.id)).error == "The export could not be built (UserErased)."


# ── the janitor ─────────────────────────────────────────────────────────────

async def test_the_sweep_expires_removes_and_fails_a_stale_export(
    maker: Any, world: SimpleNamespace, recorded: list,
) -> None:
    now = datetime.now(timezone.utc)
    store = FakeStorage()
    store.objects.update({"subject-exports/a.zip": b"a", "subject-exports/b.zip": b"b", "subject-exports/c.zip": b"c"})
    due = await _job(maker, world.subject, status="succeeded", storage_key="subject-exports/a.zip",
                     finished_at=now - timedelta(hours=73), expires_at=now - timedelta(hours=1))
    fresh = await _job(maker, world.other, status="succeeded", storage_key="subject-exports/keep.zip",
                       finished_at=now, expires_at=now + timedelta(hours=71))
    failed = await _job(maker, world.admin, status="failed", storage_key="subject-exports/b.zip")
    stale = await _job(maker, world.other, kind="export", status="running", storage_key="subject-exports/c.zip",
                       started_at=now - timedelta(minutes=41), requested_at=now - timedelta(minutes=42))
    summary = await runner.run_sweep(now=now, storage=store, session_factory=maker)
    assert (summary["expired"], summary["objects_removed"], summary["interrupted"]) == (1, 1, 1)
    row = await _read(maker, due.id)
    assert row.status == "expired" and row.storage_key is None
    assert (await _read(maker, fresh.id)).status == "succeeded"
    assert (await _read(maker, failed.id)).storage_key is None
    row = await _read(maker, stale.id)
    assert row.status == "failed" and row.error_code == "interrupted" and row.storage_key is None
    assert row.error == "The job was interrupted; nothing was kept. Start it again."
    assert store.objects == {} and recorded == []
    again = await runner.run_sweep(now=now, storage=store, session_factory=maker)       # idempotent
    assert (again["expired"], again["objects_removed"], again["interrupted"]) == (0, 0, 0)


async def test_a_storage_fault_leaves_the_row_for_the_next_hour(maker: Any, world: SimpleNamespace) -> None:
    now = datetime.now(timezone.utc)
    store = FakeStorage()
    store.fail_delete = True
    due = await _job(maker, world.subject, status="succeeded", storage_key="subject-exports/a.zip",
                     finished_at=now - timedelta(hours=80), expires_at=now - timedelta(hours=8))
    summary = await runner.run_sweep(now=now, storage=store, session_factory=maker)
    assert summary["remove_failed"] == 1
    row = await _read(maker, due.id)
    assert row.status == "succeeded" and row.storage_key == "subject-exports/a.zip"


async def test_a_queued_export_is_resent_at_most_three_times_then_interrupted(
    maker: Any, world: SimpleNamespace, recorded: list,
) -> None:
    now = datetime.now(timezone.utc)
    lost = await _job(maker, world.subject, requested_at=now - timedelta(minutes=11))
    spent = await _job(maker, world.other, requested_at=now - timedelta(minutes=50), attempts=3)
    young = await _job(maker, world.admin, requested_at=now - timedelta(minutes=5))
    summary = await runner.run_sweep(now=now, storage=FakeStorage(), session_factory=maker)
    assert summary["redispatched_export"] == 1 and summary["interrupted_queued"] == 1
    assert recorded == [("app.worker.tasks.export_subject_data", str(lost.id))]
    assert (await _read(maker, lost.id)).attempts == 1
    row = await _read(maker, spent.id)
    assert row.status == "failed" and row.error_code == "interrupted"
    assert (await _read(maker, young.id)).attempts == 0
    for _ in range(2):
        await runner.run_sweep(now=now, storage=FakeStorage(), session_factory=maker)
    assert (await _read(maker, lost.id)).attempts == 3 and (await _read(maker, lost.id)).status == "queued"
    await runner.run_sweep(now=now, storage=FakeStorage(), session_factory=maker)
    assert (await _read(maker, lost.id)).status == "failed"
    assert recorded.count(("app.worker.tasks.export_subject_data", str(lost.id))) == 3


async def test_an_erasure_is_resent_without_a_cap_and_never_failed(
    maker: Any, world: SimpleNamespace, recorded: list,
) -> None:
    now = datetime.now(timezone.utc)
    queued = await _job(maker, world.subject, kind="erasure", phase="lockout", attempts=9,
                        requested_at=now - timedelta(hours=30))
    dead = await _job(maker, world.other, kind="erasure", status="running", phase="postgres", attempts=4,
                      started_at=now - timedelta(hours=2), requested_at=now - timedelta(hours=2))
    stopped = await _job(maker, world.admin, kind="erasure", status="running", phase="postgres",
                         error_code="erasure_incomplete", started_at=now - timedelta(minutes=5),
                         requested_at=now - timedelta(minutes=6))
    for _ in range(5):
        summary = await runner.run_sweep(now=now, storage=FakeStorage(), session_factory=maker)
        assert summary["redispatched_erasure"] >= 1
    sent = [call for call in recorded if call[0] == "app.worker.tasks.erase_subject_data"]
    assert sent.count(("app.worker.tasks.erase_subject_data", str(queued.id))) == 5
    assert ("app.worker.tasks.erase_subject_data", str(dead.id)) in sent
    assert ("app.worker.tasks.erase_subject_data", str(stopped.id)) in sent
    for job in (queued, dead, stopped):
        row = await _read(maker, job.id)
        assert row.status == "queued" and row.error_code in (None, "erasure_incomplete")   # never "failed"
        assert row.phase == job.phase                                                      # resumes where it was


async def test_a_running_erasure_inside_its_window_is_left_alone(
    maker: Any, world: SimpleNamespace, recorded: list,
) -> None:
    now = datetime.now(timezone.utc)
    working = await _job(maker, world.subject, kind="erasure", status="running", phase="postgres",
                         started_at=now - timedelta(minutes=10), requested_at=now - timedelta(minutes=11))
    await runner.run_sweep(now=now, storage=FakeStorage(), session_factory=maker)
    assert recorded == [] and (await _read(maker, working.id)).status == "running"


async def test_a_broken_sweep_step_still_redispatches(
    maker: Any, world: SimpleNamespace, recorded: list, monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def broken(*_: Any, **__: Any) -> Any:
        raise ConnectionError("db hiccup")

    monkeypatch.setattr(subject_export_service, "sweep", broken)
    now = datetime.now(timezone.utc)
    lost = await _job(maker, world.subject, requested_at=now - timedelta(minutes=30))
    await runner.run_sweep(now=now, storage=FakeStorage(), session_factory=maker)
    assert recorded == [("app.worker.tasks.export_subject_data", str(lost.id))]


async def test_a_broker_refusal_leaves_the_job_queued(maker: Any, world: SimpleNamespace) -> None:
    class Refusing:
        def apply_async(self, *args: Any, **kwargs: Any) -> Any:
            raise ConnectionRefusedError("broker down")

    now = datetime.now(timezone.utc)
    lost = await _job(maker, world.subject, requested_at=now - timedelta(minutes=30))
    summary = await runner.run_sweep(now=now, storage=FakeStorage(), session_factory=maker,
                                     task_for=lambda kind: Refusing())
    assert summary["redispatch_failed"] == 1 and (await _read(maker, lost.id)).status == "queued"


# ── the erasure's object step (G-C calls it) ────────────────────────────────

async def test_remove_exports_of_removes_every_stored_export_of_the_subject(
    maker: Any, world: SimpleNamespace,
) -> None:
    now = datetime.now(timezone.utc)
    store = FakeStorage()
    store.objects.update({"subject-exports/s1.zip": b"1", "subject-exports/s2.zip": b"2",
                          "subject-exports/o.zip": b"o"})
    ready = await _job(maker, world.subject, status="succeeded", storage_key="subject-exports/s1.zip",
                       finished_at=now, expires_at=now + timedelta(hours=72))
    failed = await _job(maker, world.subject, status="failed", storage_key="subject-exports/s2.zip")
    other = await _job(maker, world.other, status="succeeded", storage_key="subject-exports/o.zip",
                       finished_at=now, expires_at=now + timedelta(hours=72))
    async with maker() as db:
        assert await subject_export_service.remove_exports_of(db, store, subject_id=world.subject.id) == 2
        await db.commit()
    row = await _read(maker, ready.id)
    assert row.status == "expired" and row.storage_key is None
    row = await _read(maker, failed.id)
    assert row.status == "failed" and row.storage_key is None
    assert (await _read(maker, other.id)).storage_key == "subject-exports/o.zip"
    assert store.objects == {"subject-exports/o.zip": b"o"}
    async with maker() as db:                                  # idempotent: a re-run finds nothing
        assert await subject_export_service.remove_exports_of(db, store, subject_id=world.subject.id) == 0


async def test_remove_exports_of_raises_while_storage_refuses(maker: Any, world: SimpleNamespace) -> None:
    now = datetime.now(timezone.utc)
    store = FakeStorage()
    store.fail_delete = True
    ready = await _job(maker, world.subject, status="succeeded", storage_key="subject-exports/s1.zip",
                       finished_at=now, expires_at=now + timedelta(hours=72))
    async with maker() as db:
        with pytest.raises(subject_export_service.ExportObjectsRemaining):
            await subject_export_service.remove_exports_of(db, store, subject_id=world.subject.id)
    row = await _read(maker, ready.id)
    assert row.status == "succeeded" and row.storage_key == "subject-exports/s1.zip"


# ── the wiring ──────────────────────────────────────────────────────────────

def test_the_tasks_take_their_real_payloads_and_route_to_default() -> None:
    job_id = str(uuid.uuid4())
    inspect.signature(tasks.export_subject_data.run).bind(job_id)
    inspect.signature(tasks.erase_subject_data.run).bind(job_id)
    inspect.signature(tasks.sweep_subject_rights_jobs.run).bind()
    with pytest.raises(TypeError):
        inspect.signature(tasks.export_subject_data.run).bind()
    for task in (tasks.export_subject_data, tasks.erase_subject_data, tasks.sweep_subject_rights_jobs):
        assert task.queue == "default" and task.max_retries == 0


def test_the_beat_entry_runs_hourly_at_50_on_default() -> None:
    from app.worker.celery_app import celery_app

    entry = celery_app.conf.beat_schedule["sweep-subject-rights-jobs"]
    assert entry["task"] == "app.worker.tasks.sweep_subject_rights_jobs" == tasks.sweep_subject_rights_jobs.name
    assert entry["schedule"].minute == {50} and len(entry["schedule"].hour) == 24
    assert entry["options"]["queue"] == "default" and entry["options"]["ignore_result"] is True


def test_the_runner_logs_no_person() -> None:
    banned = {"username", "email", "full_name", "key", "storage_key", "name"}
    for node in ast.walk(ast.parse(RUNNER_PY.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and \
                isinstance(node.func.value, ast.Name) and node.func.value.id == "logger":
            assert not node.args[1:] and not {kw.arg for kw in node.keywords} & banned, node.lineno
