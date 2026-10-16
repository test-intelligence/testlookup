"""``defect.promoted``: the outbound webhook for a promoted defect taking effect (DEF-07).

``webhook_service.SUPPORTED_EVENTS`` offered ``defect.promoted`` and nothing
ever sent it. A promoted defect takes effect in two places, both only after
the request that made it effective has committed:

* the promote route (``POST /deep-investigate/{run}/clusters/{cluster}/promote``),
  for a promotion that needed no approval (``approved`` / ``executed`` at once);
* the review route (``POST /deep-investigate/defects/{id}/review``), when a QA
  lead approves a pending one, after its last commit (the Jira filing).

Only CLUSTER promotions count (D-12): a defect without a ``cluster_id`` (an
approved manual Jira request from ``/integrations/jira``) is not a promotion.

What the payload says
---------------------
The identity and the link, nothing else (§1.6): ``defect_id, project_id,
cluster_id, run_id, title, severity, jira_key, jira_url, approval_status,
deep_link, promoted_at``. No description, evidence, owner or reviewer: those
are free text that routinely names people and systems.

Delivery
--------
``emit_event`` stages one ``WebhookDelivery`` per matching subscription with
``delivery_key = sha256(f"{scope}:{subscription}:{event}")`` and
``ON CONFLICT DO NOTHING``, so :func:`delivery_scope` makes it exactly one
delivery per subscription per defect however often promote / approve re-run.
Offline (the default install) nothing is read: ``_post_allowed`` answers first.
The emitter reads the row in its OWN short session and leaves it before
``emit_event`` opens the next one, so it never needs two connections, and it
is called only after the router's commit returned the request's connection.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Optional

import structlog
from sqlalchemy import select

from app.core.config import settings
from app.db.postgres import AsyncSessionLocal
from app.models.postgres import Defect
from app.services import webhook_service

logger = structlog.get_logger("services.defect_promoted_webhook")

EVENT_TYPE = "defect.promoted"

#: The approval states in which a promoted defect is in effect.
EFFECTIVE_STATUSES = ("approved", "executed")

#: Exactly the keys a subscriber receives (§1.6).
PAYLOAD_KEYS = (
    "defect_id", "project_id", "cluster_id", "run_id", "title", "severity",
    "jira_key", "jira_url", "approval_status", "deep_link", "promoted_at",
)


def delivery_scope(defect_id: uuid.UUID | str) -> str:
    """The idempotency scope: one delivery per subscription per defect."""
    return f"defect-promoted:{defect_id}:v1"


def _iso_utc(value: Optional[datetime]) -> Optional[str]:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _status_word(value: Any) -> Optional[str]:
    word = getattr(value, "value", value)
    return str(word).lower() if word is not None else None


def build_payload(defect: Any, *, run_id: Optional[str], base_url: str) -> dict[str, Any]:
    """The ``data`` of a ``defect.promoted`` delivery. Pure."""
    return {
        "defect_id": str(defect.id),
        "project_id": str(defect.project_id),
        "cluster_id": defect.cluster_id,
        "run_id": str(run_id) if run_id else None,
        "title": defect.title,
        "severity": defect.severity,
        "jira_key": defect.jira_ticket_id or None,
        "jira_url": defect.jira_ticket_url or None,
        "approval_status": _status_word(defect.approval_status),
        "deep_link": f"{str(base_url).rstrip('/')}/defects?defect={defect.id}",
        "promoted_at": _iso_utc(getattr(defect, "approved_at", None) or getattr(defect, "created_at", None)),
    }


async def emit_defect_promoted(defect_id: uuid.UUID | str) -> int:
    """Send ``defect.promoted`` for a committed, effective cluster promotion.

    Call ONLY after the commit. Returns the deliveries staged; never raises:
    a webhook must not fail a promotion or an approval that already stands.
    """
    try:
        # Offline / flag off for everyone: no DB touched. emit_event asks
        # again for the defect's project before anything is staged.
        if not await webhook_service._post_allowed(any_scope=True):
            return 0
        wanted = defect_id if isinstance(defect_id, uuid.UUID) else uuid.UUID(str(defect_id))
        async with AsyncSessionLocal() as db:
            defect = (
                await db.execute(select(Defect).where(Defect.id == wanted))
            ).scalar_one_or_none()
            if defect is None or not defect.cluster_id:
                return 0
            if _status_word(defect.approval_status) not in EFFECTIVE_STATUSES:
                return 0
            run_id = (defect.policy_evaluation or {}).get("source_run_id")
            payload = build_payload(defect, run_id=run_id, base_url=settings.public_base_url)
            project_id = defect.project_id
        # The read session is closed here: emit_event opens its own.
        return await webhook_service.emit_event(
            "defect.promoted",
            project_id=project_id,
            payload=payload,
            delivery_scope=delivery_scope(wanted),
        )
    except Exception as exc:  # noqa: BLE001 — the promotion / approval stands regardless
        logger.warning(
            "defect_promoted_emit_failed",
            defect_id=str(defect_id),
            error_type=type(exc).__name__,
            error=str(exc)[:300],
        )
        return 0
