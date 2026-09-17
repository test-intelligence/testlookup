"""Weekly confidence-interval drift gate (architecture E9.6 / G5)."""
from __future__ import annotations

import hashlib
import json
import math
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.postgres import (
    AIEvalRun,
    AgentConfig,
    Release,
    ReleaseGateDecision,
    ReleaseOutcome,
    ReviewRequest,
)
from app.services.agent_capability_registry import get_capability
from app.services.agent_authority_lock import lock_project_agent_authority
from app.services.eval_provenance_service import current_eval_manifest_checksum
from app.services.review_request_service import AI_DISCLAIMER_VERSION

MIN_BINOMIAL_SAMPLES = 20

# The nightly producer's task vocabulary predates capability ids. Keep the
# bridge explicit until AIEvalRun itself becomes project/capability scoped.
TASK_CAPABILITIES: dict[str, str] = {
    "classification": get_capability("anomaly_detection").capability_id,
    "root_cause": get_capability("root_cause_analysis").capability_id,
    "duplicate_detection": get_capability("failure_clustering").capability_id,
    "release_decision": get_capability("release_risk").capability_id,
}


def wilson_interval(successes: int, total: int, *, z: float = 1.96) -> tuple[float, float] | None:
    """Return a 95% Wilson interval for a binomial rate."""
    if total <= 0 or successes < 0 or successes > total:
        return None
    rate = successes / total
    z2 = z * z
    denominator = 1 + z2 / total
    centre = (rate + z2 / (2 * total)) / denominator
    margin = z * math.sqrt((rate * (1 - rate) + z2 / (4 * total)) / total) / denominator
    return round(max(0.0, centre - margin), 6), round(min(1.0, centre + margin), 6)


def compare_rates(
    name: str,
    *,
    current_successes: int,
    current_total: int,
    previous_successes: int,
    previous_total: int,
    lower_is_worse: bool = True,
    minimum_samples: int = MIN_BINOMIAL_SAMPLES,
) -> dict[str, Any]:
    """Compare two rates. Drift requires disjoint confidence intervals."""
    current_ci = wilson_interval(current_successes, current_total)
    previous_ci = wilson_interval(previous_successes, previous_total)
    measured = (
        current_total >= minimum_samples
        and previous_total >= minimum_samples
        and current_ci is not None
        and previous_ci is not None
    )
    direction = "unmeasured"
    if measured:
        assert current_ci is not None and previous_ci is not None
        current_rate = current_successes / current_total
        previous_rate = previous_successes / previous_total
        disjoint = current_ci[1] < previous_ci[0] or current_ci[0] > previous_ci[1]
        if not disjoint:
            direction = "stable"
        elif current_rate < previous_rate:
            direction = "degrading" if lower_is_worse else "improving"
        else:
            direction = "improving" if lower_is_worse else "degrading"
    return {
        "name": name,
        "measured": measured,
        "direction": direction,
        "current": {
            "successes": current_successes,
            "total": current_total,
            "rate": round(current_successes / current_total, 6) if current_total else None,
            "ci95": list(current_ci) if current_ci else None,
        },
        "previous": {
            "successes": previous_successes,
            "total": previous_total,
            "rate": round(previous_successes / previous_total, 6) if previous_total else None,
            "ci95": list(previous_ci) if previous_ci else None,
        },
    }


async def has_active_drift_pin(db: AsyncSession, project_id: uuid.UUID, capability_id: str) -> bool:
    row = await db.scalar(
        select(ReviewRequest.id).where(
            ReviewRequest.project_id == project_id,
            ReviewRequest.kind == "eval_drift",
            ReviewRequest.subject_type == "capability",
            ReviewRequest.capability_id == capability_id,
            ReviewRequest.state == "pending_review",
        ).limit(1)
    )
    return bool(row)


def _evidence_hash(report: Mapping[str, Any]) -> str:
    payload = json.dumps(report, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


async def ensure_drift_review(
    db: AsyncSession,
    *,
    project_id: uuid.UUID,
    capability_id: str,
    report: Mapping[str, Any],
) -> tuple[ReviewRequest, bool]:
    """Return the live pin, creating a new pending human review when needed."""
    await lock_project_agent_authority(db, project_id)
    subject_id = f"{project_id}:{capability_id}"
    live = (
        await db.execute(
            select(ReviewRequest).where(
                ReviewRequest.kind == "eval_drift",
                ReviewRequest.subject_type == "capability",
                ReviewRequest.subject_id == subject_id,
                ReviewRequest.state != "superseded",
            )
        )
    ).scalar_one_or_none()
    if live is not None and live.state == "pending_review":
        return live, False
    if live is not None:
        live.state = "superseded"
        await db.flush()
    row = ReviewRequest(
        id=uuid.uuid4(),
        project_id=project_id,
        kind="eval_drift",
        subject_type="capability",
        subject_id=subject_id,
        capability_id=capability_id,
        state="pending_review",
        created_by="system",
        evidence_bundle_sha256=_evidence_hash(report),
        eval_manifest_checksum=current_eval_manifest_checksum(),
        ai_disclaimer_version=AI_DISCLAIMER_VERSION,
    )
    db.add(row)
    await db.flush()
    if live is not None:
        live.superseded_by = row.id
    return row, True


async def _eval_counts(db: AsyncSession, task_type: str, start: datetime, end: datetime) -> tuple[int, int]:
    row = (
        await db.execute(
            select(
                func.coalesce(func.sum(AIEvalRun.correct_items), 0),
                func.coalesce(func.sum(AIEvalRun.total_items), 0),
            ).where(
                AIEvalRun.task_type == task_type,
                AIEvalRun.evaluated_at >= start,
                AIEvalRun.evaluated_at < end,
            )
        )
    ).one()
    return int(row[0]), int(row[1])


async def _review_counts(
    db: AsyncSession, project_id: uuid.UUID, capability_id: str, start: datetime, end: datetime
) -> dict[str, tuple[int, int]]:
    rows = (
        await db.execute(
            select(ReviewRequest.state, ReviewRequest.reason_code).where(
                ReviewRequest.project_id == project_id,
                ReviewRequest.capability_id == capability_id,
                ReviewRequest.reviewed_at >= start,
                ReviewRequest.reviewed_at < end,
                ReviewRequest.state.in_(("accepted", "rejected")),
            )
        )
    ).all()
    total = len(rows)
    counts = {"review_accept_rate": (sum(state == "accepted" for state, _ in rows), total)}
    for code in ("wrong_category", "unsupported_claim", "missing_evidence", "contradiction", "stale_data", "other"):
        counts[f"review_reason:{code}"] = (sum(reason == code for _, reason in rows), total)
    return counts


async def _incident_after_go_counts(
    db: AsyncSession, project_id: uuid.UUID, start: datetime, end: datetime
) -> tuple[int, int]:
    decisions = (
        await db.execute(
            select(ReleaseGateDecision.release_id, ReleaseGateDecision.created_at)
            .join(Release, Release.id == ReleaseGateDecision.release_id)
            .where(
                Release.project_id == project_id,
                ReleaseGateDecision.phase_id.is_(None),
                ReleaseGateDecision.verdict == "GO",
                ReleaseGateDecision.created_at >= start,
                ReleaseGateDecision.created_at < end,
            )
        )
    ).all()
    outcomes = (
        await db.execute(
            select(ReleaseOutcome.release_id, ReleaseOutcome.marked_at).where(
                ReleaseOutcome.project_id == project_id,
                ReleaseOutcome.outcome_kind == "incident",
                ReleaseOutcome.marked_at >= start,
                ReleaseOutcome.marked_at < end,
            )
        )
    ).all()
    incident_releases = {
        release_id
        for release_id, decided_at in decisions
        if any(outcome_release == release_id and marked_at >= decided_at for outcome_release, marked_at in outcomes)
    }
    return len(incident_releases), len(decisions)


async def evaluate_project_capability_drift(
    db: AsyncSession,
    *,
    project_id: uuid.UUID,
    task_type: str,
    capability_id: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    end = now or datetime.now(timezone.utc)
    current_start = end - timedelta(days=7)
    previous_start = current_start - timedelta(days=7)
    current_eval = await _eval_counts(db, task_type, current_start, end)
    previous_eval = await _eval_counts(db, task_type, previous_start, current_start)
    current_reviews = await _review_counts(db, project_id, capability_id, current_start, end)
    previous_reviews = await _review_counts(db, project_id, capability_id, previous_start, current_start)
    metrics = [compare_rates("eval_accuracy", current_successes=current_eval[0], current_total=current_eval[1], previous_successes=previous_eval[0], previous_total=previous_eval[1])]
    for name in current_reviews:
        current_count, current_total = current_reviews[name]
        previous_count, previous_total = previous_reviews[name]
        metrics.append(compare_rates(name, current_successes=current_count, current_total=current_total, previous_successes=previous_count, previous_total=previous_total, lower_is_worse=name == "review_accept_rate"))
    if task_type == "release_decision":
        current_incidents = await _incident_after_go_counts(db, project_id, current_start, end)
        previous_incidents = await _incident_after_go_counts(db, project_id, previous_start, current_start)
        metrics.append(compare_rates("incident_after_go_rate", current_successes=current_incidents[0], current_total=current_incidents[1], previous_successes=previous_incidents[0], previous_total=previous_incidents[1], lower_is_worse=False))
    degrading = [metric["name"] for metric in metrics if metric["direction"] == "degrading"]
    return {
        "project_id": str(project_id),
        "task_type": task_type,
        "capability_id": capability_id,
        "measured": any(metric["measured"] for metric in metrics),
        "drift": bool(degrading),
        "degrading_metrics": degrading,
        "metrics": metrics,
        "current_window": {"start": current_start.isoformat(), "end": end.isoformat()},
        "previous_window": {"start": previous_start.isoformat(), "end": current_start.isoformat()},
    }


async def run_weekly_online_drift(db: AsyncSession, *, now: datetime | None = None) -> dict[str, Any]:
    """Evaluate configured capabilities and stage one human pin per detected drift."""
    configured = (await db.execute(select(AgentConfig.project_id, AgentConfig.agent_id))).all()
    pairs = {(project_id, agent_id) for project_id, agent_id in configured}
    reports: list[dict[str, Any]] = []
    created = 0
    for task_type, capability_id in TASK_CAPABILITIES.items():
        for project_id, agent_id in sorted(pairs, key=lambda pair: (str(pair[0]), pair[1])):
            if agent_id != capability_id:
                continue
            report = await evaluate_project_capability_drift(
                db, project_id=project_id, task_type=task_type, capability_id=capability_id, now=now
            )
            if report["drift"]:
                _, was_created = await ensure_drift_review(
                    db, project_id=project_id, capability_id=capability_id, report=report
                )
                created += int(was_created)
            reports.append(report)
    return {
        "configured_capabilities_evaluated": len(reports),
        "measured": sum(bool(report["measured"]) for report in reports),
        "drifted": sum(bool(report["drift"]) for report in reports),
        "review_requests_created": created,
        "reports": reports,
    }


__all__ = [
    "TASK_CAPABILITIES",
    "compare_rates",
    "ensure_drift_review",
    "evaluate_project_capability_drift",
    "has_active_drift_pin",
    "run_weekly_online_drift",
    "wilson_interval",
]
