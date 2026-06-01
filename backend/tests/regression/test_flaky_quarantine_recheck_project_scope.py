"""Regression: flaky-quarantine recheck cycle ignored project scope, and
expire_stale_proposals audited before committing.

Bugs pinned (review/flaky-quarantine-service, 2026-06-01):

1. ``run_recheck_cycle`` computed the post-quarantine flip-rate with
   ``WHERE test_cases.test_fingerprint == row.test_fingerprint`` and NO
   project filter. ``make_test_fingerprint`` is ``sha256(class::name)`` with
   no project salt, so two projects with a same-named test share a
   fingerprint — the count blended other tenants' executions and could
   RELEASE a still-flaky test (or RE_QUARANTINE a stable one). Fix: join
   ``TestRun`` and filter ``TestRun.project_id == row.project_id``.

2. ``expire_stale_proposals`` called ``_audit`` (own-session, auto-committed)
   inside the loop, before the batch ``db.commit()`` — so the audit log could
   record expirations that rolled back. Fix: audit after the commit.

The maintenance functions open their own ``AsyncSessionLocal``, so the tests
patch it with a fake session and assert on the compiled SQL / call order.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

pytest.importorskip("sqlalchemy")

from app.services import flaky_quarantine_service as svc  # noqa: E402
from app.models.postgres import FlakyQuarantineStatus  # noqa: E402
from app.models.postgres import TestStatus as _TestStatus  # noqa: E402  (aliased so pytest doesn't try to collect it)


class _ScalarsResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return SimpleNamespace(all=lambda: list(self._rows))

    def all(self):
        return list(self._rows)


class _FakeCM:
    """Async context manager standing in for ``AsyncSessionLocal()``."""

    def __init__(self, db):
        self._db = db

    async def __aenter__(self):
        return self._db

    async def __aexit__(self, *exc):
        return False


@pytest.mark.asyncio
async def test_recheck_cycle_count_query_is_project_scoped():
    project_id = uuid.uuid4()
    row = SimpleNamespace(
        id=uuid.uuid4(),
        project_id=project_id,
        test_fingerprint="deadbeefdeadbeef",
        status=FlakyQuarantineStatus.RECHECK_SCHEDULED.value,
        quarantine_start=svc.datetime.now(svc.timezone.utc) - svc.timedelta(days=14),
        quarantine_duration_days=14,
        quarantine_expires_at=None,
        recheck_at=None,
        flip_rate=None,
        flip_window_size=None,
        pass_count=None,
        fail_count=None,
        updated_at=None,
    )

    counts_rows = [
        SimpleNamespace(status=_TestStatus.PASSED.value, c=20),
        SimpleNamespace(status=_TestStatus.FAILED.value, c=1),
    ]
    captured = {}

    class _FakeDB:
        def __init__(self):
            self.n = 0

        async def execute(self, stmt):
            self.n += 1
            if self.n == 1:
                return _ScalarsResult([row])
            # The per-row count query — capture its compiled SQL.
            captured["sql"] = str(stmt)
            return _ScalarsResult(counts_rows)

        async def commit(self):
            pass

    with patch.object(svc, "_feature_enabled", AsyncMock(return_value=True)), \
         patch.object(svc, "AsyncSessionLocal", lambda: _FakeCM(_FakeDB())):
        result = await svc.run_recheck_cycle()

    sql = captured.get("sql", "")
    # The fix: the count must join test_runs and filter by project.
    assert "test_runs" in sql, f"recheck count query must join test_runs; got:\n{sql}"
    assert "test_runs.project_id" in sql, (
        f"recheck count query must filter by the row's project_id; got:\n{sql}"
    )
    # 20 pass / 1 fail → flip 0.048 < 0.10 → released (sanity that it ran).
    assert result["released"] == 1
    assert row.status == FlakyQuarantineStatus.RELEASED.value


@pytest.mark.asyncio
async def test_expire_stale_proposals_audits_after_commit():
    def _proposed_row():
        return SimpleNamespace(
            id=uuid.uuid4(),
            project_id=uuid.uuid4(),
            status=FlakyQuarantineStatus.PROPOSED.value,
            flip_rate=0.5,
            flip_window_size=10,
            quarantine_start=None,
            quarantine_expires_at=None,
            approved_by_user_id=None,
            rejected_by_user_id=None,
            updated_at=None,
        )

    rows = [_proposed_row(), _proposed_row()]
    order: list[str] = []

    class _FakeDB:
        async def execute(self, stmt):
            return _ScalarsResult(rows)

        async def commit(self):
            order.append("commit")

    async def _record_audit(*args, **kwargs):
        order.append("audit")

    with patch.object(svc, "_feature_enabled", AsyncMock(return_value=True)), \
         patch.object(svc, "AsyncSessionLocal", lambda: _FakeCM(_FakeDB())), \
         patch.object(svc, "_audit", AsyncMock(side_effect=_record_audit)):
        expired = await svc.expire_stale_proposals()

    assert expired == 2
    # Commit happens before any audit (audit rows must not outlive a rollback).
    assert order == ["commit", "audit", "audit"], order
    assert all(r.status == FlakyQuarantineStatus.EXPIRED.value for r in rows)
