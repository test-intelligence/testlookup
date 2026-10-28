"""W22b E-12 — a retained deletion archive rides its job row.

A ``retain`` export stays in the compliance-packs bucket until its
``deletion_jobs`` row leaves on the AUDIT clock. The policy purge reads the
archives of the rows it will retire (ONE statement, both modes), the preview
counts them (``export_archives_expired``), and execute deletes each OBJECT
first, then the rows — a row whose object delete failed survives for the next
sweep (the expired-pack rule). The storage key is never logged. Hermetic: the
fakes of ``test_retention_purge`` (their two deliberate W22b routes).
"""
from __future__ import annotations

import inspect
import uuid

import pytest

pytest.importorskip("sqlalchemy")

from sqlalchemy.dialects import postgresql  # noqa: E402

from app.services import retention_service as svc  # noqa: E402
from tests.test_retention_purge import NOW, PROJECT_ID, _FakeStorage, _purge_fixture  # noqa: E402


def _archives():
    first, second = uuid.uuid4(), uuid.uuid4()
    return [
        (first, f"exports/2025/01/02/{PROJECT_ID}/{first}.zip"),
        (second, f"exports/2025/01/03/{PROJECT_ID}/{second}.zip"),
    ]


class _Recorder:
    def __init__(self):
        self.calls: list[tuple[str, str, dict]] = []

    def __getattr__(self, level):
        def _log(event, **kwargs):
            self.calls.append((level, event, kwargs))
        return _log


def _capture(db):
    """Keep every statement ``db`` sees, compiled for Postgres."""
    seen: list = []
    original = db.execute

    async def _execute(stmt):
        seen.append(stmt)
        return await original(stmt)

    db.execute = _execute
    return seen


async def test_the_select_reads_only_the_rows_the_audit_clock_retires():
    db, mongo, storage, _events = _purge_fixture(run_ages_days=(400,))
    seen = _capture(db)
    await svc.run_purge(db, project_id=PROJECT_ID, mode="preview", now=NOW, mongo=mongo, storage=storage)
    selects = [
        str(s.compile(dialect=postgresql.dialect()))
        for s in seen
        if type(s).__name__ == "Select" and "deletion_jobs.export_location" in str(s)
    ]
    assert len(selects) == 1, "ONE statement"
    sql = selects[0]
    assert sql.startswith("SELECT deletion_jobs.id, deletion_jobs.export_location")
    where = sql.split("WHERE", 1)[1]
    assert "deletion_jobs.export_location IS NOT NULL" in where
    assert "deletion_jobs.project_id = " in where and "deletion_jobs.requested_at < " in where
    assert "deletion_jobs.status IN " in where, "terminal jobs only (the audit-clock filter)"
    assert "retention_holds" in where, "a project hold keeps the row, so it keeps the archive"


async def test_the_preview_counts_the_archives_and_deletes_nothing():
    db, mongo, storage, events = _purge_fixture(run_ages_days=(400,))
    db.retained_archives = _archives()
    out = await svc.run_purge(db, project_id=PROJECT_ID, mode="preview", now=NOW, mongo=mongo, storage=storage)
    assert out["candidates"]["export_archives_expired"] == 2
    assert storage.deleted_objects == []
    assert not any(kind == "pg_delete" for kind, _ in events)


async def test_execute_deletes_each_object_before_the_rows():
    db, mongo, storage, events = _purge_fixture(run_ages_days=(400,))
    archives = _archives()
    db.retained_archives = archives
    out = await svc.run_purge(db, project_id=PROJECT_ID, mode="execute", now=NOW, mongo=mongo, storage=storage)
    keys = [key for _job, key in archives]
    assert [k for k in storage.deleted_objects if k.startswith("exports/")] == keys
    row_delete = events.index(("pg_delete", "deletion_jobs"))
    for key in keys:
        assert events.index(("minio_delete_object", key)) < row_delete, "object first, row second"
    assert out["counts"]["exports"] == {"archives_deleted": 2}
    assert "archives_deleted" not in out["counts"]["postgres"], "objects, not rows: never summed as records"
    job_delete = next(s for s in db.delete_stmts if s.table.name == "deletion_jobs")
    assert "NOT IN" not in str(job_delete.compile(dialect=postgresql.dialect()))


async def test_a_failed_object_delete_keeps_its_row_and_never_logs_the_key(monkeypatch):
    db, mongo, _storage, events = _purge_fixture(run_ages_days=(400,))
    archives = _archives()
    failing_job, failing_key = archives[0]
    db.retained_archives = archives

    class _Flaky(_FakeStorage):
        async def delete_object(self, key, bucket=None):
            if key == failing_key:
                raise TimeoutError("object store did not answer")
            await super().delete_object(key, bucket=bucket)

    storage = _Flaky(events)
    recorder = _Recorder()
    monkeypatch.setattr(svc, "logger", recorder)
    out = await svc.run_purge(db, project_id=PROJECT_ID, mode="execute", now=NOW, mongo=mongo, storage=storage)

    assert out["counts"]["exports"] == {"archives_deleted": 1}
    job_delete = next(s for s in db.delete_stmts if s.table.name == "deletion_jobs")
    compiled = job_delete.compile(dialect=postgresql.dialect())
    assert "deletion_jobs.id NOT IN" in str(compiled)
    assert [failing_job] in [v for v in compiled.params.values() if isinstance(v, list)]
    warnings = [c for c in recorder.calls if c[1] == "retention_export_archive_delete_failed"]
    assert len(warnings) == 1 and warnings[0][2]["job_id"] == str(failing_job)
    assert warnings[0][2]["error_type"] == "TimeoutError"
    for key in (k for _j, k in archives):
        assert key not in repr(recorder.calls), "the storage key is never logged"


def test_explicit_deletes_take_no_archive():
    plan = svc._ExecutionPlan(
        event_archive_where=(), access_audit_where=(), tc_audit_where=(), activity_where=(),
        deletion_job_where=(), revoked_share_link_where=(), provenance_where=(),
        memory_expired_ids=[], expired_packs=[],
    )
    assert plan.retained_archives == [], "the field defaults empty"
    source = inspect.getsource(svc.resolve_run_candidates)
    assert "retained_archives=[]" in source, "the explicit path passes an empty list (never project-wide rows)"
