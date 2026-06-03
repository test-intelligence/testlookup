"""Regression pin for the Phase AUTO perf fix (auto/perf-20260603-2120).

``test_management_service.recompute_plan_counts`` materialised every
TestPlanItem row and ran five Python passes (len + 4 conditional sums). It now
computes all counts in one aggregate query. The subtle bit: ``executed`` is
derived as ``total - count(status == 'not_run')`` rather than
``count(status != 'not_run')`` — ``execution_status`` is nullable and the
original ``status not in ('not_run',)`` counts a NULL as executed, which the
``!=`` form would NOT. This test pins the single query and the NULL semantics.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

pytestmark = pytest.mark.regression


class _Res:
    def __init__(self, row):
        self._row = row

    def one(self):
        return self._row


@pytest.mark.asyncio
async def test_recompute_plan_counts_one_query_and_null_counts_as_executed():
    from app.services import test_management_service as svc

    # 5 items: 2 not_run, 1 passed, 1 failed, 1 blocked → but note executed is
    # derived as total - not_run, so NULL-status rows (which the DB does NOT
    # count as not_run) land in "executed", matching the original Python
    # ``status not in ('not_run',)``. Here total=5, not_run=2 → executed=3.
    agg = SimpleNamespace(total=5, not_run=2, passed=1, failed=1, blocked=1)
    plan = SimpleNamespace(
        id=uuid.uuid4(), total_cases=0, executed_cases=0,
        passed_cases=0, failed_cases=0, blocked_cases=0,
    )
    db = SimpleNamespace(execute=AsyncMock(return_value=_Res(agg)))

    await svc.recompute_plan_counts(db, plan)

    assert db.execute.await_count == 1          # one aggregate, not fetch-all + 5 passes
    assert plan.total_cases == 5
    assert plan.executed_cases == 3             # total - not_run (NULL rows count as executed)
    assert plan.passed_cases == 1
    assert plan.failed_cases == 1
    assert plan.blocked_cases == 1


@pytest.mark.asyncio
async def test_recompute_plan_counts_empty_plan():
    from app.services import test_management_service as svc

    agg = SimpleNamespace(total=0, not_run=0, passed=0, failed=0, blocked=0)
    plan = SimpleNamespace(
        id=uuid.uuid4(), total_cases=-1, executed_cases=-1,
        passed_cases=-1, failed_cases=-1, blocked_cases=-1,
    )
    db = SimpleNamespace(execute=AsyncMock(return_value=_Res(agg)))

    await svc.recompute_plan_counts(db, plan)

    assert db.execute.await_count == 1
    assert plan.total_cases == 0
    assert plan.executed_cases == 0
    assert plan.passed_cases == 0
