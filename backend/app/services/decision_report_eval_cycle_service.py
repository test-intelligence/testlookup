"""Persisted report-level evaluation cycles.

The evaluator remains deterministic and local. This service adds an auditable
corpus/cycle envelope without storing complete reports or evidence payloads.
"""
from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.mongo import Collections
from app.models.postgres import AgentActionLedger, DecisionReportEvalCycle, DecisionReportFeedback
from app.models.postgres import TestRun
from app.services.canonical_json import stable_json_sha256
from app.services.decision_report_eval_service import evaluate_decision_report_quality
from app.services.evidence_sanitizer import sanitize_persistence_payload

MAX_REPORTS_PER_CYCLE = 200
MAX_CYCLE_KEY = 128
MIN_PILOT_CONSECUTIVE_PASSES = 2
MIN_PILOT_UTILITY_RATE = 0.80


async def summarize_decision_report_feedback(
    db: AsyncSession,
    *,
    project_id: uuid.UUID,
    test_run_ids: Iterable[uuid.UUID],
) -> dict[str, int]:
    """Aggregate persisted utility feedback for an authorized report corpus.

    Only utility rows for the requested tenant and runs contribute. Claim
    corrections are intentionally excluded from the utility denominator.
    The returned shape is bounded and contains counts only, never reasons or
    evidence payloads.
    """
    requested = list(dict.fromkeys(uuid.UUID(str(item)) for item in test_run_ids))
    if not requested or len(requested) > MAX_REPORTS_PER_CYCLE:
        raise ValueError("report_eval_feedback_scope_invalid")
    result = await db.execute(
        select(DecisionReportFeedback.utility_rating).where(
            DecisionReportFeedback.project_id == project_id,
            DecisionReportFeedback.test_run_id.in_(requested),
            DecisionReportFeedback.feedback_kind == "utility",
            DecisionReportFeedback.utility_rating.is_not(None),
        )
    )
    ratings = [str(row[0]) for row in result.all() if row[0]]
    return {
        "sample_count": len(ratings),
        "useful_count": sum(item == "useful" for item in ratings),
        "partially_useful_count": sum(item == "partially_useful" for item in ratings),
    }


async def summarize_decision_report_actions(
    db: AsyncSession,
    *,
    project_id: uuid.UUID,
    test_run_ids: Iterable[uuid.UUID],
) -> dict[str, int]:
    """Aggregate tenant/run-scoped action-ledger outcomes for report evals."""
    requested = list(dict.fromkeys(uuid.UUID(str(item)) for item in test_run_ids))
    if not requested or len(requested) > MAX_REPORTS_PER_CYCLE:
        raise ValueError("report_eval_action_scope_invalid")
    result = await db.execute(
        select(AgentActionLedger.status).where(
            AgentActionLedger.project_id == project_id,
            AgentActionLedger.test_run_id.in_(requested),
        )
    )
    statuses = [str(row[0]) for row in result.all() if row[0]]
    terminal_statuses = {"executed", "failed", "rejected", "rolled_back"}
    return {
        "action_count": len(statuses),
        "terminal_count": sum(status in terminal_statuses for status in statuses),
        "unresolved_count": sum(status not in terminal_statuses for status in statuses),
        "pending_review_count": statuses.count("pending_review"),
        "approved_count": statuses.count("approved"),
        "executing_count": statuses.count("executing"),
        "executed_count": statuses.count("executed"),
        "failed_count": statuses.count("failed"),
        "rejected_count": statuses.count("rejected"),
        "rolled_back_count": statuses.count("rolled_back"),
    }


def _safe_reports(reports: Iterable[dict[str, Any]] | None) -> list[dict[str, Any]]:
    bounded = [item for item in (reports or []) if isinstance(item, dict)]
    if len(bounded) > MAX_REPORTS_PER_CYCLE:
        raise ValueError("report_eval_cycle_too_many_reports")
    sanitized, _stats = sanitize_persistence_payload(bounded)
    if not isinstance(sanitized, list):
        raise ValueError("report_eval_cycle_invalid_corpus")
    return [item for item in sanitized if isinstance(item, dict)]


def compute_report_corpus_sha256(
    corpus_version: str, reports: Iterable[dict[str, Any]] | None
) -> str:
    """Hash the bounded/redacted corpus projection, never raw evidence."""
    if not corpus_version or len(corpus_version) > 120:
        raise ValueError("report_eval_corpus_version_invalid")
    safe_reports = _safe_reports(reports)
    return stable_json_sha256(
        {"corpus_version": corpus_version, "reports": safe_reports},
        max_bytes=2_000_000,
    )


def _aggregate_results(results: list[dict[str, Any]]) -> tuple[str, dict, list, list]:
    if not results:
        return (
            "fail",
            {"reports_evaluated": 0},
            [{"name": "corpus", "status": "fail", "detail": {"reason": "empty"}}],
            ["corpus"],
        )
    by_name: dict[str, list[str]] = defaultdict(list)
    details: dict[str, dict[str, Any]] = {}
    unavailable: set[str] = set()
    numeric: dict[str, list[float]] = defaultdict(list)
    for result in results:
        for check in result.get("checks", []):
            if not isinstance(check, dict):
                continue
            name = str(check.get("name") or "unknown")[:100]
            status = str(check.get("status") or "fail")
            by_name[name].append(status)
            details.setdefault(name, {"sample_details": []})["sample_details"].append(
                check.get("detail") or {}
            )
        unavailable.update(
            str(item)[:100]
            for item in result.get("unavailable_metrics", [])
            if item
        )
        for key, value in (result.get("metrics") or {}).items():
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                continue
            numeric[str(key)[:100]].append(float(value))
    checks: list[dict[str, Any]] = []
    for name in sorted(by_name):
        statuses = by_name[name]
        status = (
            "fail" if "fail" in statuses else
            "warn" if "warn" in statuses else
            "not_evaluated" if all(item == "not_evaluated" for item in statuses)
            else "pass"
        )
        checks.append({"name": name, "status": status, "detail": {
            "sample_count": len(statuses),
            "status_counts": {item: statuses.count(item) for item in sorted(set(statuses))},
        }})
    metrics = {"reports_evaluated": len(results)}
    metrics.update({key: sum(values) / len(values) for key, values in numeric.items() if values})
    overall = "fail" if any(item["status"] == "fail" for item in checks) else (
        "warn" if unavailable or any(item["status"] in {"warn", "not_evaluated"} for item in checks)
        else "pass"
    )
    return overall, metrics, checks, sorted(unavailable)


def assess_report_eval_readiness(
    cycles: Iterable[Any],
    *,
    min_consecutive_passes: int = MIN_PILOT_CONSECUTIVE_PASSES,
    min_utility_rate: float = MIN_PILOT_UTILITY_RATE,
) -> dict[str, Any]:
    """Return a fail-closed pilot gate from persisted cycle projections.

    This is deliberately pure: callers decide which tenant-authorized corpus
    rows to provide, while this helper enforces the same two-cycle and utility
    requirements everywhere. A warning/not-evaluated cycle is never promoted
    to ready by a prior pass.
    """
    required_passes = int(min_consecutive_passes)
    utility_floor = float(min_utility_rate)
    if required_passes < 1 or not 0 <= utility_floor <= 1:
        raise ValueError("report_eval_readiness_threshold_invalid")
    rows = [item for item in cycles if item is not None]

    def _evaluation_sort_key(item: Any) -> float:
        value = getattr(item, "evaluated_at", None)
        return value.timestamp() if hasattr(value, "timestamp") else 0.0

    rows.sort(key=_evaluation_sort_key, reverse=True)
    latest = rows[0] if rows else None
    reasons: list[str] = []
    latest_status = str(getattr(latest, "status", "missing")) if latest else "missing"
    consecutive = int(getattr(latest, "consecutive_passes", 0) or 0) if latest else 0
    metrics = getattr(latest, "metrics", {}) if latest else {}
    metrics = metrics if isinstance(metrics, dict) else {}
    utility = metrics.get("utility_rate")
    utility_valid = (
        isinstance(utility, (int, float))
        and not isinstance(utility, bool)
        and 0 <= float(utility) <= 1
    )
    if latest is None:
        reasons.append("no_evaluation_cycle")
    if latest_status != "pass":
        reasons.append("latest_cycle_not_pass")
    if consecutive < required_passes:
        reasons.append("consecutive_passes_below_threshold")
    if not utility_valid:
        reasons.append("qualified_user_utility_unavailable")
    elif float(utility) < utility_floor:
        reasons.append("qualified_user_utility_below_threshold")
    return {
        "status": "ready" if not reasons else "not_ready",
        "latest_status": latest_status,
        "consecutive_passes": consecutive,
        "required_consecutive_passes": required_passes,
        "utility_rate": float(utility) if utility_valid else None,
        "minimum_utility_rate": utility_floor,
        "corpus_version": getattr(latest, "corpus_version", None) if latest else None,
        "corpus_sha256": getattr(latest, "corpus_sha256", None) if latest else None,
        "reasons": reasons,
    }


async def evaluate_and_persist_report_cycle(
    db: AsyncSession,
    *,
    corpus_version: str,
    reports: Iterable[dict[str, Any]] | None,
    cycle_key: str | None = None,
    authorized_evidence_ids: Iterable[str] | None = None,
    feedback_summary: dict[str, Any] | None = None,
    action_summary: dict[str, Any] | None = None,
    evaluated_by: uuid.UUID | None = None,
) -> tuple[DecisionReportEvalCycle, bool]:
    """Evaluate a bounded corpus and persist one idempotent cycle record."""
    if cycle_key is not None and (not cycle_key or len(cycle_key) > MAX_CYCLE_KEY):
        raise ValueError("report_eval_cycle_key_invalid")
    safe_reports = _safe_reports(reports)
    corpus_sha256 = compute_report_corpus_sha256(corpus_version, safe_reports)
    key = cycle_key or f"{corpus_version}:{corpus_sha256}:{uuid.uuid4()}"
    existing = await db.execute(
        select(DecisionReportEvalCycle).where(
            DecisionReportEvalCycle.cycle_key == key
        ).with_for_update()
    )
    row = existing.scalar_one_or_none()
    if row is not None:
        return row, False

    results = [
        evaluate_decision_report_quality(
            report,
            authorized_evidence_ids=authorized_evidence_ids,
            feedback_summary=feedback_summary,
            action_summary=action_summary,
        )
        for report in safe_reports
    ]
    status, metrics, checks, unavailable = _aggregate_results(results)
    previous_result = await db.execute(
        select(DecisionReportEvalCycle)
        .where(DecisionReportEvalCycle.corpus_version == corpus_version)
        .order_by(DecisionReportEvalCycle.evaluated_at.desc())
        .limit(1)
    )
    previous = previous_result.scalar_one_or_none()
    consecutive = (int(previous.consecutive_passes or 0) + 1) if (
        previous is not None
        and previous.corpus_sha256 == corpus_sha256
        and status == "pass"
        and previous.status == "pass"
    ) else (1 if status == "pass" else 0)
    row = DecisionReportEvalCycle(
        cycle_key=key,
        corpus_version=corpus_version,
        corpus_sha256=corpus_sha256,
        report_count=len(safe_reports),
        status=status,
        metrics=metrics,
        checks=checks,
        unavailable_metrics=unavailable,
        consecutive_passes=consecutive,
        evaluated_by=evaluated_by,
        evaluated_at=datetime.now(timezone.utc),
    )
    db.add(row)
    await db.flush()
    return row, True


async def load_authoritative_report_projections(
    *,
    mongo_db: Any,
    db: AsyncSession,
    project_id: uuid.UUID,
    test_run_ids: Iterable[uuid.UUID],
) -> list[dict[str, Any]]:
    """Load latest published reports after server-side project/run checks."""
    requested = list(dict.fromkeys(uuid.UUID(str(item)) for item in test_run_ids))
    if not requested or len(requested) > MAX_REPORTS_PER_CYCLE:
        raise ValueError("report_eval_run_scope_invalid")
    result = await db.execute(
        select(TestRun.id).where(
            TestRun.project_id == project_id,
            TestRun.id.in_(requested),
        )
    )
    authorized = {str(item) for item in result.scalars().all()}
    if authorized != {str(item) for item in requested}:
        raise ValueError("report_eval_run_scope_invalid")
    cursor = mongo_db[Collections.DECISION_REPORTS].find(
        {
            "project_id": str(project_id),
            "test_run_id": {"$in": sorted(authorized)},
            "status": "published",
        },
        {"_id": 0},
    ).sort("report_version", -1).limit(MAX_REPORTS_PER_CYCLE * 5)
    documents = await cursor.to_list(length=MAX_REPORTS_PER_CYCLE * 5)
    latest: dict[str, dict[str, Any]] = {}
    for document in documents:
        run_id = str(document.get("test_run_id") or "")
        if run_id and run_id not in latest:
            projection = dict(document.get("decision_intelligence") or {})
            projection["report_id"] = document.get("report_id")
            projection["report_version"] = document.get("report_version")
            projection["verification"] = document.get("verification") or {}
            latest[run_id] = projection
    if set(latest) != authorized:
        raise ValueError("report_eval_published_report_missing")
    return [latest[str(item)] for item in requested]
