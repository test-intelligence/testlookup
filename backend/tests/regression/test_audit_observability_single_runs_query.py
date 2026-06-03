"""Regression pin for the Phase AUTO perf fix (auto/perf-20260603-2018).

``audit_dashboard_service.get_tenant_observability`` ran a separate COUNT for
failed runs in addition to the total/avg/sum aggregate over the same
(project_id, created_at >= cutoff) window. The FAILED count is now a conditional
aggregate (`count(...).filter(status == 'FAILED')`) in that same query, dropping
the function from 5 DB round trips to 4 with identical output.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

pytestmark = pytest.mark.regression


class _Res:
    def __init__(self, *, one=None, scalar=None):
        self._one = one
        self._scalar = scalar

    def one_or_none(self):
        return self._one

    def scalar(self):
        return self._scalar


@pytest.mark.asyncio
async def test_get_tenant_observability_merges_failed_count_into_runs_query():
    from app.services import audit_dashboard_service as svc

    run_row = SimpleNamespace(
        total_runs=10, avg_pass_rate=92.34, total_tests=500, failed_runs=2
    )
    # execute() order: 1 runs(+failed) aggregate, 2 ai, 3 release, 4 audit
    db = SimpleNamespace(execute=AsyncMock(side_effect=[
        _Res(one=run_row),
        _Res(scalar=7),
        _Res(scalar=3),
        _Res(scalar=15),
    ]))

    out = await svc.get_tenant_observability(db, uuid.uuid4(), days=7)

    assert db.execute.await_count == 4  # was 5 (separate failed-runs COUNT removed)
    assert out["total_runs"] == 10
    assert out["total_tests"] == 500
    assert out["avg_pass_rate"] == 92.3      # round(92.34, 1)
    assert out["failed_runs"] == 2           # from the merged conditional count
    assert out["ai_analyses_count"] == 7
    assert out["release_decisions_count"] == 3
    assert out["audit_events_count"] == 15
    assert out["period_days"] == 7


@pytest.mark.asyncio
async def test_get_tenant_observability_empty_window():
    from app.services import audit_dashboard_service as svc

    empty_row = SimpleNamespace(
        total_runs=0, avg_pass_rate=None, total_tests=None, failed_runs=0
    )
    db = SimpleNamespace(execute=AsyncMock(side_effect=[
        _Res(one=empty_row),
        _Res(scalar=0),
        _Res(scalar=0),
        _Res(scalar=0),
    ]))

    out = await svc.get_tenant_observability(db, uuid.uuid4(), days=30)

    assert db.execute.await_count == 4
    assert out["total_runs"] == 0
    assert out["failed_runs"] == 0
    assert out["avg_pass_rate"] is None
