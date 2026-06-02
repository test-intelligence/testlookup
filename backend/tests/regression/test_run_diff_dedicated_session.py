"""Regression: get_baseline_diff must not commit/poison the injected session.

Bug pinned (review/run-diff-service, 2026-06-02):

``get_baseline_diff`` persisted its RunBaseline/RunDiff cache by calling
``db.add(...)`` + ``await db.commit()`` on the INJECTED session, wrapped in a
broad ``except`` that swallowed a commit failure WITHOUT rollback. But the
service is consumed by GET aggregators that keep using the same session for
further reads — ``run_intelligence_service.get_run_intelligence`` reads defect
candidates + stage results in steps 7-8 after calling it. So a premature commit
ended the caller's transaction mid-aggregation, and a swallowed commit failure
left the session rollback-required → the rest of /intelligence 500'd.

Fix: write the cache through a dedicated ``AsyncSessionLocal`` (CQS split) and
keep the injected session strictly read-only.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytest.importorskip("sqlalchemy")

from app.services import run_diff_service as svc  # noqa: E402


class _R:
    def __init__(self, scalar=None, rows=None, scalars_rows=None):
        self._scalar = scalar
        self._rows = rows or []
        self._scalars_rows = scalars_rows or []

    def scalar_one_or_none(self):
        return self._scalar

    def all(self):
        return self._rows

    def scalars(self):
        m = MagicMock()
        m.all = MagicMock(return_value=self._scalars_rows)
        return m


class _SeqDB:
    """Async session returning canned results in order; records add/commit."""

    def __init__(self, results):
        self._results = list(results)
        self.add = MagicMock()
        self.commit = AsyncMock()
        self.rollback = AsyncMock()

    async def execute(self, *a, **k):
        return self._results.pop(0)


class _WriteDB(_SeqDB):
    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


@pytest.mark.asyncio
async def test_get_baseline_diff_keeps_injected_session_read_only():
    run = SimpleNamespace(
        id=uuid.uuid4(), project_id=uuid.uuid4(),
        created_at="2026-06-02T00:00:00Z", pass_rate=80.0,
        commit_hash=None, branch="main", ocp_namespace=None,
    )
    baseline = SimpleNamespace(
        id=uuid.uuid4(), pass_rate=95.0, build_number="100",
        commit_hash=None, branch="main", ocp_namespace=None,
    )

    fp_row = SimpleNamespace(
        id=uuid.uuid4(), test_fingerprint="fp1", test_name="test_a"
    )
    # Injected-session read sequence (8 executes): baseline, current_failed,
    # baseline_failed, current_passing, baseline_failed_names, clusters,
    # current_suites, baseline_suites.
    injected = _SeqDB([
        _R(scalar=baseline),                       # baseline lookup
        _R(rows=[fp_row]),                         # current failures
        _R(rows=[]),                               # baseline failures (fp1 is new)
        _R(rows=[]),                               # current passing
        _R(rows=[]),                               # baseline failed names
        _R(scalars_rows=[]),                       # failure clusters (none)
        _R(rows=[SimpleNamespace(suite_name="s1")]),  # current suites
        _R(rows=[]),                               # baseline suites
    ])

    write_db = _WriteDB([_R(scalar=None), _R(scalar=None)])  # no existing baseline/diff

    with patch("app.db.postgres.AsyncSessionLocal", lambda: write_db):
        result = await svc.get_baseline_diff(run, injected, flaky_count=0)

    # The diff was computed and returned.
    assert result is not None
    assert result["new_failures"] == ["test_a"]
    assert result["baseline_run_id"] == str(baseline.id)

    # Injected session was READ-ONLY — never added to or committed.
    injected.add.assert_not_called()
    injected.commit.assert_not_awaited()
    injected.rollback.assert_not_awaited()

    # The cache was persisted on the dedicated write session instead.
    assert write_db.add.call_count == 2  # RunBaseline + RunDiff
    write_db.commit.assert_awaited_once()
