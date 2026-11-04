"""W31 STRAT-12: the subject-rights job rows (``services/subject_rights_job_service.py``).

SQLite in memory with the two REAL partial uniques re-created as SQLite partial
indexes (the ``test_project_data_routes.py`` pattern): create → ExportRunning /
ErasureRunning on the partial unique (also when the pre-check is raced);
``claim`` is a no-op for the other kind and for a non-queued job;
``load_job_for`` answers the SAME 404 for an unknown id, another person's job,
a QA lead and every API-key principal; ``set_phase("done")`` → ``succeeded``;
``to_read`` never carries the storage key; ``displays`` maps an erased user to
"Erased user"; ``require_signed_in_session`` refuses every API key with 403
``session_required``; the module never commits.
"""
from __future__ import annotations

import ast
import json
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException
from sqlalchemy import event, select, text
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
)
from app.models.postgres import Base, SubjectRightsJob, User, UserRole
from app.services import subject_rights_job_service as svc
from app.services import subject_rights_vocab as vocab

SERVICE_PY = Path(__file__).resolve().parents[1] / "app" / "services" / "subject_rights_job_service.py"
NOT_FOUND = {"code": "job_not_found", "message": "Subject data job not found."}


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


_TABLES = [User.__table__, SubjectRightsJob.__table__]
_PARTIAL = (
    "CREATE UNIQUE INDEX uq_srj_one_live_export ON subject_rights_jobs (subject_user_id) "
    "WHERE kind = 'export' AND status IN ('queued', 'running')",
    "CREATE UNIQUE INDEX uq_srj_one_live_erasure ON subject_rights_jobs (subject_user_id) "
    "WHERE kind = 'erasure' AND status IN ('queued', 'running')",
)


@pytest.fixture
async def maker(monkeypatch: pytest.MonkeyPatch) -> Any:
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

    # ``'{}'::jsonb`` is Postgres syntax: the rows always carry ``counts`` explicitly.
    monkeypatch.setattr(SubjectRightsJob.__table__.c.counts, "server_default", None)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all, tables=_TABLES)
        for name in ("uq_srj_one_live_export", "uq_srj_one_live_erasure"):
            await conn.execute(text(f"DROP INDEX IF EXISTS {name}"))
        for ddl in _PARTIAL:
            await conn.execute(text(ddl))
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


def _user(role: UserRole, name: str, **extra: Any) -> User:
    return User(id=uuid.uuid4(), username=name, email=f"{name}@example.test", full_name=name.title(),
                hashed_password="x", role=role.value, is_active=True, **extra)


@pytest.fixture
async def world(maker: Any) -> SimpleNamespace:
    people = SimpleNamespace(
        admin=_user(UserRole.ADMIN, "zqxwadmin"),
        other_admin=_user(UserRole.ADMIN, "zqxwsecond"),
        lead=_user(UserRole.QA_LEAD, "zqxwlead"),
        subject=_user(UserRole.TESTER, "zqxwsubject"),
        stranger=_user(UserRole.TESTER, "zqxwstranger"),
    )
    async with maker() as db:
        db.add_all(vars(people).values())
        await db.commit()
    return people


async def _job(maker: Any, **values: Any) -> SubjectRightsJob:
    row = SubjectRightsJob(counts={}, attempts=0, requested_at=datetime.now(timezone.utc), **values)
    async with maker() as db:
        db.add(row)
        await db.commit()
    return row


# ── create: one live job per subject and kind ────────────────────────────────

async def test_a_second_live_export_is_refused_with_the_live_jobs_id(maker, world):
    async with maker() as db:
        first = await svc.create_export_job(db, subject=world.subject, actor=world.subject, source="self")
        await db.commit()
    assert (first.kind, first.status, first.source, first.phase) == ("export", "queued", "self", None)
    async with maker() as db:
        with pytest.raises(svc.ExportRunning) as refused:
            await svc.create_export_job(db, subject=world.subject, actor=world.admin, source="admin")
    assert refused.value.job_id == first.id
    # another person, and the other kind, are unaffected
    async with maker() as db:
        await svc.create_export_job(db, subject=world.stranger, actor=world.admin, source="admin")
        erasure = await svc.create_erasure_job(db, subject=world.subject, actor=world.admin)
        await db.commit()
    assert (erasure.kind, erasure.status, erasure.source, erasure.phase) == ("erasure", "queued", "admin", None)


@pytest.mark.parametrize("kind", ["export", "erasure"])
async def test_the_partial_unique_refuses_a_raced_request(maker, world, monkeypatch, kind):
    """The pre-check lost the race: the partial unique index itself refuses the second live job."""
    await _job(maker, kind=kind, status="running", source="admin", subject_user_id=world.subject.id,
               requested_by_id=world.admin.id)
    real_live_id = svc._live_id
    calls: list[int] = []

    async def stale_precheck(db, subject_id, job_kind):
        calls.append(1)
        return None if len(calls) == 1 else await real_live_id(db, subject_id, job_kind)

    monkeypatch.setattr(svc, "_live_id", stale_precheck)
    refusal = svc.ExportRunning if kind == "export" else svc.ErasureRunning
    async with maker() as db:
        with pytest.raises(refusal) as refused:
            if kind == "export":
                await svc.create_export_job(db, subject=world.subject, actor=world.admin, source="admin")
            else:
                await svc.create_erasure_job(db, subject=world.subject, actor=world.admin)
        assert refused.value.job_id is not None
        count = (await db.execute(select(SubjectRightsJob).where(SubjectRightsJob.kind == kind))).scalars().all()
    assert len(count) == 1


async def test_a_finished_export_frees_the_slot(maker, world):
    for status in ("succeeded", "failed", "expired"):
        await _job(maker, kind="export", status=status, source="self", subject_user_id=world.subject.id,
                   requested_by_id=world.subject.id)
    async with maker() as db:
        job = await svc.create_export_job(db, subject=world.subject, actor=world.subject, source="self")
        await db.commit()
        assert (await svc.live_job(db, subject_id=world.subject.id, kind="export")).id == job.id
        assert await svc.live_job(db, subject_id=world.subject.id, kind="erasure") is None


async def test_an_unknown_source_is_a_programming_error(maker, world):
    async with maker() as db:
        with pytest.raises(ValueError):
            await svc.create_export_job(db, subject=world.subject, actor=world.subject, source="api")


# ── the worker's transitions ─────────────────────────────────────────────────

async def test_claim_is_a_no_op_for_the_other_kind_and_a_non_queued_job(maker, world):
    export = await _job(maker, kind="export", status="queued", source="self", subject_user_id=world.subject.id,
                        requested_by_id=world.subject.id)
    running = await _job(maker, kind="erasure", status="running", source="admin", phase="lockout",
                         subject_user_id=world.stranger.id, requested_by_id=world.admin.id)
    async with maker() as db:
        assert await svc.claim(db, export.id, kind="erasure") is None
        assert await svc.claim(db, running.id, kind="erasure") is None
        assert await svc.claim(db, uuid.uuid4(), kind="export") is None
        claimed = await svc.claim(db, export.id, kind="export")
        await db.commit()
    assert claimed is not None and (claimed.status, claimed.attempts) == ("running", 1)
    first_start = claimed.started_at
    assert first_start is not None
    async with maker() as db:
        assert await svc.claim(db, export.id, kind="export") is None   # a redelivered message
        # an erasure re-queued (resume / janitor) is claimed again; started_at stays the FIRST claim's
        assert await svc.requeue(db, running.id, kind="erasure") is True
        again = await svc.claim(db, running.id, kind="erasure")
        await db.commit()
    assert again is not None and again.attempts == 1 and again.status == "running"


async def test_requeue_touches_only_a_live_job_of_its_kind(maker, world):
    done = await _job(maker, kind="erasure", status="succeeded", source="admin", phase="done",
                      subject_user_id=world.subject.id, requested_by_id=world.admin.id)
    live = await _job(maker, kind="export", status="running", source="self", subject_user_id=world.subject.id,
                      requested_by_id=world.subject.id)
    async with maker() as db:
        assert await svc.requeue(db, done.id, kind="erasure") is False
        assert await svc.requeue(db, live.id, kind="erasure") is False
        assert await svc.requeue(db, live.id, kind="export") is True
        await db.commit()


async def test_set_phase_merges_counts_clears_the_error_and_done_succeeds(maker, world):
    job = await _job(maker, kind="erasure", status="running", source="admin", phase="lockout",
                     subject_user_id=world.subject.id, requested_by_id=world.admin.id,
                     error_code="erasure_incomplete", error="stale")
    now = datetime.now(timezone.utc)
    async with maker() as db:
        await svc.set_phase(db, job.id, phase="postgres", counts={"api_keys.user_id": 2, "users.*": 1}, now=now)
        await db.commit()
    async with maker() as db:
        row = await db.get(SubjectRightsJob, job.id)
        assert (row.status, row.phase, row.error_code, row.error) == ("running", "postgres", None, None)
        await svc.set_phase(db, job.id, phase="objects", counts={"objects:subject-exports/": 1}, now=now)
        await svc.set_phase(db, job.id, phase="done", counts={}, now=now)
        await db.commit()
    async with maker() as db:
        row = await db.get(SubjectRightsJob, job.id)
    assert (row.status, row.phase) == ("succeeded", "done")
    assert row.finished_at is not None
    assert row.counts == {"api_keys.user_id": 2, "users.*": 1, "objects:subject-exports/": 1}


async def test_set_phase_refuses_an_unknown_phase_and_an_export(maker, world):
    export = await _job(maker, kind="export", status="running", source="self", subject_user_id=world.subject.id,
                        requested_by_id=world.subject.id)
    async with maker() as db:
        with pytest.raises(ValueError):
            await svc.set_phase(db, export.id, phase="finished", counts={}, now=datetime.now(timezone.utc))
        with pytest.raises(LookupError):
            await svc.set_phase(db, export.id, phase="done", counts={}, now=datetime.now(timezone.utc))


async def test_finish_export_and_mark_failed(maker, world):
    job = await _job(maker, kind="export", status="running", source="self", subject_user_id=world.subject.id,
                     requested_by_id=world.subject.id)
    now = datetime.now(timezone.utc)
    async with maker() as db:
        await svc.finish_export(db, job.id, now=now, outcome={
            "key": "subject-exports/2026/11/04/k.zip", "bytes": 10, "file_count": 3, "manifest_sha256": "m" * 64,
            "archive_sha256": "a" * 64, "signature": "signed", "signing_key_id": "kid", "counts": {"comments": 4},
        })
        await db.commit()
    async with maker() as db:
        row = await db.get(SubjectRightsJob, job.id)
    assert (row.status, row.bytes, row.counts, row.error_code) == ("succeeded", 10, {"comments": 4}, None)
    assert row.expires_at - row.finished_at == timedelta(hours=vocab.EXPORT_TTL_HOURS)
    failed = await _job(maker, kind="export", status="running", source="self", subject_user_id=world.stranger.id,
                        requested_by_id=world.stranger.id)
    async with maker() as db:
        await svc.mark_failed(db, failed.id, code="export_build", now=now, error_type="OSError")
        await svc.mark_erasure_incomplete(db, failed.id, phase="postgres")   # an export: untouched
        await db.commit()
    async with maker() as db:
        row = await db.get(SubjectRightsJob, failed.id)
    assert (row.status, row.error_code, row.error) == (
        "failed", "export_build", "The export could not be built (OSError).",
    )


async def test_an_incomplete_erasure_stays_running_and_names_its_phase(maker, world):
    job = await _job(maker, kind="erasure", status="running", source="admin", phase="postgres",
                     subject_user_id=world.subject.id, requested_by_id=world.admin.id)
    async with maker() as db:
        await svc.mark_erasure_incomplete(db, job.id, phase="postgres")
        await db.commit()
    async with maker() as db:
        row = await db.get(SubjectRightsJob, job.id)
    assert (row.status, row.error_code) == ("running", "erasure_incomplete")
    assert row.error == vocab.ERROR_SENTENCES["erasure_incomplete"].format(phase="postgres")


# ── reads: the requester or an ADMIN; everything else the ONE 404 ────────────

def _as_key(user: User, how: str) -> User:
    """A fresh copy of ``user`` authenticated by an API key, the way each auth path marks it."""
    principal = _user(UserRole(user.role), user.username)
    principal.id = user.id
    if how == "scoped":
        _bind_credential_kind(principal, CREDENTIAL_KIND_API_KEY)
        _bind_api_key_grant(principal, ApiKeyGrant(key_id=uuid.uuid4(), scopes=("project:admin",), expires_at=None))
    elif how == "legacy":
        _bind_credential_kind(principal, CREDENTIAL_KIND_API_KEY)
        _bind_api_key_grant(principal, ApiKeyGrant(key_id=uuid.uuid4(), scopes=(), expires_at=None))
    elif how == "bound":
        _bind_credential_kind(_bind_api_key_project(principal, uuid.uuid4()), CREDENTIAL_KIND_API_KEY)
        _bind_api_key_grant(principal, ApiKeyGrant(key_id=uuid.uuid4(), scopes=(), expires_at=None))
    else:   # the streaming path records the kind only
        _bind_credential_kind(principal, CREDENTIAL_KIND_API_KEY)
    return principal


async def _refusal(maker: Any, job_id: uuid.UUID, user: User) -> Any:
    async with maker() as db:
        with pytest.raises(HTTPException) as refused:
            await svc.load_job_for(db, job_id, user)
    return refused.value.status_code, refused.value.detail


async def test_load_job_for_the_requester_and_any_admin(maker, world):
    mine = await _job(maker, kind="export", status="succeeded", source="self", subject_user_id=world.subject.id,
                      requested_by_id=world.subject.id)
    by_admin = await _job(maker, kind="export", status="queued", source="admin", subject_user_id=world.subject.id,
                          requested_by_id=world.admin.id)
    async with maker() as db:
        assert (await svc.load_job_for(db, mine.id, world.subject)).id == mine.id
        assert (await svc.load_job_for(db, mine.id, world.other_admin)).id == mine.id
        assert (await svc.load_job_for(db, by_admin.id, world.admin)).id == by_admin.id
        assert (await svc.load_job_for(db, by_admin.id, world.other_admin)).id == by_admin.id
    # the subject did not request the admin's export of them: refused like anyone else
    assert await _refusal(maker, by_admin.id, world.subject) == (404, NOT_FOUND)


async def test_every_other_reader_gets_the_same_404_body(maker, world):
    job = await _job(maker, kind="export", status="succeeded", source="self", subject_user_id=world.subject.id,
                     requested_by_id=world.subject.id)
    erasure = await _job(maker, kind="erasure", status="running", source="admin", phase="lockout",
                         subject_user_id=world.stranger.id, requested_by_id=world.admin.id)
    refusals = {
        "unknown id": await _refusal(maker, uuid.uuid4(), world.admin),
        "another user": await _refusal(maker, job.id, world.stranger),
        "a QA lead": await _refusal(maker, job.id, world.lead),
        "a QA lead, erasure": await _refusal(maker, erasure.id, world.lead),
    }
    for how in ("scoped", "legacy", "bound", "streaming"):
        refusals[f"admin {how} key"] = await _refusal(maker, job.id, _as_key(world.admin, how))
        refusals[f"requester {how} key"] = await _refusal(maker, job.id, _as_key(world.subject, how))
    assert len(refusals) == 12
    assert {json.dumps(r, sort_keys=True) for r in refusals.values()} == {json.dumps((404, NOT_FOUND), sort_keys=True)}, (
        refusals
    )


async def test_require_signed_in_session_refuses_every_api_key(world):
    guard = svc.require_signed_in_session()
    assert await guard(current_user=world.subject) is world.subject
    assert await guard(current_user=world.admin) is world.admin
    for how in ("scoped", "legacy", "bound", "streaming"):
        with pytest.raises(HTTPException) as refused:
            await guard(current_user=_as_key(world.admin, how))
        assert refused.value.status_code == 403
        assert refused.value.detail == {
            "code": "session_required", "message": vocab.ERROR_SENTENCES["session_required"],
        }


async def test_the_lists_are_scoped_and_count_beyond_the_page(maker, world):
    for _ in range(vocab.JOBS_LIST_LIMIT + 2):
        await _job(maker, kind="export", status="expired", source="self", subject_user_id=world.subject.id,
                   requested_by_id=world.subject.id)
    await _job(maker, kind="export", status="succeeded", source="admin", subject_user_id=world.subject.id,
               requested_by_id=world.admin.id)
    await _job(maker, kind="export", status="succeeded", source="admin", subject_user_id=world.stranger.id,
               requested_by_id=world.subject.id)   # cannot happen through the routes; the filter still holds
    await _job(maker, kind="erasure", status="running", source="admin", subject_user_id=world.subject.id,
               requested_by_id=world.admin.id)
    async with maker() as db:
        mine, mine_total = await svc.list_my_exports(db, world.subject)
        every, every_total = await svc.list_for_subject(db, world.subject.id)
        erasures, erasure_total = await svc.list_for_subject(db, world.subject.id, kind="erasure")
        with pytest.raises(ValueError):
            await svc.list_for_subject(db, world.subject.id, kind="import")
    assert (len(mine), mine_total) == (vocab.JOBS_LIST_LIMIT, vocab.JOBS_LIST_LIMIT + 2)
    assert all(j.requested_by_id == j.subject_user_id == world.subject.id for j in mine)
    assert (len(every), every_total) == (vocab.JOBS_LIST_LIMIT, vocab.JOBS_LIST_LIMIT + 4)
    assert (len(erasures), erasure_total) == (1, 1)


async def test_displays_never_an_email_and_an_erased_user_reads_erased(maker, world):
    erased = _user(UserRole.TESTER, "zqxwgone", erased_at=datetime.now(timezone.utc))
    no_name = _user(UserRole.TESTER, "zqxwnoname")
    no_name.full_name = None
    async with maker() as db:
        db.add_all([erased, no_name])
        await db.commit()
    jobs = [
        await _job(maker, kind="export", status="queued", source="admin", subject_user_id=erased.id,
                   requested_by_id=world.admin.id),
        await _job(maker, kind="export", status="queued", source="self", subject_user_id=no_name.id,
                   requested_by_id=None),
    ]
    async with maker() as db:
        names = await svc.displays(db, jobs)
        assert await svc.displays(db, []) == {}
    assert names == {erased.id: vocab.ERASED_DISPLAY, world.admin.id: "Zqxwadmin", no_name.id: "zqxwnoname"}
    assert not [n for n in names.values() if "@" in n]


async def test_to_read_never_carries_the_storage_key(maker, world):
    key = "subject-exports/2026/11/04/zqxwkey.zip"
    now = datetime.now(timezone.utc)
    job = await _job(maker, kind="export", status="succeeded", source="self", subject_user_id=world.subject.id,
                     requested_by_id=world.subject.id, storage_key=key, archive_sha256="zqxwarchive",
                     signing_key_id="zqxwsigner", finished_at=now, expires_at=now + timedelta(hours=1), bytes=5)
    read = svc.to_read(job, subject_display="Zqxwsubject", requested_by_display="Zqxwsubject", now=now)
    dumped = read.model_dump_json()
    assert "storage_key" not in read.model_dump() and "zqxwkey" not in dumped
    assert "zqxwarchive" not in dumped and "zqxwsigner" not in dumped
    assert read.download_path == f"/api/v1/subject-data-jobs/{job.id}/download"
    assert read.phase is None and read.error is None
    expired = svc.to_read(job, subject_display=None, requested_by_display=None, now=now + timedelta(hours=2))
    assert expired.download_path is None
    erasure = await _job(maker, kind="erasure", status="running", source="admin", phase="postgres",
                         subject_user_id=world.stranger.id, requested_by_id=world.admin.id,
                         error_code="erasure_incomplete", error=None)
    read = svc.to_read(erasure, subject_display=vocab.ERASED_DISPLAY, requested_by_display="Zqxwadmin", now=now)
    assert (read.phase, read.download_path) == ("postgres", None)
    assert read.error.code == "erasure_incomplete" and read.error.message == vocab.ERROR_SENTENCES["erasure_incomplete"]


def test_the_service_never_commits_and_logs_no_person():
    tree = ast.parse(SERVICE_PY.read_text(encoding="utf-8"))
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)]
    assert not [c for c in calls if c.func.attr in {"commit", "rollback"}], "a stage-only service never commits"
    log_kwargs = {
        kw.arg for c in calls if isinstance(c.func.value, ast.Name) and c.func.value.id == "_slog" for kw in c.keywords
    }
    assert log_kwargs and not log_kwargs & {"username", "email", "full_name", "name", "storage_key", "key"}
