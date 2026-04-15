"""
API integration tests for /api/v1/runs/{run_id}/decision-trail — Tier 0B.

Verifies tenant isolation, 404 on missing runs, and that the aggregated
response shape from ``decision_trail_service.build_trail`` is passed
through the router unchanged.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

pytest.importorskip("httpx")
pytest.importorskip("jose")
pytest.importorskip("asyncpg")

from app.models.postgres import UserRole  # noqa: E402
from tests.integration.conftest import fake_execute_result  # noqa: E402

pytestmark = pytest.mark.asyncio


async def test_decision_trail_requires_project_membership(
    client, auth_as, override_db, fake_db,
):
    """A non-admin without access to the run's project gets 403."""
    victim_project_id = uuid.uuid4()
    run_id = uuid.uuid4()

    # db.execute sequence inside the router:
    #   1. Look up the run's project_id.
    fake_db.set_execute_results([fake_execute_result(scalar=victim_project_id)])

    # Caller belongs to SOME OTHER project.
    auth_as(accessible_projects={uuid.uuid4()})

    resp = await client.get(f"/api/v1/runs/{run_id}/decision-trail")
    assert resp.status_code == 403


async def test_decision_trail_returns_404_for_unknown_run(
    client, auth_as, override_db, fake_db,
):
    auth_as(role=UserRole.ADMIN)
    fake_db.set_execute_results([fake_execute_result(scalar=None)])
    resp = await client.get(f"/api/v1/runs/{uuid.uuid4()}/decision-trail")
    assert resp.status_code == 404


async def test_decision_trail_happy_path_returns_aggregated_document(
    client, auth_as, override_db, fake_db,
):
    project_id = uuid.uuid4()
    run_id = uuid.uuid4()
    pipeline_run_id = uuid.uuid4()
    now = datetime.now(timezone.utc)

    auth_as(role=UserRole.ADMIN)
    # The router's initial project lookup.
    fake_db.set_execute_results([fake_execute_result(scalar=project_id)])

    trail = {
        "run_id": run_id,
        "pipeline_run_id": pipeline_run_id,
        "workflow_type": "offline",
        "pipeline_status": "completed",
        "started_at": now,
        "completed_at": now,
        "total_cost_usd": 0.0123,
        "total_tokens": 4200,
        "stages": [
            {
                "stage_name": "root_cause_analysis",
                "status": "completed",
                "started_at": now,
                "completed_at": now,
                "duration_seconds": 12.5,
                "analysis_mode": "llm",
                "fallback_used": False,
                "fallback_reason": None,
                "route_rationale": "configured ANALYSIS_MODE",
                "error_category": None,
                "skipped_reason": None,
                "execution_path": None,
                "confidence_score": 85,
                "evidence_count": 3,
                "input_tokens": 2000,
                "output_tokens": 2200,
                "cost_usd": 0.0123,
                "decision_log": [
                    {
                        "at": now.isoformat(),
                        "decision_point": "route_analysis_mode",
                        "chosen": "llm",
                        "rationale": "auto resolution picked LLM",
                    },
                ],
            }
        ],
        "workflow_events": [
            {
                "at": now.isoformat(),
                "decision_point": "route_after_ingestion",
                "chosen": "anomaly_detection",
                "rationale": "7 failed tests — fan out to analysis",
                "alternatives": None,
                "context": None,
            }
        ],
        "per_test": [
            {
                "test_case_id": uuid.uuid4(),
                "test_name": "test_login",
                "analysis_mode": "llm",
                "mode_requested": "auto",
                "fallback_from": None,
                "fallback_reason": None,
                "confidence_adjustments": [],
                "retry_count": 0,
                "duration_seconds": 0.5,
            }
        ],
        "mode_distribution": {"llm": 1},
        "fallback_count": 0,
    }

    with patch(
        "app.services.decision_trail_service.build_trail",
        AsyncMock(return_value=trail),
    ):
        resp = await client.get(f"/api/v1/runs/{run_id}/decision-trail")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["pipeline_status"] == "completed"
    assert body["mode_distribution"] == {"llm": 1}
    assert body["fallback_count"] == 0
    assert len(body["stages"]) == 1
    assert body["stages"][0]["decision_log"][0]["decision_point"] == "route_analysis_mode"
