"""VIZ-607: background report exports, against a real (in-memory SQLite)
database so every transition is observed in the row, not in the calls.

1. The job's life: claim is idempotent (a redelivered message is a no-op); a
   ``running`` row whose worker died is claimable again only past Celery's
   hard limit; a late result from an older attempt cannot overwrite a newer
   one's (fenced by ``attempts``).
2. A failure is recorded on its own session (the job's rollback cannot erase
   it), with a reason; an access refusal at render time is a failure with
   that reason, not a file.
3. Retry: failed, a lost message, or a dead worker -- nothing else.
4. A broker that refuses the message leaves the row ``queued``, never failed.
5. The task's real signature takes exactly what dispatch sends.
6. The sweep deletes day folders older than 7 days and expired rows only.
7. The routes: small -> download now; large or asked -> 202 + job; download
   is 409 until complete, 410 after expiry, and audited.
"""
from __future__ import annotations

import inspect
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import select, types as sqltypes
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.sqlite import DATETIME as SQLITE_DATETIME
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.models.postgres import (
    AccessAuditLog,
    Base,
    Project,
    ReportExport,
    ReportExportStatus,
    User,
    UserRole,
)
from app.services import report_export_service as export_svc
from app.services.analytics_scope import AnalyticsScope

@compiles(JSONB, "sqlite")
def _jsonb_on_sqlite(type_, compiler, **kw):  # noqa: D103 -- JSONB columns as SQLite JSON
    return "JSON"


_TABLES = [Project.__table__, User.__table__, AccessAuditLog.__table__, ReportExport.__table__]


class _AwareDateTime(SQLITE_DATETIME):
    def result_processor(self, dialect, coltype):  # noqa: D102
        inner = super().result_processor(dialect, coltype)

        def process(value):
            parsed = inner(value) if inner is not None else value
            if isinstance(parsed, datetime) and parsed.tzinfo is None:
                return parsed.replace(tzinfo=timezone.utc)
            return parsed

        return process


@pytest.fixture
async def session_factory(monkeypatch):
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    dialect = engine.sync_engine.dialect
    dialect.colspecs = {**dialect.colspecs, sqltypes.DateTime: _AwareDateTime}
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all, tables=_TABLES)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    # The job opens its own sessions (and a separate one for failures).
    monkeypatch.setattr("app.db.postgres.AsyncSessionLocal", factory)
    yield factory
    await engine.dispose()


class _Storage:
    """An in-memory object store with the provider's interface."""

    def __init__(self):
        self.objects: dict[str, bytes] = {}
        self.deleted_prefixes: list[str] = []

    async def put_object(self, key, content, content_type="application/json", bucket=None):
        self.objects[key] = content

    async def stream_object(self, key, bucket=None):
        yield self.objects[key]

    async def delete_prefix(self, prefix, bucket=None):
        self.deleted_prefixes.append(prefix)
        gone = [k for k in self.objects if k.startswith(prefix)]
        for k in gone:
            del self.objects[k]
        return len(gone)


@pytest.fixture
def storage(monkeypatch):
    store = _Storage()
    monkeypatch.setattr("app.db.storage.get_storage_provider", lambda: store)
    return store


@pytest.fixture
async def world(session_factory):
    project = Project(id=uuid.uuid4(), name="Payment Service", slug="payment-service")
    user = User(
        id=uuid.uuid4(), username="reader", email="reader@example.test",
        hashed_password="x", role=UserRole.VIEWER.value, is_active=True,
    )
    async with session_factory() as db:
        db.add_all([project, user])
        await db.commit()
    return SimpleNamespace(project=project, user=user)


def _scope(project_id) -> AnalyticsScope:
    return AnalyticsScope(
        project_id=project_id, allowed_project_ids=None,
        release_ids=(), suite_names=("PaymentSuite",), days=30,
    )


async def _queued(session_factory, world, **overrides) -> ReportExport:
    async with session_factory() as db:
        export = await export_svc.create_export(
            db, user=world.user, scope=_scope(world.project.id), mode="window", fmt="pdf",
        )
        for key, value in overrides.items():
            setattr(export, key, value)
        await db.commit()
        return export


async def _row(session_factory, export_id) -> ReportExport:
    async with session_factory() as db:
        row = await db.get(ReportExport, export_id)
        assert row is not None
        return row


def _render_ok(monkeypatch, world, content=b"%PDF-1.4 fake"):
    async def requester_scope(db, export):
        return world.user, _scope(world.project.id)

    async def render(db, scope, mode, fmt):
        return export_svc.RenderedExport(content=content, filename="summary-payment_service-30d-window.pdf",
                                         media_type="application/pdf")

    monkeypatch.setattr(export_svc, "_requester_scope", requester_scope)
    monkeypatch.setattr(export_svc, "render_summary_export", render)


# ── 1. The job's life ─────────────────────────────────────────────────────


async def test_a_job_renders_stores_and_completes_with_an_audit_row(session_factory, storage, world, monkeypatch):
    export = await _queued(session_factory, world)
    _render_ok(monkeypatch, world)

    assert await export_svc.run_export_job(export.id) == "completed"

    row = await _row(session_factory, export.id)
    assert row.status == ReportExportStatus.COMPLETED.value and row.attempts == 1
    assert row.size_bytes == len(b"%PDF-1.4 fake") and row.error is None
    day = row.requested_at.astimezone(timezone.utc)
    assert row.storage_key == (
        f"report-exports/{day:%Y/%m/%d}/{world.project.id}/{export.id}/summary-payment_service-30d-window.pdf"
    )
    assert storage.objects[row.storage_key] == b"%PDF-1.4 fake"
    async with session_factory() as db:
        audits = (await db.execute(select(AccessAuditLog))).scalars().all()
    assert [a.action for a in audits] == ["report_summary_export_pdf"]
    assert audits[0].after_value["export_id"] == str(export.id)
    assert audits[0].after_value["suite_names"] == ["PaymentSuite"]


async def test_a_redelivered_message_is_a_no_op(session_factory, storage, world, monkeypatch):
    export = await _queued(session_factory, world)
    _render_ok(monkeypatch, world)
    assert await export_svc.run_export_job(export.id) == "completed"

    assert await export_svc.run_export_job(export.id) == "skipped"
    row = await _row(session_factory, export.id)
    assert row.status == "completed" and row.attempts == 1


async def test_a_running_row_is_reclaimable_only_past_the_hard_limit(session_factory, world):
    export = await _queued(session_factory, world)
    async with session_factory() as db:
        assert await export_svc.claim(db, export.id) == 1
    async with session_factory() as db:
        assert await export_svc.claim(db, export.id) is None, "a live worker still owns it"

    async with session_factory() as db:
        row = await db.get(ReportExport, export.id)
        row.started_at = datetime.now(timezone.utc) - export_svc._hard_time_limit() - timedelta(seconds=5)
        await db.commit()
    async with session_factory() as db:
        assert await export_svc.claim(db, export.id) == 2, "its worker is certainly gone"


async def test_an_older_attempt_cannot_overwrite_a_newer_one(session_factory, world):
    export = await _queued(session_factory, world)
    async with session_factory() as db:
        await export_svc.claim(db, export.id)
    async with session_factory() as db:
        row = await db.get(ReportExport, export.id)
        row.started_at = datetime.now(timezone.utc) - export_svc._hard_time_limit() - timedelta(seconds=5)
        await db.commit()
    async with session_factory() as db:
        assert await export_svc.claim(db, export.id) == 2

    rendered = export_svc.RenderedExport(content=b"late", filename="late.pdf", media_type="application/pdf")
    async with session_factory() as db:
        assert await export_svc.complete(db, export.id, 1, key="k/late.pdf", rendered=rendered) is False
        await db.commit()
    async with session_factory() as db:
        await export_svc.fail(db, export.id, 1, "late failure")
    row = await _row(session_factory, export.id)
    assert (row.status, row.attempts, row.storage_key, row.error) == ("running", 2, None, None)


# ── 2. Failures ───────────────────────────────────────────────────────────


async def test_a_render_error_is_recorded_failed_with_a_reason_and_no_file(session_factory, storage, world, monkeypatch):
    export = await _queued(session_factory, world)
    _render_ok(monkeypatch, world)

    async def broken(db, scope, mode, fmt):
        raise ValueError("chart data was malformed")

    monkeypatch.setattr(export_svc, "render_summary_export", broken)

    assert await export_svc.run_export_job(export.id) == "failed"
    row = await _row(session_factory, export.id)
    assert row.status == "failed" and row.finished_at is not None
    assert "ValueError: chart data was malformed" in row.error
    assert storage.objects == {}


async def test_access_removed_before_the_render_is_a_refusal_not_a_file(session_factory, storage, world, monkeypatch):
    export = await _queued(session_factory, world)
    rendered = []

    async def deny(*args, **kwargs):
        raise HTTPException(status_code=403, detail="Not a member of this project")

    async def render(*args, **kwargs):
        rendered.append(True)

    monkeypatch.setattr("app.services.analytics_scope.resolve_analytics_scope", deny)
    monkeypatch.setattr(export_svc, "render_summary_export", render)

    assert await export_svc.run_export_job(export.id) == "failed"
    row = await _row(session_factory, export.id)
    assert row.error == "Access to this report was refused when the export ran (403)."
    assert rendered == [] and storage.objects == {}


async def test_a_deactivated_requester_is_refused(session_factory, storage, world):
    export = await _queued(session_factory, world)
    async with session_factory() as db:
        user = await db.get(User, world.user.id)
        user.is_active = False
        await db.commit()

    assert await export_svc.run_export_job(export.id) == "failed"
    row = await _row(session_factory, export.id)
    assert "no longer has an active account" in row.error


# ── 3. Retry ──────────────────────────────────────────────────────────────


async def test_retry_is_offered_only_where_the_work_can_be_lost(session_factory, world):
    now = datetime.now(timezone.utc)
    hard = export_svc._hard_time_limit()
    cases = {
        "failed": (dict(status="failed"), True),
        "completed": (dict(status="completed"), False),
        "queued just now": (dict(status="queued"), False),
        "queued, message lost": (dict(status="queued", requested_at=now - timedelta(minutes=3)), True),
        "running, worker alive": (dict(status="running", started_at=now - timedelta(minutes=1)), False),
        "running, worker dead": (dict(status="running", started_at=now - hard - timedelta(seconds=5)), True),
    }
    for name, (fields, expected) in cases.items():
        export = await _queued(session_factory, world, **fields)
        assert export_svc.retryable(await _row(session_factory, export.id), now) is expected, name


async def test_retry_requeues_a_failed_export_and_it_then_completes(session_factory, storage, world, monkeypatch):
    export = await _queued(session_factory, world)
    _render_ok(monkeypatch, world)

    async def broken(db, scope, mode, fmt):
        raise RuntimeError("storage was down")

    monkeypatch.setattr(export_svc, "render_summary_export", broken)
    assert await export_svc.run_export_job(export.id) == "failed"

    async with session_factory() as db:
        row = await db.get(ReportExport, export.id)
        assert await export_svc.retry(db, row) is True
        await db.commit()
    row = await _row(session_factory, export.id)
    assert (row.status, row.error, row.finished_at) == ("queued", None, None)

    _render_ok(monkeypatch, world)
    assert await export_svc.run_export_job(export.id) == "completed"
    row = await _row(session_factory, export.id)
    assert (row.status, row.attempts) == ("completed", 2)


# ── 4 + 5. Dispatch ───────────────────────────────────────────────────────


def test_dispatch_sends_what_the_real_task_signature_takes(monkeypatch):
    from app.worker import tasks

    sent = []
    monkeypatch.setattr(tasks.generate_report_export, "apply_async", lambda **kw: sent.append(kw))
    export_id = uuid.uuid4()

    assert export_svc.dispatch(export_id) is True
    (call,) = sent
    inspect.signature(tasks.generate_report_export.run).bind(*call["args"], **call.get("kwargs", {}))
    assert call["args"] == [str(export_id)]
    # The default queue, by the route table and on the task itself.
    assert tasks.generate_report_export.queue == "default"


async def test_a_broker_that_refuses_leaves_the_job_queued_not_failed(session_factory, world, monkeypatch):
    from app.worker import tasks

    def refuse(**kw):
        raise ConnectionError("broker unreachable")

    monkeypatch.setattr(tasks.generate_report_export, "apply_async", refuse)
    export = await _queued(session_factory, world)

    assert export_svc.dispatch(export.id) is False
    row = await _row(session_factory, export.id)
    assert row.status == "queued" and row.error is None


def test_the_sweep_is_scheduled_nightly():
    from app.worker.celery_app import celery_app

    entry = celery_app.conf.beat_schedule["nightly-report-export-sweep"]
    assert entry["task"] == "app.worker.tasks.sweep_report_exports"
    from app.worker import tasks

    assert tasks.sweep_report_exports.name == entry["task"]


# ── 6. Retention ──────────────────────────────────────────────────────────


async def test_the_sweep_deletes_old_day_folders_and_expired_rows_only(session_factory, storage, world):
    now = datetime(2026, 10, 20, 1, 15, tzinfo=timezone.utc)
    old = await _queued(session_factory, world, requested_at=now - timedelta(days=9), expires_at=now - timedelta(days=2))
    fresh = await _queued(session_factory, world, requested_at=now - timedelta(days=1), expires_at=now + timedelta(days=6))
    storage.objects["report-exports/2026/10/11/p/old/a.pdf"] = b"old"
    storage.objects["report-exports/2026/10/19/p/fresh/b.pdf"] = b"fresh"

    async with session_factory() as db:
        out = await export_svc.sweep_expired(db, now=now)

    assert out == {"objects": 1, "rows": 1}
    assert list(storage.objects) == ["report-exports/2026/10/19/p/fresh/b.pdf"]
    # Nothing younger than 7 full days is ever offered to delete_prefix.
    # Day 10-12's last request expires 10-19 24:00: deletable. Day 10-13's
    # expires 10-20 24:00, after this sweep: never offered to delete_prefix.
    assert "report-exports/2026/10/12/" in storage.deleted_prefixes
    assert not any(p >= "report-exports/2026/10/13/" for p in storage.deleted_prefixes)
    async with session_factory() as db:
        ids = {r.id for r in (await db.execute(select(ReportExport))).scalars()}
    assert ids == {fresh.id} and old.id not in ids


# ── 7. Routes ─────────────────────────────────────────────────────────────


async def test_a_small_report_downloads_now_and_a_large_one_queues(session_factory, world, monkeypatch):
    from app.routers import summary_report as router
    from app.worker import tasks

    sent = []
    monkeypatch.setattr(tasks.generate_report_export, "apply_async", lambda **kw: sent.append(kw))

    async def estimate(db, project_id, days):
        return 1_000

    monkeypatch.setattr(export_svc, "estimate_tests", estimate)
    monkeypatch.setattr(router.settings, "REPORT_EXPORT_SYNC_MAX_TESTS", 5_000)

    async with session_factory() as db:
        small = await router.request_summary_report_export(
            format="pdf", mode="window", background=False, scope=_scope(world.project.id),
            db=db, current_user=world.user,
        )
    assert small.delivery == "download" and small.export is None and sent == []

    async with session_factory() as db:
        asked = await router.request_summary_report_export(
            format="xlsx", mode="latest", background=True, scope=_scope(world.project.id),
            db=db, current_user=world.user,
        )
    assert asked.status_code == 202
    import json

    body = json.loads(asked.body)
    assert body["delivery"] == "background" and body["dispatched"] is True
    assert body["export"]["status"] == "queued" and body["export"]["format"] == "xlsx"
    assert body["export"]["params"] == {"mode": "latest", "days": 30, "release_ids": [], "suite_names": ["PaymentSuite"]}
    assert sent[0]["args"] == [body["export"]["id"]]

    monkeypatch.setattr(router.settings, "REPORT_EXPORT_SYNC_MAX_TESTS", 10)
    async with session_factory() as db:
        large = await router.request_summary_report_export(
            format="pdf", mode="window", background=False, scope=_scope(world.project.id),
            db=db, current_user=world.user,
        )
    assert large.status_code == 202 and len(sent) == 2


async def test_download_is_409_until_complete_410_after_expiry_and_audited(session_factory, storage, world, monkeypatch):
    from app.routers import summary_report as router

    export = await _queued(session_factory, world)
    async with session_factory() as db:
        with pytest.raises(HTTPException) as exc:
            await router.download_summary_report_export(export.id, db=db, current_user=world.user)
    assert exc.value.status_code == 409

    _render_ok(monkeypatch, world, content=b"%PDF-1.4 the file")
    await export_svc.run_export_job(export.id)
    async with session_factory() as db:
        response = await router.download_summary_report_export(export.id, db=db, current_user=world.user)
    chunks = [chunk async for chunk in response.body_iterator]
    assert b"".join(chunks) == b"%PDF-1.4 the file"
    assert response.media_type == "application/pdf"
    assert 'filename="summary-payment_service-30d-window.pdf"' in response.headers["content-disposition"]
    async with session_factory() as db:
        actions = [a.action for a in (await db.execute(select(AccessAuditLog))).scalars()]
    assert actions == ["report_summary_export_pdf", "report_summary_export_pdf_download"]

    async with session_factory() as db:
        row = await db.get(ReportExport, export.id)
        row.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        await db.commit()
    async with session_factory() as db:
        with pytest.raises(HTTPException) as exc:
            await router.download_summary_report_export(export.id, db=db, current_user=world.user)
    assert exc.value.status_code == 410


async def test_retry_route_refuses_what_is_not_retryable(session_factory, world, monkeypatch):
    from app.routers import summary_report as router
    from app.worker import tasks

    monkeypatch.setattr(tasks.generate_report_export, "apply_async", lambda **kw: None)
    export = await _queued(session_factory, world)
    async with session_factory() as db:
        with pytest.raises(HTTPException) as exc:
            await router.retry_summary_report_export(export.id, db=db, current_user=world.user)
    assert exc.value.status_code == 409

    async with session_factory() as db:
        row = await db.get(ReportExport, export.id)
        row.status = "failed"
        row.error = "boom"
        await db.commit()
    async with session_factory() as db:
        out = await router.retry_summary_report_export(export.id, db=db, current_user=world.user)
    assert out.status == "queued" and out.error is None and out.retryable is False
