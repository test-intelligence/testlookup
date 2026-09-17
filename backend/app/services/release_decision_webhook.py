"""``release.decided``: the outbound webhook for a written release decision.

``webhook_service.SUPPORTED_EVENTS`` offered ``release.decided`` to subscribers,
and nothing ever sent it: a subscription was accepted and then never received a
delivery. A release decision is published from two places, both only after its
authoritative source has committed:

* ``trigger="agent"``: ``DecisionReportCriticAgent._persist`` published the
  immutable decision report for the deep pipeline run;
* ``trigger="override"``: a QA lead overrode it through
  ``POST /api/v1/release-readiness/{run_id}/override``. An override changes the
  value CI pipelines gate on, which is exactly what a subscriber listens for, so
  it emits too, with ``overridden: true``.

What the payload says
---------------------
The value is the one ``GET /api/v1/release-readiness/{run_id}`` returns: the
``get_release_council`` recommendation (band floor included) under the same
E8.4 review-gate rule (``release_review_projection``). While
``REVIEW_GATE_ENFORCED`` is on, an unreviewed AI decision is sent as
``PENDING_REVIEW`` with the model's value in ``draft_recommendation``. Otherwise
the value is unchanged. Each delivery attempt rechecks the exact pipeline
subject so a queued retry reflects acceptance, rejection, or supersession that
happened while the receiver was unavailable. Either way a ``review`` block says
whether a human reviewed the decision and when, never who. ``overridden_by`` and
the override reason stay out for the same reason: the reason is free text that
routinely names people.

Delivery
--------
``emit_event`` stages one ``WebhookDelivery`` per matching subscription and
publishes it; ``relay_pending_webhook_deliveries`` recovers a lost broker
publish. ``delivery_scope`` makes a retried write idempotent: one delivery per
pipeline run for an agent decision, and one per override, keyed by its position
in ``override_audit``. Like every other producer, emission is best-effort. A
crash between the commit and the staging insert loses the event, and so does a
failure to build the payload (a review lookup error included). That fails
closed: an unreviewed value is never sent because the gate could not be read.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Optional

import structlog
from sqlalchemy import select

from app.db.postgres import AsyncSessionLocal
from app.models.postgres import ReleaseDecision, TestRun
from app.services import report_distribution_policy, webhook_service
from app.services.release_council_service import get_release_council

logger = structlog.get_logger("services.release_decision_webhook")

TRIGGER_AGENT = "agent"
TRIGGER_OVERRIDE = "override"
_TRIGGERS = frozenset({TRIGGER_AGENT, TRIGGER_OVERRIDE})


def _iso(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat() if value is not None else None


def delivery_scope(
    decision: ReleaseDecision,
    trigger: str,
    *,
    override_ordinal: int | None = None,
) -> str:
    """The idempotency scope for one decision write."""
    if trigger == TRIGGER_OVERRIDE:
        if override_ordinal is None:
            raise ValueError("override delivery scope requires its committed ordinal")
        marker = f"override:{override_ordinal}"
    else:
        marker = f"pipeline:{decision.pipeline_run_id}"
    return f"release-decided:{decision.test_run_id}:{marker}:v1"


async def build_release_decided_payload(
    db: Any,
    *,
    run_id: uuid.UUID,
    project_id: uuid.UUID,
    decision: ReleaseDecision,
    trigger: str,
    evidence_bundle_sha256: str | None = None,
    override_snapshot: dict[str, Any] | None = None,
    override_audit_timestamp: str | None = None,
) -> Optional[dict[str, Any]]:
    report: dict[str, Any] | None = None
    if (
        trigger == TRIGGER_AGENT
        and decision.pipeline_run_id is not None
        and evidence_bundle_sha256 is None
    ):
        from app.db.mongo import get_mongo_db
        from app.services.decision_report_service import (
            load_decision_report_for_pipeline,
        )

        report = await load_decision_report_for_pipeline(
            get_mongo_db(),
            str(run_id),
            str(decision.pipeline_run_id),
        )
        if report is None:
            return None
        raw_hash = report.get("evidence_bundle_sha256")
        evidence_bundle_sha256 = str(raw_hash) if raw_hash is not None else None
    if (
        trigger == TRIGGER_AGENT
        and decision.pipeline_run_id is not None
        and not evidence_bundle_sha256
    ):
        return None
    if trigger == TRIGGER_AGENT and decision.pipeline_run_id is not None:
        if report is None:
            from app.db.mongo import get_mongo_db
            from app.services.decision_report_service import (
                load_decision_report_for_pipeline,
            )

            report = await load_decision_report_for_pipeline(
                get_mongo_db(), str(run_id), str(decision.pipeline_run_id)
            )
        report_hash = (report or {}).get("evidence_bundle_sha256")
        if not report_hash or (
            evidence_bundle_sha256 is not None
            and str(evidence_bundle_sha256) != str(report_hash)
        ):
            return None
        evidence_bundle_sha256 = str(report_hash)
        intelligence = (report or {}).get("decision_intelligence") or {}
        source = intelligence.get("release_decision") or {}
        if not source:
            return None
        recommendation = source.get("recommendation")
        risk_score = source.get("risk_score")
        blocking_issues = list(source.get("blocking_issues") or [])
        conditions_for_go = list(source.get("conditions_for_go") or [])
        synthesized = False
        human_override = None
    elif trigger == TRIGGER_OVERRIDE and override_snapshot is not None:
        recommendation = override_snapshot.get("recommendation")
        risk_score = override_snapshot.get("risk_score")
        blocking_issues = list(override_snapshot.get("blocking_issues") or [])
        conditions_for_go = list(override_snapshot.get("conditions_for_go") or [])
        synthesized = bool(override_snapshot.get("synthesized"))
        human_override = True
    else:
        council = await get_release_council(run_id, db)
        if council is None:
            return None
        recommendation = council.recommendation
        risk_score = council.risk_score
        blocking_issues = list(council.blocking_issues or [])
        conditions_for_go = list(council.conditions_for_go or [])
        synthesized = bool(council.synthesized)
        human_override = council.human_override
    payload: dict[str, Any] = {
        "run_id": str(run_id),
        "project_id": str(project_id),
        "pipeline_run_id": (
            str(decision.pipeline_run_id) if decision.pipeline_run_id is not None else None
        ),
        "evidence_bundle_sha256": evidence_bundle_sha256,
        "trigger": trigger,
        "recommendation": recommendation,
        "risk_score": risk_score,
        "blocking_issues": blocking_issues,
        "conditions_for_go": conditions_for_go,
        "synthesized": synthesized,
        "overridden": bool(human_override),
        "created_at": _iso(decision.created_at),
        "updated_at": override_audit_timestamp or _iso(decision.updated_at),
    }
    return await report_distribution_policy.gate_release_decided_payload(
        db,
        payload,
        run_id=run_id,
        synthesized=synthesized,
        human_override=human_override,
        pipeline_run_id=decision.pipeline_run_id,
        evidence_bundle_sha256=evidence_bundle_sha256,
        project_id=project_id,
    )


async def emit_release_decided(
    run_id: Any,
    *,
    trigger: str,
    pipeline_run_id: Any = None,
    evidence_bundle_sha256: str | None = None,
    override_ordinal: int | None = None,
    override_audit_timestamp: str | None = None,
    override_snapshot: dict[str, Any] | None = None,
) -> int:
    """Send ``release.decided`` for the committed decision on ``run_id``.

    Call only after the write's transaction has committed. Returns the number of
    deliveries staged. Never raises: a webhook must not fail the release gate.
    """
    if trigger not in _TRIGGERS:
        raise ValueError(f"unknown release.decided trigger: {trigger}")
    try:
        run_uuid = run_id if isinstance(run_id, uuid.UUID) else uuid.UUID(str(run_id))
        # Skip building the council view when webhooks are off, the default
        # (offline) install; emit_event would drop the event anyway.
        if not await webhook_service._post_allowed():
            return 0
        async with AsyncSessionLocal() as db:
            found = (
                await db.execute(
                    select(ReleaseDecision, TestRun.project_id)
                    .join(TestRun, TestRun.id == ReleaseDecision.test_run_id)
                    .where(ReleaseDecision.test_run_id == run_uuid)
                    .with_for_update()
                )
            ).first()
            if found is None:
                logger.warning(
                    "release_decided_decision_missing",
                    run_id=str(run_uuid),
                    trigger=trigger,
                )
                return 0
            decision, project_id = found
            if pipeline_run_id is not None and str(decision.pipeline_run_id) != str(
                pipeline_run_id
            ):
                return 0
            if trigger == TRIGGER_AGENT and decision.human_override is not None:
                return 0
            if trigger == TRIGGER_OVERRIDE:
                if (
                    override_ordinal is None
                    or override_ordinal < 1
                    or override_snapshot is None
                    or override_audit_timestamp is None
                ):
                    return 0
                audit = list(decision.override_audit or [])
                if override_ordinal > len(audit):
                    return 0
                committed_entry = audit[override_ordinal - 1]
                if str(committed_entry.get("timestamp")) != str(
                    override_audit_timestamp
                ):
                    return 0
            payload = await build_release_decided_payload(
                db,
                run_id=run_uuid,
                project_id=project_id,
                decision=decision,
                trigger=trigger,
                evidence_bundle_sha256=evidence_bundle_sha256,
                override_snapshot=override_snapshot,
                override_audit_timestamp=override_audit_timestamp,
            )
            scope = delivery_scope(
                decision,
                trigger,
                override_ordinal=override_ordinal,
            )
            if payload is None:
                return 0
            # Keep the decision row locked through durable staging. An override
            # that wins the lock first suppresses a delayed agent event; an
            # agent event that wins is staged before the later override event.
            return await webhook_service.emit_event(
                "release.decided",
                project_id=project_id,
                payload=payload,
                delivery_scope=scope,
            )
    except Exception as exc:  # noqa: BLE001 — the release gate must not fail on a webhook
        logger.warning(
            "release_decided_emit_failed",
            run_id=str(run_id),
            trigger=trigger,
            error_type=type(exc).__name__,
            error=str(exc),
        )
        return 0
