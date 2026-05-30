"""Regression: live-stream persist could create duplicate per-test rows.

Bug (found 2026-05-30 multi-pass review, `live-stream-ingestion` audit
finding #2): ``worker/tasks.persist_live_session`` bulk-inserted TestCase
rows with a plain ``INSERT`` and no conflict handling, and there was no
unique constraint on ``(test_run_id, test_fingerprint)`` — only a
non-unique index. The Redis event buffer is deleted only AFTER
``finalize_run``, so a task retry triggered by a post-commit failure
re-presented the same rows (``events`` still non-empty → the dedup skip
doesn't fire) and inserted them again → duplicate per-test rows that
inflate counts. The file-ingestion path
(``services/ingestion._upsert_test_case``) already treats
``(test_run_id, test_fingerprint)`` as the idempotency key.

Fix (two parts):
  1. migration 0089 adds ``uq_test_cases_run_fingerprint`` unique constraint.
  2. both inserts in persist_live_session use
     ``pg_insert(...).on_conflict_do_nothing(index_elements=[...])``.

This file pins BOTH parts.
"""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import pytest


# ── Part 1: the model carries the unique constraint ──────────────────────────

def test_testcase_has_unique_run_fingerprint_constraint():
    from sqlalchemy import UniqueConstraint
    from app.models.postgres import TestCase

    uniques = [
        c for c in TestCase.__table__.constraints
        if isinstance(c, UniqueConstraint)
    ]
    cols_by_name = {
        c.name: {col.name for col in c.columns} for c in uniques
    }
    assert "uq_test_cases_run_fingerprint" in cols_by_name, (
        "TestCase lost its (test_run_id, test_fingerprint) unique constraint — "
        "live-stream persist retries can now create duplicate per-test rows."
    )
    assert cols_by_name["uq_test_cases_run_fingerprint"] == {
        "test_run_id", "test_fingerprint",
    }


# ── Part 2: the persist inserts compile to ON CONFLICT DO NOTHING ────────────

class _Result:
    def __init__(self, *, scalar=None):
        self._scalar = scalar

    def scalar(self):
        return self._scalar

    def scalar_one_or_none(self):
        return self._scalar


class _CapturingSession:
    """Records every statement passed to execute() so the test can inspect
    the INSERT the placeholder path emits."""

    def __init__(self, results):
        self._results = list(results)
        self.statements = []
        self.added = []
        self.committed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def execute(self, stmt, params=None):
        self.statements.append(stmt)
        if self._results:
            return self._results.pop(0)
        return _Result(scalar=None)

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        for obj in self.added:
            if getattr(obj, "id", None) is None:
                try:
                    obj.id = uuid.uuid4()
                except Exception:
                    pass

    async def commit(self):
        self.committed = True


def _compiled_inserts(statements):
    """Return the compiled-SQL strings of INSERT statements against test_cases."""
    from sqlalchemy.dialects import postgresql

    out = []
    for stmt in statements:
        try:
            sql = str(stmt.compile(dialect=postgresql.dialect()))
        except Exception:
            continue
        if "INSERT INTO test_cases" in sql:
            out.append(sql)
    return out


def test_persist_placeholder_insert_uses_on_conflict_do_nothing():
    """Drive the placeholder path (empty buffer + final_state counts) and
    assert the emitted INSERT carries ON CONFLICT DO NOTHING so a retry is a
    no-op rather than a duplicate."""
    pytest.importorskip("celery")

    from app.worker import tasks as worker_tasks

    run_id = str(uuid.uuid4())
    project_id = str(uuid.uuid4())

    # execute() call order inside _run:
    #   1. dedup COUNT  → 0 (proceed)
    #   2. TestRun select → None (create new)
    #   3. placeholder INSERT (captured)
    session = _CapturingSession(results=[_Result(scalar=0), _Result(scalar=None)])

    class _Redis:
        async def lrange(self, *a):
            return []                      # empty buffer → placeholder path

        async def delete(self, *a):
            return None

    with (
        patch("app.db.postgres.AsyncSessionLocal", return_value=session),
        patch("app.db.redis_client.get_redis", return_value=_Redis()),
        patch(
            "app.services.ingestion_pipeline.finalize_run",
            new=AsyncMock(),
        ),
    ):
        result = worker_tasks.persist_live_session.apply(kwargs={
            "run_id": run_id,
            "project_id": project_id,
            "build_number": "b1",
            # final_state with a total>0 + counts → placeholder synthesis fires
            "final_state": {"total": 2, "passed": 2},
        })

    if not result.successful():
        raise AssertionError(f"persist_live_session failed: {result.traceback}")

    inserts = _compiled_inserts(session.statements)
    assert inserts, "No INSERT INTO test_cases was emitted by the placeholder path"
    assert all("ON CONFLICT" in sql and "DO NOTHING" in sql for sql in inserts), (
        "persist_live_session INSERT lost ON CONFLICT DO NOTHING — a task "
        "retry can now duplicate per-test rows. Compiled SQL:\n"
        + "\n".join(inserts)
    )
