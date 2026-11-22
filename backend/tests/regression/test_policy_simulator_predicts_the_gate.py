"""The policy simulator predicts what publishing the policy will show (D-133, D-135).

Seen on the default install with a draft policy:

* For ``nightly-1854`` (70.3 %) the simulator said "No change: recommendation
  stays CONDITIONAL_GO"; once published, the gate read **No-Go**. The gate
  applies the policy's pass-rate bands and hard caps at read time, outside
  ``evaluate_policy``, and the simulator ran ``evaluate_policy`` only.
* For ``main-1865`` the Open Defect Limit rule reported "Open defects: 0 (limit:
  0)" while the gate page listed one open defect. The simulator read a snapshot
  key no writer sets.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.models.schemas import PolicyDocument
from app.routers import release_gate_policies as router
from app.services import metrics_service
from app.services.release_council_service import apply_policy_band_floor

DIMS = {d: 5.0 for d in (
    "user_impact", "env_sensitivity", "reproducibility", "regression_likely",
    "hist_recurrence", "blast_radius", "diagnosis_conf",
)}


def _document(*rules: dict) -> dict:
    doc = PolicyDocument().model_dump()
    doc["rules"] = list(rules)
    return doc


async def _simulate(decision, run, document):
    project_id = uuid.uuid4()
    results = iter([
        SimpleNamespace(scalar_one_or_none=lambda: project_id),
        SimpleNamespace(scalar_one_or_none=lambda: decision),
        SimpleNamespace(scalar_one_or_none=lambda: run),
    ])
    db = AsyncMock()
    db.execute = AsyncMock(side_effect=lambda *_a, **_k: next(results))
    payload = SimpleNamespace(
        run_id=uuid.uuid4(), policy_document=SimpleNamespace(model_dump=lambda: document)
    )
    with patch.object(router, "_enforce_api_key_project_binding", lambda *a, **k: None), \
            patch("app.core.deps.resolve_project_scope", AsyncMock()), \
            patch.object(metrics_service, "_resolve_active_policy_for_project", AsyncMock(return_value=None)), \
            patch.object(metrics_service, "count_open_critical_defects", AsyncMock(return_value=0)):
        return await router.simulate_policy(
            payload=payload, db=db, current_user=SimpleNamespace(id=uuid.uuid4())
        )


@pytest.mark.asyncio
async def test_a_draft_with_bands_predicts_no_go_below_the_orange_band():
    decision = SimpleNamespace(
        dimension_scores=DIMS, input_snapshot={"policy_context": {"open_defects": 0}},
        recommendation="CONDITIONAL_GO", risk_score=35.0, composite_risk=35.0,
    )
    run = SimpleNamespace(pass_rate=70.3, duration_findings=None)

    response = await _simulate(decision, run, _document())

    # No policy is active today, so the gate shows the stored verdict.
    assert response.original_recommendation == "CONDITIONAL_GO"
    # The draft's default bands put 70.3 % in red: publishing makes it No-Go.
    assert response.simulated_recommendation == "NO_GO"
    assert response.release_readiness_band == "red"
    assert "No change" not in response.diff_summary


@pytest.mark.asyncio
async def test_the_open_defect_rule_sees_the_frozen_count():
    decision = SimpleNamespace(
        dimension_scores=DIMS,
        input_snapshot={"policy_context": {"open_defects": 1, "flaky_count": 0}},
        recommendation="GO", risk_score=7.0, composite_risk=7.0,
    )
    run = SimpleNamespace(pass_rate=99.5, duration_findings=None)
    rule = {"id": "od", "name": "Open Defect Limit", "type": "open_defect_limit",
            "enabled": True, "params": {"max_open_defects": 0, "action": "BLOCK"}}

    response = await _simulate(decision, run, _document(rule))

    [evaluation] = response.rule_evaluations
    assert evaluation.passed is False
    assert evaluation.actual_value == 1
    assert response.simulated_recommendation == "NO_GO"


@pytest.mark.asyncio
async def test_the_gate_and_the_simulator_share_one_band_floor():
    with patch.object(metrics_service, "count_open_critical_defects", AsyncMock(return_value=0)):
        verdict, band, downgrades = await apply_policy_band_floor(
            None, uuid.uuid4(), "GO", 70.3,
            bands={"green_min": 99, "yellow_min": 95, "orange_min": 90}, hard_caps={},
        )
    assert (verdict, band, downgrades) == ("NO_GO", "red", [])

    source = (router.simulate_policy.__code__.co_names)
    assert "apply_policy_band_floor" in source
    assert "_apply_band_floor" in source
