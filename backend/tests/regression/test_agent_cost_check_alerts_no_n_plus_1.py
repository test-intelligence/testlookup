"""Regression pin for the Phase AUTO perf fix (auto/perf-20260603-0745).

``check_alerts`` issued one COUNT query per failed stage (N+1 — a round trip per
failed stage in the pipeline). It now batches them into a single GROUP BY query.
This test pins that the total DB round-trip count is constant regardless of the
number of failed stages, while the ``repeated_failure`` alert behaviour is
unchanged.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

pytestmark = pytest.mark.regression


def _stage(name, cost=0.1):
    now = None
    return SimpleNamespace(
        stage_name=name, status="failed", error="boom", error_category="infra",
        input_tokens=0, output_tokens=0, total_tokens=0, llm_calls_count=0,
        cost_usd=cost, started_at=now, completed_at=now,
        confidence_score=None, evidence_count=0, route_rationale=None,
        fallback_used=False, fallback_reason=None,
    )


class _Result:
    def __init__(self, *, scalars_all=None, all_=None, scalar=None):
        self._scalars_all = scalars_all or []
        self._all = all_ or []
        self._scalar = scalar

    def scalars(self):
        return SimpleNamespace(all=lambda: list(self._scalars_all))

    def all(self):
        return list(self._all)

    def scalar(self):
        return self._scalar


@pytest.mark.asyncio
async def test_check_alerts_batches_failure_counts_into_one_query():
    from app.services import agent_cost_service as svc

    # 3 failed stages across 2 distinct stage names — the old code would have
    # fired 3 COUNT queries; the new code fires exactly one GROUP BY.
    failed = [_stage("analysis"), _stage("triage"), _stage("analysis")]

    # execute() call order in check_alerts:
    #   1. failed-stage SELECT  -> scalars().all()
    #   2. grouped recent-counts GROUP BY -> .all()  (THE batched query)
    #   3. get_pipeline_cost_summary SELECT -> scalars().all()
    #   4. avg-pipeline-cost     -> .scalar()
    results = [
        _Result(scalars_all=failed),
        _Result(all_=[("analysis", 5), ("triage", 1)]),  # analysis ≥ 3 → alert
        _Result(scalars_all=failed),                       # cost summary stages
        _Result(scalar=0.0),                               # avg pipeline cost
    ]
    db = SimpleNamespace(execute=AsyncMock(side_effect=results))

    alerts = await svc.check_alerts(db, str(uuid.uuid4()))

    # Exactly 4 round trips — constant, NOT 1 + N(=3) + 2 = 6 as before.
    assert db.execute.await_count == 4

    repeated = [a for a in alerts if a["type"] == "repeated_failure"]
    # 'analysis' has 5 ≥ ALERT_CONSECUTIVE_FAILURES(3); it appears twice in
    # failed_stages so the per-row loop emits it for each occurrence (prior
    # behaviour preserved). 'triage' (1) is below threshold → no alert.
    assert len(repeated) == 2
    assert all(a["detail"]["stage_name"] == "analysis" for a in repeated)
    assert all(a["detail"]["failure_count"] == 5 for a in repeated)


@pytest.mark.asyncio
async def test_check_alerts_no_failed_stages_skips_count_query():
    from app.services import agent_cost_service as svc

    results = [
        _Result(scalars_all=[]),        # no failed stages
        _Result(scalars_all=[]),        # cost summary stages
        _Result(scalar=0.0),            # avg pipeline cost
    ]
    db = SimpleNamespace(execute=AsyncMock(side_effect=results))

    alerts = await svc.check_alerts(db, str(uuid.uuid4()))

    # No failed stages → the grouped count query is skipped entirely (3 calls).
    assert db.execute.await_count == 3
    assert not [a for a in alerts if a["type"] == "repeated_failure"]
