"""Durable, asynchronous child-enriched DecisionReport publication.

This slice is intentionally dark-gated.  Requests contain only tenant/run/
pipeline identifiers; the worker re-resolves the parent report and terminal
cluster children from PostgreSQL/Mongo before creating a new immutable report
version.  The resulting version is explicitly marked degraded/human-review
because it is a deterministic supersession projection, not a second LLM
critic pass.
"""
from __future__ import annotations

import uuid
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import or_, select

from app.agents.decision_report_agent import compute_decision_evidence_hash, render_decision_markdown
from app.db.mongo import Collections, get_mongo_db
from app.db.postgres import AsyncSessionLocal
from app.models.postgres import (
    AgentInvestigation,
    AgentPipelineRun,
    DecisionReportSupersessionRequest,
    TestRun,
)
from app.services.decision_report_service import publish_decision_report
from app.services.evidence_sanitizer import sanitize_persistence_payload

MAX_BATCH = 20
RETRY_SECONDS = 30
PROCESSING_LEASE_SECONDS = 300


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def schedule_decision_report_supersession(
    *,
    project_id: str,
    test_run_id: str,
    parent_pipeline_run_id: str,
    reason: str = "cluster_children_terminal",
) -> dict[str, Any]:
    """Create or return the one idempotent request for a parent pipeline."""
    parent_id = uuid.UUID(str(parent_pipeline_run_id))
    async with AsyncSessionLocal() as db:
        parent = (await db.execute(
            select(AgentPipelineRun).join(
                TestRun, TestRun.id == AgentPipelineRun.test_run_id
            ).where(
                AgentPipelineRun.id == parent_id,
                AgentPipelineRun.test_run_id == uuid.UUID(str(test_run_id)),
                TestRun.project_id == uuid.UUID(str(project_id)),
            ).with_for_update()
        )).scalar_one_or_none()
        if parent is None:
            raise ValueError("supersession_parent_not_found")
        metadata = dict(parent.execution_metadata or {})
        if metadata.get("async_decision_report_supersession_enabled") is not True:
            raise ValueError("async_decision_report_supersession_disabled")
        existing = (await db.execute(
            select(DecisionReportSupersessionRequest).where(
                DecisionReportSupersessionRequest.parent_pipeline_run_id == parent_id
            ).with_for_update()
        )).scalar_one_or_none()
        if existing is not None:
            return _request_projection(existing)
        row = DecisionReportSupersessionRequest(
            project_id=uuid.UUID(str(project_id)),
            test_run_id=uuid.UUID(str(test_run_id)),
            parent_pipeline_run_id=parent_id,
            reason=str(reason)[:120],
            next_attempt_at=_now(),
        )
        db.add(row)
        await db.commit()
        return _request_projection(row)


def _request_projection(row: DecisionReportSupersessionRequest) -> dict[str, Any]:
    return {
        "request_id": str(row.id),
        "parent_pipeline_run_id": str(row.parent_pipeline_run_id),
        "status": row.status,
        "attempts": int(row.attempts or 0),
        "reason": row.reason,
    }


def merge_terminal_child_projection(
    decision: dict[str, Any],
    children: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build a bounded, deterministic child projection without mutating input."""
    safe_children, _stats = sanitize_persistence_payload(children[:20])
    safe_children = safe_children if isinstance(safe_children, list) else []
    merged = deepcopy(decision)
    statuses = [str(item.get("status") or "failed") for item in safe_children if isinstance(item, dict)]
    degraded = any(status != "completed" for status in statuses)
    merged["cluster_investigation_results"] = {
        "status": "degraded" if degraded else "complete",
        "selected_count": len(safe_children),
        "completed_count": sum(status == "completed" for status in statuses),
        "children": safe_children,
        "stop_reasons": sorted({
            str(item.get("stop_reason"))
            for item in safe_children
            if isinstance(item, dict) and item.get("stop_reason")
        }),
    }
    quality = deepcopy(merged.get("quality_review") or {})
    missing = list(quality.get("missing_or_failed_specialists") or [])
    if degraded and "cluster_investigation" not in missing:
        missing.append("cluster_investigation")
    quality["missing_or_failed_specialists"] = sorted(set(map(str, missing)))
    quality["requires_human_review"] = True if safe_children else bool(
        quality.get("requires_human_review")
    )
    merged["quality_review"] = quality
    sources = list(merged.get("source_stages") or [])
    if "cluster_investigation_join" not in sources:
        sources.append("cluster_investigation_join")
    merged["source_stages"] = sources
    merged["status"] = "degraded" if degraded else merged.get("status", "complete")
    merged["verification"] = {
        "status": "degraded",
        "checks": ["supersession_child_authority", "supersession_projection_hash"],
        "repairs": [],
        "unresolved_failures": [] if not degraded else ["cluster_investigation"],
    }
    merged["evidence_bundle_sha256"] = compute_decision_evidence_hash(merged)
    return merged


def validate_supersession_projection(
    *,
    previous_decision: dict[str, Any],
    candidate_decision: dict[str, Any],
    children: list[dict[str, Any]],
    project_id: str | None = None,
    test_run_id: str | None = None,
    previous_report: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Independently replay the supersession delta and fail closed on drift.

    This validator does not call ``build_decision_intelligence`` or trust the
    worker's candidate payload. It recomputes the bounded child projection from
    the supplied terminal rows, verifies the prior report's evidence digest and
    tenant/run identity, then compares the complete canonical evidence
    projection plus its digest.
    """
    failures: list[str] = []
    if previous_report is not None:
        if project_id is not None and str(previous_report.get("project_id")) != str(project_id):
            failures.append("previous_report_project_mismatch")
        if test_run_id is not None and str(previous_report.get("test_run_id")) != str(test_run_id):
            failures.append("previous_report_run_mismatch")
        if (previous_report.get("verification") or {}).get("status") != "passed":
            failures.append("previous_report_not_verified")
    previous_hash = previous_decision.get("evidence_bundle_sha256")
    if previous_hash != compute_decision_evidence_hash(previous_decision):
        failures.append("previous_report_evidence_hash_mismatch")
    expected = merge_terminal_child_projection(previous_decision, children)
    if candidate_decision != expected:
        failures.append("supersession_projection_mismatch")
    observed_hash = candidate_decision.get("evidence_bundle_sha256")
    expected_hash = compute_decision_evidence_hash(candidate_decision)
    if observed_hash != expected_hash:
        failures.append("supersession_evidence_hash_mismatch")
    return {
        "status": "passed" if not failures else "failed",
        "checks": [
            "previous_report_identity",
            "previous_report_evidence_hash",
            "terminal_child_projection",
            "supersession_evidence_hash",
        ],
        "failures": failures,
    }


async def _process_request(request_id: uuid.UUID) -> dict[str, Any]:
    async with AsyncSessionLocal() as db:
        row = (await db.execute(
            select(DecisionReportSupersessionRequest).where(
                DecisionReportSupersessionRequest.id == request_id
            ).with_for_update()
        )).scalar_one_or_none()
        if row is None or row.status in {"published", "rejected", "failed"}:
            return {"status": "skipped"}
        row.status = "processing"
        row.attempts = int(row.attempts or 0) + 1
        row.claimed_at = _now()
        await db.commit()
        project_id, test_run_id, parent_id = row.project_id, row.test_run_id, row.parent_pipeline_run_id

    mongo = get_mongo_db()
    latest = await mongo[Collections.DECISION_REPORTS].find_one(
        {
            "project_id": str(project_id),
            "test_run_id": str(test_run_id),
            "pipeline_run_id": str(parent_id),
            "status": "published",
        },
        {"_id": 0},
        sort=[("report_version", -1)],
    )
    async with AsyncSessionLocal() as db:
        children = list((await db.execute(
            select(AgentInvestigation).where(
                AgentInvestigation.parent_pipeline_run_id == parent_id,
                AgentInvestigation.scope_type == "failure_cluster",
            ).order_by(AgentInvestigation.failure_cluster_id, AgentInvestigation.id).limit(20)
        )).scalars().all())
        active = [item for item in children if item.status in {"queued", "running", "synthesizing"}]
        row = (await db.execute(
            select(DecisionReportSupersessionRequest).where(
                DecisionReportSupersessionRequest.id == request_id
            ).with_for_update()
        )).scalar_one_or_none()
        if row is None:
            return {"status": "missing"}
        if latest is None or active:
            row.status = "pending"
            row.next_attempt_at = _now() + timedelta(seconds=RETRY_SECONDS)
            row.error = "parent_report_or_children_not_terminal"
            await db.commit()
            return {"status": "pending", "active_children": len(active)}
        child_projection = [
            {
                "investigation_id": str(child.id),
                "failure_cluster_id": str(child.failure_cluster_id),
                "cluster_scope_sha256": child.cluster_scope_sha256,
                "status": child.status,
                "stop_reason": (
                    "cluster_child_join_timeout"
                    if child.cancelled_by == "parent_join_timeout"
                    else child.error
                ),
                "spend": dict(child.spend or {}),
                "verdict": dict(child.verdict or {}),
            }
            for child in children
        ]
        previous_decision = dict(latest.get("decision_intelligence") or {})
        decision = merge_terminal_child_projection(previous_decision, child_projection)
        verification = validate_supersession_projection(
            previous_decision=previous_decision,
            candidate_decision=decision,
            children=child_projection,
            project_id=str(project_id),
            test_run_id=str(test_run_id),
            previous_report=latest,
        )
        if verification["status"] != "passed":
            row.status = "rejected"
            row.next_attempt_at = None
            row.error = "supersession_validation_failed"
            await db.commit()
            return {"status": "rejected", "failures": verification["failures"]}
        decision["verification"] = {
            "status": "passed",
            "scope": "supersession_delta",
            "checks": verification["checks"],
            "repairs": [],
            "unresolved_failures": [],
        }
        decision["evidence_bundle_sha256"] = compute_decision_evidence_hash(decision)
        supersession_pipeline_id = str(
            uuid.uuid5(uuid.NAMESPACE_URL, f"testlookup:report-supersession:{request_id}")
        )
        report = await publish_decision_report(
            mongo,
            state={
                "project_id": str(project_id),
                "test_run_id": str(test_run_id),
                "pipeline_run_id": supersession_pipeline_id,
            },
            decision=decision,
            markdown=render_decision_markdown(decision),
        )
        row.status = "published"
        row.published_report_id = report["report_id"]
        row.published_report_version = int(report["report_version"])
        row.next_attempt_at = None
        row.error = None
        await db.commit()
        return {"status": "published", "report_id": report["report_id"], "report_version": report["report_version"]}


async def process_pending_decision_report_supersessions() -> dict[str, int]:
    now = _now()
    cutoff = now - timedelta(seconds=PROCESSING_LEASE_SECONDS)
    async with AsyncSessionLocal() as db:
        rows = list((await db.execute(
            select(DecisionReportSupersessionRequest).where(
                or_(
                    DecisionReportSupersessionRequest.status == "pending",
                    (
                        DecisionReportSupersessionRequest.status == "processing"
                    ) & (DecisionReportSupersessionRequest.claimed_at <= cutoff),
                ),
                or_(
                    DecisionReportSupersessionRequest.next_attempt_at.is_(None),
                    DecisionReportSupersessionRequest.next_attempt_at <= now,
                ),
            ).order_by(DecisionReportSupersessionRequest.created_at).with_for_update(skip_locked=True).limit(MAX_BATCH)
        )).scalars().all())
    published = pending = failed = 0
    for row in rows:
        try:
            result = await _process_request(row.id)
            if result.get("status") == "published":
                published += 1
            elif result.get("status") == "pending":
                pending += 1
        except Exception as exc:  # noqa: BLE001
            failed += 1
            async with AsyncSessionLocal() as db:
                current = (await db.execute(
                    select(DecisionReportSupersessionRequest).where(
                        DecisionReportSupersessionRequest.id == row.id
                    ).with_for_update()
                )).scalar_one_or_none()
                if current is not None:
                    current.status = "failed" if int(current.attempts or 0) >= 10 else "pending"
                    current.next_attempt_at = None if current.status == "failed" else _now() + timedelta(seconds=RETRY_SECONDS)
                    current.error = type(exc).__name__
                    await db.commit()
    return {"claimed": len(rows), "published": published, "pending": pending, "failed": failed}
