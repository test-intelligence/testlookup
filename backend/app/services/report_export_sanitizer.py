"""Explicit, fail-closed projection for downloadable AI report content."""
from __future__ import annotations

from typing import Any, Iterable

from app.services.evidence_sanitizer import sanitize_persistence_payload


def _pick(value: Any, keys: Iterable[str]) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    return {key: value[key] for key in keys if key in value}


def _pick_many(value: Any, keys: Iterable[str], *, limit: int = 100) -> list[dict]:
    if not isinstance(value, list):
        return []
    return [_pick(item, keys) for item in value[:limit] if isinstance(item, dict)]


def sanitize_report_export_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Return only documented report fields; raw evidence has no export path."""
    summary = _pick(payload.get("structured_summary"), (
        "executive_summary", "layer1_executive",
    ))
    layer4 = _pick(
        (payload.get("structured_summary") or {}).get("layer4_action_plan")
        if isinstance(payload.get("structured_summary"), dict) else None,
        ("immediate_mitigation", "fix_recommendations", "validation_steps", "owner_hints"),
    )
    if layer4:
        summary["layer4_action_plan"] = layer4

    projection: dict[str, Any] = {
        "run": _pick(payload.get("run"), (
            "id", "run_id", "build_number", "branch", "status", "started_at",
            "completed_at", "total_tests", "passed_tests", "failed_tests",
            "broken_tests", "skipped_tests", "unknown_tests", "pass_rate",
            "duration_seconds", "project_name",
        )),
        "structured_summary": summary,
        "failure_clusters": _pick_many(payload.get("failure_clusters"), (
            "cluster_id", "label", "size", "criticality_level", "cohesion_score",
            "failure_category", "affected_tests", "recommended_actions",
            "requires_human_review", "dimension_scores",
        ), limit=20),
        "category_breakdown": payload.get("category_breakdown")
        if isinstance(payload.get("category_breakdown"), dict) else {},
        "affected_suites": _pick_many(payload.get("affected_suites"), (
            "suite", "suite_name", "failed_count", "total_count", "pass_rate",
        )),
        "top_analyses": _pick_many(payload.get("top_analyses"), (
            "test_name", "test_case_id", "failure_category", "root_cause_summary",
            "confidence_score", "is_flaky", "requires_human_review",
            "recommended_actions", "role_actions",
        ), limit=20),
        "avg_confidence": payload.get("avg_confidence"),
        "release_decision": _pick(payload.get("release_decision"), (
            "recommendation", "risk_score", "composite_risk", "blocking_issues",
            "conditions_for_go", "reasoning", "requires_human_review",
        )),
        "dimension_scores": _pick_many(payload.get("dimension_scores"), (
            "name", "label", "score", "weight", "contribution",
        )),
        "what_changed_since_last_good_run": _pick(
            payload.get("what_changed_since_last_good_run"),
            ("pass_rate_delta", "new_failures", "resolved_failures", "regression_classification"),
        ),
        "defect_candidates": _pick_many(payload.get("defect_candidates"), (
            "cluster_id", "label", "severity_hint", "failure_category", "confidence",
            "recommended_actions", "status", "duplicate_detected",
            "duplicate_defect_id", "promoted_defect_id", "composite_score",
        ), limit=20),
        "provenance": _pick(payload.get("provenance"), (
            "confidence", "sources_used", "evidence_count", "deterministic_checks_used",
        )),
        "intelligence_available": payload.get("intelligence_available"),
        "all_green": payload.get("all_green"),
    }

    safe, stats = sanitize_persistence_payload(projection)
    if not isinstance(safe, dict) or stats.omitted_items:
        raise ValueError("report export payload exceeds the safe projection limit")
    return safe
