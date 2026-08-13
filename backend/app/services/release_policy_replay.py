"""Independent replay of a frozen release-policy decision snapshot."""
from __future__ import annotations

import math
import uuid
from types import SimpleNamespace
from typing import Any

from sqlalchemy import select

from app.db.postgres import AsyncSessionLocal
from app.models.postgres import ReleaseDecision, TestRun
from app.services.criticality_service import SCORE_MODEL_VERSION
from app.services.policy_evaluator_service import (
    POLICY_EVALUATOR_VERSION,
    evaluate_policy,
)

REPLAY_SCHEMA_VERSION = 1


def _check(name: str, passed: bool, detail: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"name": name, "status": "pass" if passed else "fail", "detail": detail or {}}


def _semantic_policy_result(value: dict[str, Any]) -> dict[str, Any]:
    rules = []
    for raw in value.get("rule_evaluations") or []:
        if not isinstance(raw, dict):
            continue
        rules.append({
            key: raw.get(key)
            for key in (
                "rule_id",
                "rule_type",
                "passed",
                "action",
                "actual_value",
                "threshold_value",
            )
        })
    return {
        "policy_id": value.get("policy_id"),
        "policy_version": value.get("policy_version"),
        "overall_result": value.get("overall_result"),
        "recommendation": value.get("recommendation"),
        "effective_composite": value.get("effective_composite"),
        "effective_thresholds": value.get("effective_thresholds") or {},
        "effective_weights": value.get("effective_weights") or {},
        "rule_evaluations": rules,
        "kind_breakdown": value.get("kind_breakdown"),
        "kind_rule_applied": bool(value.get("kind_rule_applied")),
        "kind_counterfactual": value.get("kind_counterfactual"),
    }


async def replay_frozen_release_policy(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Replay only frozen inputs and policy material; never query active policy state."""
    release = snapshot.get("release_policy_inputs")
    release = release if isinstance(release, dict) else {}
    input_snapshot = release.get("input_snapshot")
    input_snapshot = input_snapshot if isinstance(input_snapshot, dict) else {}
    expected = release.get("policy_evaluation")
    expected = expected if isinstance(expected, dict) else {}
    policy_snapshot = input_snapshot.get("policy_snapshot") or expected.get("policy_snapshot")
    policy_snapshot = policy_snapshot if isinstance(policy_snapshot, dict) else {}
    document = policy_snapshot.get("document")
    document = document if isinstance(document, dict) else {}
    recorded_version = (
        input_snapshot.get("policy_evaluator_version") or expected.get("evaluator_version")
    )
    checks = [
        _check("policy_snapshot_present", bool(document)),
        _check(
            "policy_evaluator_version_match",
            recorded_version == POLICY_EVALUATOR_VERSION,
            {"recorded": recorded_version, "runtime": POLICY_EVALUATOR_VERSION},
        ),
        _check("policy_inputs_present", bool(input_snapshot.get("dimension_scores"))),
        _check("policy_context_present", isinstance(input_snapshot.get("policy_context"), dict)),
        _check("policy_pass_rate_present", "pass_rate" in input_snapshot),
        _check(
            "score_model_version_match",
            release.get("score_model_version") == SCORE_MODEL_VERSION
            and input_snapshot.get("score_model_version") == SCORE_MODEL_VERSION,
            {
                "release": release.get("score_model_version"),
                "input": input_snapshot.get("score_model_version"),
                "runtime": SCORE_MODEL_VERSION,
            },
        ),
        _check(
            "frozen_dimensions_match",
            (release.get("dimension_scores") or {})
            == (input_snapshot.get("dimension_scores") or {}),
        ),
        _check(
            "frozen_policy_snapshot_match",
            policy_snapshot == (expected.get("policy_snapshot") or {}),
        ),
    ]
    if any(item["status"] == "fail" for item in checks):
        return {
            "schema_version": REPLAY_SCHEMA_VERSION,
            "status": "failed",
            "checks": checks,
            "replayed_result": None,
        }

    frozen_policy = SimpleNamespace(
        id=policy_snapshot.get("policy_id"),
        version=policy_snapshot.get("policy_version"),
        rules=document,
    )
    replayed = await evaluate_policy(
        project_id=snapshot.get("project_id"),
        dim_scores=input_snapshot["dimension_scores"],
        pass_rate=float(input_snapshot.get("pass_rate", 0.0)),
        context=input_snapshot.get("policy_context") or {},
        db=None,  # type: ignore[arg-type] -- policy_override prevents DB access
        policy_override=frozen_policy,
    )
    replayed_dict = replayed.to_dict()
    expected_semantic = _semantic_policy_result(expected)
    replayed_semantic = _semantic_policy_result(replayed_dict)
    checks.extend([
        _check(
            "policy_semantic_result_match",
            expected_semantic == replayed_semantic,
            {"expected": expected_semantic, "replayed": replayed_semantic},
        ),
        _check(
            "release_recommendation_match",
            release.get("recommendation") == replayed.recommendation,
            {
                "persisted": release.get("recommendation"),
                "replayed": replayed.recommendation,
            },
        ),
        _check(
            "release_composite_match",
            release.get("composite_risk") is not None
            and math.isclose(
                float(release["composite_risk"]),
                float(replayed.effective_composite),
                rel_tol=1e-9,
                abs_tol=1e-9,
            ),
        ),
        _check(
            "release_risk_score_match",
            release.get("risk_score") == int(replayed.effective_composite),
        ),
    ])
    return {
        "schema_version": REPLAY_SCHEMA_VERSION,
        "status": "passed" if all(item["status"] == "pass" for item in checks) else "failed",
        "checks": checks,
        "replayed_result": replayed_semantic,
    }


async def compare_persisted_release_record(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Detect missing, human-overridden, or superseded singleton release records."""
    release = snapshot.get("release_policy_inputs")
    release = release if isinstance(release, dict) else {}
    try:
        run_id = uuid.UUID(str(snapshot.get("test_run_id")))
        project_id = uuid.UUID(str(snapshot.get("project_id")))
    except ValueError:
        return {"status": "failed", "reason": "invalid_snapshot_identity"}
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(ReleaseDecision)
            .join(TestRun, TestRun.id == ReleaseDecision.test_run_id)
            .where(
                ReleaseDecision.test_run_id == run_id,
                TestRun.project_id == project_id,
            )
        )
        record = result.scalar_one_or_none()
    if record is None:
        return {"status": "failed", "reason": "release_record_missing"}
    if record.human_override or record.overridden_by:
        return {
            "status": "failed",
            "reason": "release_record_human_override",
            "human_override": bool(record.human_override),
        }
    comparisons = {
        "pipeline_run_id": (
            str(record.pipeline_run_id) if record.pipeline_run_id else None,
            str(snapshot.get("pipeline_run_id")),
        ),
        "recommendation": (record.recommendation, release.get("recommendation")),
        "risk_score": (record.risk_score, release.get("risk_score")),
        "composite_risk": (record.composite_risk, release.get("composite_risk")),
        "dimension_scores": (record.dimension_scores or {}, release.get("dimension_scores") or {}),
        "policy_id": (
            str(record.policy_id) if record.policy_id else None,
            release.get("policy_id"),
        ),
        "score_model_version": (
            record.score_model_version,
            release.get("score_model_version"),
        ),
        "input_snapshot": (
            record.input_snapshot or {},
            release.get("input_snapshot") or {},
        ),
        "policy_evaluation": (
            _semantic_policy_result(record.policy_evaluation or {}),
            _semantic_policy_result(release.get("policy_evaluation") or {}),
        ),
    }
    mismatches = [name for name, (current, frozen) in comparisons.items() if current != frozen]
    return {
        "status": "passed" if not mismatches else "failed",
        "reason": None if not mismatches else "release_record_superseded_or_mutated",
        "mismatched_fields": mismatches,
    }
