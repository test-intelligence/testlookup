"""Regression pins for the agent_cost_service review (review/agent-cost-service).

Two bugs were fixed:

1. ``check_alerts`` cost-spike used the 7-day average of individual *stage*
   costs as its baseline, then compared a pipeline's *total* cost against
   ``baseline * 3``. Any multi-stage pipeline cleared that bar trivially and
   fired a spurious ``cost_spike`` alert. The baseline is now the 7-day average
   of per-PIPELINE totals (``GROUP BY pipeline_run_id``).

2. The ``/api/v1/agents/pipelines/{id}/*`` read endpoints (get, stages,
   timeline, replay) and ``list_pipelines`` only required an authenticated
   user — never project access — so any tenant could read another tenant's
   pipeline status, per-stage cost, decision timeline, and replay. Access is
   now derived from the owning test run's project (the ``/runs/{id}/*``
   siblings already did this via ``require_run_access``).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.regression


# ── Bug 1: cost-spike baseline must be per-PIPELINE, not per-stage ──────────────

def test_avg_cost_stmt_groups_by_pipeline_not_stage():
    """The baseline statement must average per-pipeline totals.

    Pins the fix against a revert to ``avg(cost_usd)`` over raw stage rows: the
    compiled SQL has to GROUP BY the pipeline id and SUM the stage costs inside.
    """
    from app.services.agent_cost_service import _build_avg_pipeline_cost_stmt

    sql = str(_build_avg_pipeline_cost_stmt()).lower()
    assert "group by" in sql
    assert "pipeline_run_id" in sql
    assert "sum(" in sql  # per-pipeline total
    assert "avg(" in sql  # averaged across pipelines


def _stage(cost: float):
    now = datetime.now(timezone.utc)
    return SimpleNamespace(
        stage_name="s", status="completed",
        input_tokens=10, output_tokens=10, total_tokens=20,
        cost_usd=cost, llm_calls_count=1,
        started_at=now, completed_at=now,
        error_category=None, fallback_used=False,
        confidence_score=90, evidence_count=1, route_rationale=None,
    )


class _Result:
    def __init__(self, *, scalars_all=None, scalar=None):
        self._scalars_all = scalars_all or []
        self._scalar = scalar

    def scalars(self):
        return SimpleNamespace(all=lambda: list(self._scalars_all))

    def scalar(self):
        return self._scalar


def _fake_db(stages, avg_cost):
    """execute() order in check_alerts: failed-stages → cost-summary → avg."""
    from unittest.mock import AsyncMock

    db = SimpleNamespace()
    db.execute = AsyncMock(side_effect=[
        _Result(scalars_all=[]),            # 1. failed-stage query → none failed
        _Result(scalars_all=stages),        # 2. get_pipeline_cost_summary stages
        _Result(scalar=avg_cost),           # 3. avg-pipeline-cost baseline
    ])
    return db


@pytest.mark.asyncio
async def test_no_cost_spike_when_total_within_factor_of_pipeline_avg():
    """A 9-stage pipeline at 2.25x the per-pipeline avg must NOT alert.

    9 * 0.5 = 4.5 total; baseline 2.0; 4.5 < 2.0 * 3.0. Under the old per-stage
    baseline (~0.5) this would have been 9x and fired.
    """
    from app.services import agent_cost_service as svc

    stages = [_stage(0.5) for _ in range(9)]  # total 4.5 (< 5.0 budget too)
    db = _fake_db(stages, avg_cost=2.0)

    alerts = await svc.check_alerts(db, str(uuid.uuid4()))
    assert not [a for a in alerts if a["type"] == "cost_spike"]


@pytest.mark.asyncio
async def test_cost_spike_fires_above_factor():
    """Total >= baseline * factor still fires (3.5x here)."""
    from app.services import agent_cost_service as svc

    stages = [_stage(0.5) for _ in range(7)]  # total 3.5
    db = _fake_db(stages, avg_cost=1.0)        # 3.5 > 1.0 * 3.0

    alerts = await svc.check_alerts(db, str(uuid.uuid4()))
    spikes = [a for a in alerts if a["type"] == "cost_spike"]
    assert len(spikes) == 1


# ── Bug 2: pipeline read endpoints enforce project access ───────────────────────

@pytest.mark.asyncio
async def test_require_pipeline_access_forbids_foreign_project():
    """Non-admin without the run's project in their accessible set → 403."""
    from unittest.mock import AsyncMock, patch

    from fastapi import HTTPException

    from app.routers import agents as agents_router

    foreign_project = uuid.uuid4()
    pipeline = SimpleNamespace(id=uuid.uuid4(), test_run_id=uuid.uuid4())
    run = SimpleNamespace(id=pipeline.test_run_id, project_id=foreign_project)

    db = SimpleNamespace()
    db.get = AsyncMock(return_value=run)

    with patch.object(
        agents_router, "get_accessible_project_ids",
        AsyncMock(return_value={uuid.uuid4()}),  # some OTHER project, not foreign_project
    ):
        with pytest.raises(HTTPException) as ei:
            await agents_router._require_pipeline_access(db, SimpleNamespace(), pipeline)
    assert ei.value.status_code == 403


@pytest.mark.asyncio
async def test_require_pipeline_access_allows_member_and_admin():
    from unittest.mock import AsyncMock, patch

    from app.routers import agents as agents_router

    project = uuid.uuid4()
    pipeline = SimpleNamespace(id=uuid.uuid4(), test_run_id=uuid.uuid4())
    run = SimpleNamespace(id=pipeline.test_run_id, project_id=project)
    db = SimpleNamespace(get=AsyncMock(return_value=run))

    # member: project in accessible set → no raise
    with patch.object(
        agents_router, "get_accessible_project_ids",
        AsyncMock(return_value={project}),
    ):
        await agents_router._require_pipeline_access(db, SimpleNamespace(), pipeline)

    # admin: accessible is None → no raise, no run lookup needed
    db_admin = SimpleNamespace(get=AsyncMock(return_value=None))
    with patch.object(
        agents_router, "get_accessible_project_ids",
        AsyncMock(return_value=None),
    ):
        await agents_router._require_pipeline_access(db_admin, SimpleNamespace(), pipeline)
