from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from app.services.policy_evaluator_service import (
    POLICY_EVALUATOR_VERSION,
    evaluate_policy,
)
from app.services.release_policy_replay import replay_frozen_release_policy
from app.services.release_policy_replay import compare_persisted_release_record


async def _snapshot(*, pass_rate=95.0, recommendation=None):
    document = {
        "thresholds": {
            "go_threshold": 20.0,
            "no_go_threshold": 55.0,
            "pass_rate_minimum": 90.0,
            "pass_rate_hard_floor_factor": 0.7,
        },
        "dimension_weights": {
            "user_impact": 1.0,
            "env_sensitivity": 0.0,
            "reproducibility": 0.0,
            "regression_likely": 0.0,
            "hist_recurrence": 0.0,
            "blast_radius": 0.0,
            "diagnosis_conf": 0.0,
        },
        "rules": [],
    }
    dimensions = {key: 0.0 for key in document["dimension_weights"]}
    frozen = SimpleNamespace(id=None, version=None, rules=document)
    expected = await evaluate_policy(
        project_id=None,
        dim_scores=dimensions,
        pass_rate=pass_rate,
        context={},
        db=None,  # type: ignore[arg-type]
        policy_override=frozen,
    )
    expected.policy_snapshot = {
        "schema_version": 1,
        "policy_id": None,
        "policy_version": None,
        "policy_level": "hardcoded",
        "document": document,
    }
    expected.evaluator_version = POLICY_EVALUATOR_VERSION
    expected_dict = expected.to_dict()
    return {
        "project_id": str(uuid.uuid4()),
        "release_policy_inputs": {
            "recommendation": recommendation or expected.recommendation,
            "risk_score": int(expected.effective_composite),
            "composite_risk": expected.effective_composite,
            "score_model_version": 1,
            "dimension_scores": dimensions,
            "policy_evaluation": expected_dict,
            "input_snapshot": {
                "pass_rate": pass_rate,
                "score_model_version": 1,
                "dimension_scores": dimensions,
                "policy_context": {},
                "policy_snapshot": expected.policy_snapshot,
                "policy_evaluator_version": POLICY_EVALUATOR_VERSION,
            },
        },
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("pass_rate", [95.0, 0.0])
async def test_frozen_policy_replay_matches_zero_and_normal_inputs(pass_rate):
    result = await replay_frozen_release_policy(await _snapshot(pass_rate=pass_rate))
    assert result["status"] == "passed"


@pytest.mark.asyncio
async def test_real_synthetic_hardcoded_policy_snapshot_replays_without_string_none_id():
    document = {
        "thresholds": {
            "go_threshold": 20.0,
            "no_go_threshold": 55.0,
            "pass_rate_minimum": 90.0,
            "pass_rate_hard_floor_factor": 0.7,
        },
        "dimension_weights": {"user_impact": 1.0},
        "rules": [],
    }
    expected = await evaluate_policy(
        project_id=None,
        dim_scores={"user_impact": 10.0},
        pass_rate=95.0,
        context={},
        db=None,  # type: ignore[arg-type]
        policy_override=SimpleNamespace(id=None, version=None, rules=document),
    )
    expected.policy_level = "hardcoded"
    payload = expected.to_dict()
    assert payload["policy_id"] is None
    assert payload["policy_snapshot"]["policy_id"] is None
    snapshot = {
        "project_id": str(uuid.uuid4()),
        "release_policy_inputs": {
            "recommendation": expected.recommendation,
            "risk_score": int(expected.effective_composite),
            "composite_risk": expected.effective_composite,
            "score_model_version": 1,
            "dimension_scores": {"user_impact": 10.0},
            "policy_evaluation": payload,
            "input_snapshot": {
                "pass_rate": 95.0,
                "score_model_version": 1,
                "dimension_scores": {"user_impact": 10.0},
                "policy_context": {},
                "policy_snapshot": payload["policy_snapshot"],
                "policy_evaluator_version": POLICY_EVALUATOR_VERSION,
            },
        },
    }
    assert (await replay_frozen_release_policy(snapshot))["status"] == "passed"


@pytest.mark.asyncio
async def test_replay_detects_tampered_release_recommendation():
    result = await replay_frozen_release_policy(
        await _snapshot(pass_rate=95.0, recommendation="NO_GO")
    )
    assert result["status"] == "failed"
    failed = {item["name"] for item in result["checks"] if item["status"] == "fail"}
    assert "release_recommendation_match" in failed


@pytest.mark.asyncio
async def test_replay_fails_closed_on_evaluator_version_drift():
    snapshot = await _snapshot()
    snapshot["release_policy_inputs"]["input_snapshot"]["policy_evaluator_version"] = "future"
    result = await replay_frozen_release_policy(snapshot)
    assert result["status"] == "failed"
    assert result["replayed_result"] is None


class _ScalarResult:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class _Session:
    def __init__(self, record):
        self.record = record

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def execute(self, _statement):
        return _ScalarResult(self.record)


def _record(**overrides):
    values = {
        "recommendation": "GO",
        "risk_score": 0,
        "composite_risk": 0.0,
        "dimension_scores": {"user_impact": 0.0},
        "policy_id": None,
        "pipeline_run_id": None,
        "score_model_version": None,
        "input_snapshot": {},
        "policy_evaluation": {},
        "human_override": None,
        "overridden_by": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _record_snapshot():
    return {
        "project_id": str(uuid.uuid4()),
        "test_run_id": str(uuid.uuid4()),
        "pipeline_run_id": str(uuid.uuid4()),
        "release_policy_inputs": {
            "recommendation": "GO",
            "risk_score": 0,
            "composite_risk": 0.0,
            "dimension_scores": {"user_impact": 0.0},
            "policy_id": None,
        },
    }


@pytest.mark.asyncio
async def test_persisted_release_record_match_and_supersession(monkeypatch):
    snapshot = _record_snapshot()
    monkeypatch.setattr(
        "app.services.release_policy_replay.AsyncSessionLocal",
        lambda: _Session(_record(pipeline_run_id=uuid.UUID(snapshot["pipeline_run_id"]))),
    )
    assert (await compare_persisted_release_record(snapshot))["status"] == "passed"

    monkeypatch.setattr(
        "app.services.release_policy_replay.AsyncSessionLocal",
        lambda: _Session(_record(
            pipeline_run_id=uuid.UUID(snapshot["pipeline_run_id"]),
            recommendation="NO_GO",
        )),
    )
    result = await compare_persisted_release_record(snapshot)
    assert result["status"] == "failed"
    assert result["reason"] == "release_record_superseded_or_mutated"
    assert result["mismatched_fields"] == ["recommendation"]


@pytest.mark.asyncio
async def test_persisted_release_human_override_is_distinct(monkeypatch):
    snapshot = _record_snapshot()
    monkeypatch.setattr(
        "app.services.release_policy_replay.AsyncSessionLocal",
        lambda: _Session(_record(
            pipeline_run_id=uuid.UUID(snapshot["pipeline_run_id"]),
            human_override="QA approved",
        )),
    )
    result = await compare_persisted_release_record(snapshot)
    assert result == {
        "status": "failed",
        "reason": "release_record_human_override",
        "human_override": True,
    }
