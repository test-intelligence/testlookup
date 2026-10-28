"""W22b ACT-03 E-6 / E-9 / E-10 — the criteria worker's export phase, ``bytes_reclaimed``, the janitor.

The worker is driven through ``execute_criteria_deletion_task.run`` with every
session a recording fake (a fresh one per ``AsyncSessionLocal()``), the export
itself stubbed (``run_export`` is covered by ``test_retention_export_service``)
and the per-run deletion stubbed. The point of every test: no run is deleted
unless the archive was verified (and, for ``collect``, acknowledged).
"""
from __future__ import annotations

import asyncio
import inspect
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.dialects import postgresql

from app.services import deletion_job_service as jobs
from app.services import retention_export_service as export_svc
from app.services.retention_hold_service import HoldRef
from app.services.retention_references import ReferenceReport
from app.services.retention_vocab import EXPORT_JOB_ERRORS, EXPORT_JOB_EXPIRED_ONE
from tests.test_retention_export_service import MemStorage

PROJECT = uuid.UUID("00000000-0000-4000-8000-0000000000dd")


class _R:
    def __init__(self, scalar=None, rows=()):
        self._scalar, self._rows = scalar, list(rows)

    def scalar_one_or_none(self):
        return self._scalar

    def all(self):
        return list(self._rows)


class _WSession:
    """A fresh fake per AsyncSessionLocal(); the locked run answers by id."""

    def __init__(self, world):
        self.world = world
        self.sql: list[str] = []
        self.added: list = []
        world.sessions.append(self)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False

    async def execute(self, stmt, *_a, **_kw):
        compiled = stmt.compile(dialect=postgresql.dialect())
        sql = str(compiled)
        self.sql.append(sql)
        if sql.startswith("UPDATE"):
            return SimpleNamespace(rowcount=1, all=lambda: [])
        if "FROM test_runs" in sql and "FOR UPDATE" in sql:
            ids = [v for v in compiled.params.values() if isinstance(v, uuid.UUID)]
            return _R(scalar=self.world.runs.get(ids[0]) if ids else None)
        return _R()

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.world.events.append("commit")

    async def rollback(self):
        return None


def _run(run_id):
    return SimpleNamespace(id=run_id, project_id=PROJECT, status="PASSED", minio_prefix=None)


def _plan(*, target="collect", verified=False, acknowledged=False, excluded=(), meta=None):
    return jobs.ExportPlan(tier="evidence", target=target, verified=verified, acknowledged=acknowledged,
                           run_ids_excluded=tuple(excluded), location="exports/k.zip" if verified else None,
                           meta=dict(meta or {"estimates": {}}))


def _outcome(archived, excluded=(), holds=()):
    return export_svc.ExportOutcome(
        location="exports/k.zip", manifest_sha256="ab" * 32, archive_sha256="cd" * 32, bytes=1234,
        file_count=7, signing={"state": "unsigned"}, run_ids_archived=list(archived),
        run_ids_excluded=list(excluded), missing_run_ids=[], holds=list(holds), objects_not_archived=0,
    )


@pytest.fixture
def world(monkeypatch, mocker):
    from app.db import mongo as mongo_module
    from app.db import postgres as pg
    from app.db import storage as storage_module
    from app.services import retention_references, run_deletion_service, semantic_search
    from app.worker import tasks

    w = SimpleNamespace(runs={}, sessions=[], events=[], run_ids=[], plan=None,
                        storage=MemStorage(), blocked=set())
    monkeypatch.setattr(pg, "AsyncSessionLocal", lambda: _WSession(w))
    monkeypatch.setattr(mongo_module, "get_mongo_db", lambda: object())
    monkeypatch.setattr(storage_module, "get_storage_provider", lambda: w.storage)
    monkeypatch.setattr(tasks, "_run_async", asyncio.run)
    monkeypatch.setattr(jobs, "start_frozen_set", AsyncMock(side_effect=lambda *a, **k: list(w.run_ids)))
    monkeypatch.setattr(jobs, "get_export_plan", AsyncMock(side_effect=lambda *a, **k: w.plan))
    w.close = mocker.patch.object(jobs, "close_job", AsyncMock(return_value=True))
    monkeypatch.setattr(retention_references, "references_broken", AsyncMock(return_value=ReferenceReport(True, [])))

    async def _blockers(db, *, run, mongo):
        return ["Held: Audit sentinel"] if run.id in w.blocked else []

    monkeypatch.setattr(run_deletion_service, "execution_blockers", _blockers)
    monkeypatch.setattr(run_deletion_service, "hold_refs_for_refusal", AsyncMock(return_value=[]))
    monkeypatch.setattr(semantic_search, "purge_run_documents", AsyncMock(return_value=0))
    w.delete = mocker.patch.object(run_deletion_service, "perform_run_deletion",
                                   AsyncMock(return_value={"postgres": {"runs": 1}}))
    w.bump = mocker.patch("app.services.cache_service.bump_analytics_epoch", AsyncMock())
    w.task = tasks.execute_criteria_deletion_task
    return w


def _seed(world, n):
    ids = [uuid.uuid4() for _ in range(n)]
    for i, run_id in enumerate(ids):
        world.runs[run_id] = _run(run_id)
        world.storage.objects[(None, f"uploads/{PROJECT}/{run_id}/a.json")] = b"x" * (100 * (i + 1))
    world.run_ids = ids
    return ids


def _go(world):
    return world.task.run(str(uuid.uuid4()), str(PROJECT), None)


# ── the export phase ─────────────────────────────────────────────────────────


def test_collect_stops_at_awaiting_collection_and_deletes_nothing(world, monkeypatch):
    ids = _seed(world, 2)
    world.plan = _plan(target="collect")
    written = []

    async def _export(db, **kw):
        await kw["on_written"]("exports/k.zip")
        written.append(kw)
        return _outcome(ids)

    monkeypatch.setattr(export_svc, "run_export", _export)
    verified = AsyncMock(return_value=True)
    monkeypatch.setattr(jobs, "stage_export_verified", verified)

    assert _go(world) == {"executed": False, "status": "awaiting_collection"}
    world.delete.assert_not_awaited()
    world.bump.assert_not_awaited()
    world.close.assert_not_awaited()
    kwargs = verified.await_args.kwargs
    assert (kwargs["target"], kwargs["ttl_hours"]) == ("collect", 72)
    assert kwargs["meta"]["run_count"] == 2 and "estimates" in kwargs["meta"]
    written_sql = [s for sess in world.sessions for s in sess.sql if "export_location" in s and s.startswith("UPDATE")]
    assert written_sql, "the location is committed BEFORE verification (stage_export_written)"
    audits = [o for sess in world.sessions for o in sess.added if getattr(o, "action", None) == "export_verified"]
    assert len(audits) == 1 and "exports/k.zip" not in str(audits[0].changed_fields)


def test_retain_runs_the_loop_over_the_archived_runs_and_records_the_holds(world, monkeypatch):
    kept, held = _seed(world, 2)
    ref = HoldRef(id=uuid.uuid4(), entity_type="run", entity_id=held, entity_label="run h",
                  reason="Audit sentinel", expires_at=None)
    world.plan = _plan(target="retain")
    monkeypatch.setattr(export_svc, "run_export", AsyncMock(return_value=_outcome([kept], [held], [ref])))
    monkeypatch.setattr(jobs, "stage_export_verified", AsyncMock(return_value=True))

    result = _go(world)

    assert result["executed"] is True and result["deleted"] == 1
    assert [c.kwargs["run"].id for c in world.delete.await_args_list] == [kept]
    kwargs = world.close.await_args.kwargs
    assert kwargs["holds_honoured"]["run_ids_kept"] == [str(held)]
    assert kwargs["holds_honoured"]["holds"] == [ref.as_json()]
    assert kwargs["export_meta"]["run_ids_excluded"] == [str(held)]
    assert kwargs["bytes_reclaimed"] == 100, "the archived (deleted) run's bytes only"


def test_the_delivery_after_the_acknowledgement_skips_the_export(world, monkeypatch):
    ids = _seed(world, 3)
    excluded = str(ids[2])
    ref = HoldRef(id=uuid.uuid4(), entity_type="run", entity_id=ids[2], entity_label="r3",
                  reason="Audit sentinel", expires_at=None)
    world.plan = _plan(target="collect", verified=True, acknowledged=True, excluded=[excluded],
                       meta={"run_ids_excluded": [excluded], "holds_excluded": [ref.as_json()]})
    world.storage.objects[("compliance-packs", "exports/k.zip")] = b"zip"
    run_export = AsyncMock()
    monkeypatch.setattr(export_svc, "run_export", run_export)

    result = _go(world)

    run_export.assert_not_awaited()
    assert result["deleted"] == 2
    assert sorted(c.kwargs["run"].id for c in world.delete.await_args_list) == sorted(ids[:2])
    kwargs = world.close.await_args.kwargs
    assert kwargs["holds_honoured"]["run_ids_kept"] == [excluded]
    assert kwargs["bytes_reclaimed"] == 300
    assert ("compliance-packs", "exports/k.zip") not in world.storage.objects, "removed after the deletion"
    cleared = [s for sess in world.sessions for s in sess.sql if s.startswith("UPDATE") and "export_location" in s]
    assert cleared, "the location is cleared once the object is gone"


def test_a_stray_delivery_of_an_unacknowledged_job_deletes_nothing(world, monkeypatch):
    _seed(world, 1)
    world.plan = _plan(target="collect", verified=True, acknowledged=False)
    monkeypatch.setattr(export_svc, "run_export", AsyncMock())
    assert _go(world) == {"executed": False, "status": "awaiting_collection"}
    world.delete.assert_not_awaited()


@pytest.mark.parametrize("raised", ["export_failed", "unexpected"])
def test_a_failed_export_fails_the_job_and_deletes_nothing(raised, world, monkeypatch):
    _seed(world, 2)
    world.plan = _plan(target="retain")
    failure = (export_svc.ExportFailed("unverified", reason="digest mismatch")
               if raised == "export_failed" else RuntimeError("worker lost its spool"))
    monkeypatch.setattr(export_svc, "run_export", AsyncMock(side_effect=failure))

    assert _go(world) == {"executed": False, "status": "failed"}
    world.delete.assert_not_awaited()
    world.bump.assert_not_awaited()
    kwargs = world.close.await_args.kwargs
    assert kwargs["status"] == jobs.FAILED and kwargs["expected_status"] == jobs.RUNNING
    expected = (EXPORT_JOB_ERRORS["unverified"].format(reason="digest mismatch") if raised == "export_failed"
                else EXPORT_JOB_ERRORS["build"].format(error_type="RuntimeError"))
    assert kwargs["error"] == expected
    assert "nothing was deleted" in kwargs["error"]
    assert kwargs["export_meta"]["failure"] in ("unverified", "build")


# ── bytes_reclaimed (E-9) ─────────────────────────────────────────────────────


def test_bytes_reclaimed_counts_deleted_runs_only_and_no_export_is_todays_path(world):
    ids = _seed(world, 3)
    world.blocked = {ids[1]}
    world.plan = None

    result = _go(world)

    assert (result["deleted"], result["failed"]) == (2, 1)
    kwargs = world.close.await_args.kwargs
    assert kwargs["bytes_reclaimed"] == 100 + 300, "the refused run's 200 bytes stay"
    assert "export_meta" not in kwargs


def test_bytes_reclaimed_is_not_measured_when_the_listing_fails(world, monkeypatch):
    from tests.test_retention_export_service import RaisingListStorage

    _seed(world, 1)
    world.storage = RaisingListStorage()
    world.plan = None
    _go(world)
    assert "bytes_reclaimed" not in world.close.await_args.kwargs, "NULL = not measured, never 0"


def test_the_single_run_worker_writes_bytes_reclaimed(world, monkeypatch):
    from app.worker import tasks

    (run_id,) = _seed(world, 1)
    monkeypatch.setattr("app.services.retention_references.references_broken",
                        AsyncMock(return_value=ReferenceReport(True, [])))
    tasks.delete_run_everywhere.run(str(run_id), str(uuid.uuid4()))
    assert world.close.await_args.kwargs["bytes_reclaimed"] == 100


# ── the janitor (E-10) ───────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("ttl_hours", [72, 1])
async def test_the_janitor_expires_fails_and_removes_and_deletes_no_run(world, monkeypatch, ttl_hours):
    from app.core.config import settings
    from app.worker import tasks

    # A one-hour window read "Not collected within 1 hours" on the W22b local demo stack.
    monkeypatch.setattr(settings, "RETENTION_EXPORT_COLLECT_TTL_HOURS", ttl_hours)
    now = datetime.now(timezone.utc)
    expired_job, interrupted_job, done_job, stuck_job = (uuid.uuid4() for _ in range(4))
    seen = {}

    async def _expire(db, *, now, error, limit=100):
        seen["error"] = error
        return [(expired_job, PROJECT, "summary", "collect")]

    monkeypatch.setattr(jobs, "expire_overdue", _expire)
    monkeypatch.setattr(jobs, "fail_interrupted", AsyncMock(return_value=[(interrupted_job, PROJECT)]))
    monkeypatch.setattr(jobs, "archives_to_remove", AsyncMock(return_value=[
        (done_job, PROJECT, "exports/done.zip"), (stuck_job, PROJECT, "exports/stuck.zip"),
    ]))

    class _Store(MemStorage):
        async def delete_object(self, key, bucket=None):
            if "stuck" in key:
                raise ConnectionError("store down")
            await super().delete_object(key, bucket)

    store = _Store({("compliance-packs", "exports/done.zip"): b"z"})
    summary = await tasks._sweep_deletion_exports(now=now, storage=store)

    assert summary == {"expired": 1, "interrupted": 1, "removed": 1, "remove_failed": 1}
    assert seen["error"] == (
        EXPORT_JOB_EXPIRED_ONE if ttl_hours == 1 else EXPORT_JOB_ERRORS["expired"].format(hours=ttl_hours)
    )
    assert "1 hours" not in seen["error"]
    assert not store.objects
    actions = sorted(o.action for sess in world.sessions for o in sess.added)
    assert actions == ["archive_removed", "export_expired", "export_failed"]
    cleared = [s for sess in world.sessions for s in sess.sql if s.startswith("UPDATE")]
    assert len(cleared) == 1, "only the removed archive's location is cleared (the janitor retries the other)"
    world.delete.assert_not_awaited()
    source = inspect.getsource(tasks._sweep_deletion_exports)
    assert "perform_run_deletion" not in source and "TestRun" not in source


# ── the stage-only transitions, compiled ─────────────────────────────────────


class _Recorder:
    def __init__(self):
        self.sql: list[str] = []
        self.params: list[dict] = []

    async def execute(self, stmt, *_a, **_kw):
        compiled = stmt.compile(dialect=postgresql.dialect())
        self.sql.append(str(compiled))
        self.params.append(dict(compiled.params))
        return SimpleNamespace(rowcount=1, all=lambda: [], scalar_one_or_none=lambda: None)


@pytest.mark.asyncio
async def test_the_transitions_are_guarded_by_the_state_they_leave():
    db, now = _Recorder(), datetime.now(timezone.utc)
    outcome = _outcome([uuid.uuid4()])
    assert await jobs.stage_export_verified(db, job_id=uuid.uuid4(), outcome=outcome, target="collect",
                                            ttl_hours=72, now=now)
    sql, params = db.sql[-1], db.params[-1]
    assert "WHERE deletion_jobs.id = " in sql and "deletion_jobs.status = " in sql
    assert params["status"] == jobs.AWAITING_COLLECTION
    assert params["collect_expires_at"] - now == timedelta(hours=72)
    assert params["status_1"] == jobs.RUNNING

    await jobs.stage_export_verified(db, job_id=uuid.uuid4(), outcome=outcome, target="retain",
                                     ttl_hours=72, now=now)
    assert "status" not in db.params[-1], "retain stays RUNNING (the loop follows)"
    assert "collect_expires_at" not in db.params[-1]

    await jobs.expire_overdue(db, now=now, error="x")
    assert "FOR UPDATE SKIP LOCKED" in db.sql[-1] and "RETURNING" in db.sql[-1]
    await jobs.fail_interrupted(db, now=now, older_than=timedelta(seconds=2160))
    assert "export_verified_at IS NULL" in db.sql[-1] and "export_tier IS NOT NULL" in db.sql[-1]
    await jobs.archives_to_remove(db)
    assert "export_location IS NOT NULL" in db.sql[-1]
    assert "commit" not in inspect.getsource(jobs.expire_overdue)


@pytest.mark.asyncio
async def test_get_export_plan_is_none_for_a_job_without_an_export():
    class _One:
        def __init__(self, job):
            self.job, self.calls = job, 0

        async def execute(self, _stmt):
            self.calls += 1
            return _R(scalar=self.job)

    assert await jobs.get_export_plan(_One(None), uuid.uuid4()) is None
    assert await jobs.get_export_plan(_One(SimpleNamespace(export_tier=None)), uuid.uuid4()) is None
    from unittest.mock import MagicMock
    assert await jobs.get_export_plan(_One(MagicMock()), uuid.uuid4()) is None, "a scripted fake = no export"
    row = SimpleNamespace(export_tier="full", export_target="retain", export_verified_at=None,
                          acknowledged_at=None, export_location=None,
                          export_meta={"run_ids_excluded": ["a"], "estimates": {}})
    db = _One(row)
    plan = await jobs.get_export_plan(db, uuid.uuid4())
    assert (plan.tier, plan.target, plan.verified, plan.run_ids_excluded) == ("full", "retain", False, ("a",))
    assert db.calls == 1, "ONE read"
