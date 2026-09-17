"""``release.decided``: the outbound webhook for a written release decision.

``webhook_service.SUPPORTED_EVENTS`` offered ``release.decided`` to subscribers,
and nothing ever sent it: a subscription was accepted and then never received a
delivery. A release decision is written in two places, and both emit AFTER their
transaction commits, so a receiver is never told about a decision that rolled back:

* ``trigger="agent"``: ``ReleaseRiskAgent._persist_decision`` inserted or
  rewrote the row at the end of a deep pipeline run;
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


def delivery_scope(decision: ReleaseDecision, trigger: str) -> str:
    """The idempotency scope for one decision write."""
    if trigger == TRIGGER_OVERRIDE:
        marker = f"override:{len(decision.override_audit or [])}"
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
) -> Optional[dict[str, Any]]:
    council = await get_release_council(run_id, db)
    if council is None:
        return None
    if decision.pipeline_run_id is not None and evidence_bundle_sha256 is None:
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
    if decision.pipeline_run_id is not None and not evidence_bundle_sha256:
        return None
    payload: dict[str, Any] = {
        "run_id": str(run_id),
        "project_id": str(project_id),
        "pipeline_run_id": (
            str(decision.pipeline_run_id) if decision.pipeline_run_id is not None else None
        ),
        "evidence_bundle_sha256": evidence_bundle_sha256,
        "trigger": trigger,
        "recommendation": council.recommendation,
        "risk_score": council.risk_score,
        "blocking_issues": list(council.blocking_issues or []),
        "conditions_for_go": list(council.conditions_for_go or []),
        "synthesized": bool(council.synthesized),
        "overridden": bool(council.human_override),
        "created_at": _iso(decision.created_at),
        "updated_at": _iso(decision.updated_at),
    }
    return await report_distribution_policy.gate_release_decided_payload(
        db,
        payload,
        run_id=run_id,
        synthesized=bool(council.synthesized),
        human_override=council.human_override,
        pipeline_run_id=decision.pipeline_run_id,
        evidence_bundle_sha256=evidence_bundle_sha256,
        project_id=project_id,
    )


async def emit_release_decided(
    run_id: Any,
    *,
    trigger: str,
    evidence_bundle_sha256: str | None = None,
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
            payload = await build_release_decided_payload(
                db,
                run_id=run_uuid,
                project_id=project_id,
                decision=decision,
                trigger=trigger,
                evidence_bundle_sha256=evidence_bundle_sha256,
            )
            scope = delivery_scope(decision, trigger)
        if payload is None:
            return 0
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
