"""Regression pin for the Phase AUTO perf fix (auto/perf-20260603-1531).

get_baseline_diff issued TWO SELECTs against test_cases with identical WHERE
clauses for the baseline run's failures — one projecting test_fingerprint (for
new-failure detection), one projecting fingerprint+name (for resolved_failures).
The fix fetches both columns once and reuses the rows, cutting one round trip.
Output (new_failures, resolved_failures) is unchanged.

This test drives get_baseline_diff with an ordered mock and asserts the injected
session is hit exactly 6 times (was 7 before the fix) while the computed
new_failures / resolved_failures stay correct.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

pytestmark = pytest.mark.regression


class _Result:
    def __init__(self, *, scalar=None, all_=None):
        self._scalar = scalar
        self._all = all_ or []

    def scalar_one_or_none(self):
        return self._scalar

    def all(self):
        return list(self._all)


def _row(**kw):
    return SimpleNamespace(**kw)


@pytest.mark.asyncio
async def test_get_baseline_diff_fetches_baseline_failures_once():
    from app.services import run_diff_service as svc

    run = SimpleNamespace(
        id=uuid.uuid4(), project_id=uuid.uuid4(), created_at="2026-06-03T00:00:00",
        pass_rate=80.0, build_number="b2", commit_hash=None, branch="main",
        ocp_namespace=None,
    )
    baseline = SimpleNamespace(
        id=uuid.uuid4(), pass_rate=100.0, build_number="b1", commit_hash=None,
        branch="main", ocp_namespace=None,
    )

    # current failures: c1 (new) + shared (also failed in baseline)
    current_failed = [
        _row(id=uuid.uuid4(), test_fingerprint="c1", test_name="test_c1"),
        _row(id=uuid.uuid4(), test_fingerprint="shared", test_name="test_shared_cur"),
    ]
    # baseline failures: shared (still failing) + resolved (now passing)
    baseline_failed = [
        _row(test_fingerprint="shared", test_name="test_shared_base"),
        _row(test_fingerprint="resolved", test_name="test_resolved"),
    ]
    current_passing = [_row(test_fingerprint="resolved")]

    # Ordered execute results on the INJECTED db:
    #   1 baseline lookup, 2 current-failures, 3 baseline-failures(ONCE),
    #   4 current-passing, 5 current-suites, 6 baseline-suites
    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[
        _Result(scalar=baseline),
        _Result(all_=current_failed),
        _Result(all_=baseline_failed),
        _Result(all_=current_passing),
        _Result(all_=[_row(suite_name="S")]),
        _Result(all_=[_row(suite_name="S")]),
    ])

    with patch.object(svc, "_classify_failure_clusters", AsyncMock(return_value=([], {}))), \
         patch("app.db.postgres.AsyncSessionLocal", side_effect=RuntimeError("skip persist")):
        result = await svc.get_baseline_diff(run, db)

    # ONE baseline-failures fetch, not two → 6 round trips total (was 7).
    assert db.execute.await_count == 6
    # Output unchanged: c1 is new; resolved is resolved.
    assert result["new_failures"] == ["test_c1"]
    assert result["resolved_failures"] == ["test_resolved"]


@pytest.mark.asyncio
async def test_get_baseline_diff_returns_none_without_baseline():
    from app.services import run_diff_service as svc

    run = SimpleNamespace(
        id=uuid.uuid4(), project_id=uuid.uuid4(), created_at="2026-06-03T00:00:00",
        pass_rate=80.0,
    )
    db = AsyncMock()
    db.execute = AsyncMock(return_value=_Result(scalar=None))  # no prior PASSED run

    assert await svc.get_baseline_diff(run, db) is None
    assert db.execute.await_count == 1  # bails after the baseline lookup
