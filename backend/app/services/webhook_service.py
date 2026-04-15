"""
Outbound webhooks — Tier 2 item 6.

Customer-managed event subscriptions. When TestLookup fires a supported
event, the service scans ``webhook_subscriptions`` for matching rows
(project + event type + enabled + flag/offline gates), then enqueues
one Celery delivery task per subscription.

Deliveries are HMAC-SHA256 signed using a per-subscription secret that
lives in ``secret_service``. Every attempt lands in ``webhook_deliveries``
so customers can see delivery history + failure reason from the Settings
UI. Retries use exponential backoff; the final outcome lands on a single
``WebhookDelivery`` row so the audit trail stays compact.

Supported event types (the authoritative list — keep in sync with the
``WebhookEventCatalogResponse`` shown in the UI):

* ``run.completed``       — a test run finished post-ingestion
* ``defect.promoted``     — a failure cluster was promoted into a defect
* ``release.decided``     — a ReleaseDecision was written (GO/NO_GO/CONDITIONAL)
* ``flaky.quarantined``   — a QA Lead approved a quarantine request
* ``quota.exceeded``      — the LLM cost budget hit its hard cap

Every public entry point is gated by the ``outbound_webhooks`` feature
flag AND by ``AI_OFFLINE_MODE``. Both off → egress goes through; either
on (flag off OR offline mode on) → the whole subsystem is a silent no-op.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

import httpx
import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.postgres import AsyncSessionLocal
from app.models.postgres import User, WebhookDelivery, WebhookSubscription

logger = structlog.get_logger("services.webhook")


# ── Event catalog ──────────────────────────────────────────────────────────

_SUPPORTED_EVENTS: dict[str, str] = {
    "run.completed": "Fired after a test run has finished ingestion and post-processing.",
    "defect.promoted": "Fired when a failure cluster is promoted into a defect with a ticket link.",
    "release.decided": "Fired when a ReleaseDecision is written (GO/NO_GO/CONDITIONAL_GO).",
    "flaky.quarantined": "Fired when a QA Lead approves a flaky-test quarantine.",
    "quota.exceeded": "Fired when the LLM cost budget hits its hard cap for a project.",
}

# Secret service scope for HMAC keys.
SECRET_SCOPE = "webhook_subscription"


def list_supported_events() -> list[dict[str, str]]:
    return [
        {"event_type": k, "description": v} for k, v in sorted(_SUPPORTED_EVENTS.items())
    ]


def validate_events(events: list[str]) -> list[str]:
    """Return the subset of events that are supported. Raises if any are unknown."""
    unknown = [e for e in events if e not in _SUPPORTED_EVENTS]
    if unknown:
        from fastapi import HTTPException, status as _s
        raise HTTPException(
            status_code=_s.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"Unknown event types: {', '.join(unknown)}. "
                f"Supported: {', '.join(sorted(_SUPPORTED_EVENTS))}"
            ),
        )
    return events


def _secret_key(subscription_id: uuid.UUID | str) -> str:
    return f"subscription:{subscription_id}:hmac"


# ── Feature-flag / offline gates ────────────────────────────────────────────


async def _post_allowed() -> bool:
    """``AI_OFFLINE_MODE`` is the hard kill switch — even with the feature
    flag on, an air-gapped deployment never egresses traffic."""
    if settings.AI_OFFLINE_MODE:
        return False
    try:
        from app.services.feature_flags import is_enabled
        return await is_enabled("outbound_webhooks")
    except Exception as exc:
        logger.debug("outbound_webhooks flag check failed", error=str(exc))
        return False


# ── HMAC signing ───────────────────────────────────────────────────────────


def compute_signature(secret: str, body: bytes) -> str:
    """Stable HMAC-SHA256 hex digest used in the ``X-TestLookup-Signature``
    header. Customers verify with::

        import hmac, hashlib
        expected = hmac.new(SECRET, request.body, hashlib.sha256).hexdigest()
        assert hmac.compare_digest(expected, request.headers["X-TestLookup-Signature"])
    """
    return hmac.new(
        secret.encode("utf-8"),
        body,
        hashlib.sha256,
    ).hexdigest()


# ── Subscription CRUD ──────────────────────────────────────────────────────


async def get_subscription(
    db: AsyncSession, subscription_id: uuid.UUID,
) -> Optional[WebhookSubscription]:
    result = await db.execute(
        select(WebhookSubscription).where(WebhookSubscription.id == subscription_id)
    )
    return result.scalar_one_or_none()


async def list_subscriptions(
    db: AsyncSession,
    *,
    project_id: Optional[uuid.UUID] = None,
    project_ids: Optional[set[uuid.UUID]] = None,
) -> list[WebhookSubscription]:
    stmt = select(WebhookSubscription)
    if project_id is not None:
        stmt = stmt.where(WebhookSubscription.project_id == project_id)
    elif project_ids is not None:
        if not project_ids:
            return []
        stmt = stmt.where(WebhookSubscription.project_id.in_(project_ids))
    stmt = stmt.order_by(WebhookSubscription.created_at.desc())
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def create_subscription(
    db: AsyncSession,
    *,
    project_id: uuid.UUID,
    actor: User,
    name: str,
    target_url: str,
    events: list[str],
    enabled: bool,
    max_retries: int,
    secret: Optional[str],
) -> WebhookSubscription:
    validate_events(events)
    row = WebhookSubscription(
        project_id=project_id,
        name=name,
        target_url=target_url,
        events=list(events),
        enabled=enabled,
        max_retries=max_retries,
        updated_by_user_id=actor.id,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)

    if secret:
        from app.services import secret_service
        await secret_service.store_secret(
            db, SECRET_SCOPE, _secret_key(row.id), secret, actor.id,
        )
        row.has_secret = True
        await db.commit()

    await _audit(db, actor, "create", row)
    return row


async def update_subscription(
    db: AsyncSession,
    *,
    subscription_id: uuid.UUID,
    actor: User,
    name: Optional[str],
    target_url: Optional[str],
    events: Optional[list[str]],
    enabled: Optional[bool],
    max_retries: Optional[int],
    secret: Optional[str],
) -> WebhookSubscription:
    from fastapi import HTTPException, status as _s
    row = await get_subscription(db, subscription_id)
    if row is None:
        raise HTTPException(status_code=_s.HTTP_404_NOT_FOUND, detail="Subscription not found")

    if name is not None:
        row.name = name
    if target_url is not None:
        row.target_url = target_url
    if events is not None:
        validate_events(events)
        row.events = list(events)
    if enabled is not None:
        row.enabled = enabled
    if max_retries is not None:
        row.max_retries = max_retries
    row.updated_by_user_id = actor.id
    row.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(row)

    if secret is not None:
        from app.services import secret_service
        if secret == "":
            await secret_service.store_secret(
                db, SECRET_SCOPE, _secret_key(row.id), "", actor.id,
            )
            row.has_secret = False
        else:
            await secret_service.store_secret(
                db, SECRET_SCOPE, _secret_key(row.id), secret, actor.id,
            )
            row.has_secret = True
        await db.commit()
        await db.refresh(row)

    await _audit(db, actor, "update", row)
    return row


async def delete_subscription(
    db: AsyncSession, subscription_id: uuid.UUID, actor: User,
) -> None:
    from fastapi import HTTPException, status as _s
    row = await get_subscription(db, subscription_id)
    if row is None:
        raise HTTPException(status_code=_s.HTTP_404_NOT_FOUND, detail="Subscription not found")
    await _audit(db, actor, "delete", row)
    await db.delete(row)
    await db.commit()


async def list_deliveries(
    db: AsyncSession,
    subscription_id: uuid.UUID,
    *,
    limit: int = 50,
) -> list[WebhookDelivery]:
    result = await db.execute(
        select(WebhookDelivery)
        .where(WebhookDelivery.subscription_id == subscription_id)
        .order_by(WebhookDelivery.created_at.desc())
        .limit(min(max(1, limit), 200))
    )
    return list(result.scalars().all())


async def _audit(
    db: AsyncSession, actor: User, action: str, row: WebhookSubscription,
) -> None:
    try:
        from app.models.postgres import SettingsAuditLog
        entry = SettingsAuditLog(
            setting_key=f"webhook_subscription:{row.id}",
            action=action,
            actor_id=actor.id,
            actor_name=getattr(actor, "username", None) or getattr(actor, "email", None),
            changed_fields=["name", "target_url", "events", "enabled"],
        )
        db.add(entry)
        await db.commit()
    except Exception as exc:
        logger.warning("webhook audit log failed", error=str(exc))


# ── Emission ───────────────────────────────────────────────────────────────


async def emit_event(
    event_type: str,
    *,
    project_id: uuid.UUID | str,
    payload: dict[str, Any],
) -> int:
    """Public trigger — called from the pipeline at each event boundary.

    Scans active subscriptions for this project + event type and enqueues
    a Celery delivery task for each match. Returns the number of
    subscriptions matched so callers can log telemetry.

    Gated by ``_post_allowed``: when the feature flag is off or
    ``AI_OFFLINE_MODE`` is on, returns 0 immediately without touching
    the DB. Never raises — webhook fan-out is best-effort and must not
    block the operation that triggered it.
    """
    if event_type not in _SUPPORTED_EVENTS:
        logger.debug("emit_event called with unknown event", event_type=event_type)
        return 0
    if not await _post_allowed():
        return 0

    try:
        pid = project_id if isinstance(project_id, uuid.UUID) else uuid.UUID(str(project_id))
    except (TypeError, ValueError):
        return 0

    matched: list[WebhookSubscription] = []
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(WebhookSubscription).where(
                WebhookSubscription.project_id == pid,
                WebhookSubscription.enabled.is_(True),
            )
        )
        for sub in result.scalars().all():
            events = sub.events or []
            if event_type in events:
                matched.append(sub)

        # Create one pending delivery row per match before enqueueing so the
        # celery task has a stable id to update. Under Celery backpressure,
        # rows may sit at PENDING for a few seconds — that's fine.
        deliveries: list[tuple[uuid.UUID, uuid.UUID]] = []
        for sub in matched:
            delivery = WebhookDelivery(
                subscription_id=sub.id,
                event_type=event_type,
                event_payload=payload,
                status="PENDING",
                attempt_count=0,
            )
            db.add(delivery)
            await db.flush()
            deliveries.append((delivery.id, sub.id))
        if deliveries:
            await db.commit()

    # Enqueue outside the session so celery errors don't roll back the
    # delivery rows — we'd rather have a PENDING row with no enqueued
    # task (visible as stuck) than silently drop an event.
    if deliveries:
        try:
            from app.worker.tasks import deliver_webhook as _deliver
            for delivery_id, _sub_id in deliveries:
                _deliver.delay(delivery_id=str(delivery_id))
        except Exception as exc:
            logger.warning("webhook dispatch failed", event_type=event_type, error=str(exc))

    logger.info(
        "webhook emit",
        event_type=event_type,
        project_id=str(pid),
        subscription_count=len(deliveries),
    )
    return len(deliveries)


# ── Delivery (called from celery worker) ────────────────────────────────────


async def deliver(delivery_id: uuid.UUID) -> dict[str, Any]:
    """Perform a single delivery attempt for a ``WebhookDelivery`` row.

    This is called from the ``deliver_webhook`` Celery task. Returns a
    dict describing the outcome so the task can decide whether to retry.
    Never raises.

    The row is the single source of truth for outcome — every attempt
    updates the same row's ``attempt_count`` and ``status`` so customers
    see one row per emission in the UI.
    """
    async with AsyncSessionLocal() as db:
        delivery = (
            await db.execute(
                select(WebhookDelivery).where(WebhookDelivery.id == delivery_id)
            )
        ).scalar_one_or_none()
        if delivery is None:
            return {"skipped": "delivery_row_missing"}

        subscription = (
            await db.execute(
                select(WebhookSubscription).where(
                    WebhookSubscription.id == delivery.subscription_id
                )
            )
        ).scalar_one_or_none()
        if subscription is None or not subscription.enabled:
            delivery.status = "FAILED"
            delivery.error = "subscription missing or disabled"
            await db.commit()
            return {"error": "subscription missing or disabled"}

        # Feature flag / offline re-check — an admin may have disabled
        # webhooks between enqueue and delivery.
        if not await _post_allowed():
            delivery.status = "FAILED"
            delivery.error = "webhooks disabled or offline mode"
            await db.commit()
            return {"error": "gated"}

        # Load HMAC secret, if configured.
        hmac_secret: Optional[str] = None
        if subscription.has_secret:
            from app.services import secret_service
            hmac_secret = await secret_service.read_secret(
                db, SECRET_SCOPE, _secret_key(subscription.id),
            )

        # Build the body and sign.
        envelope = {
            "event_type": delivery.event_type,
            "delivery_id": str(delivery.id),
            "subscription_id": str(subscription.id),
            "project_id": str(subscription.project_id),
            "emitted_at": datetime.now(timezone.utc).isoformat(),
            "data": delivery.event_payload or {},
        }
        body = json.dumps(envelope, default=str).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "TestLookup-Webhook/1.0",
            "X-TestLookup-Event": delivery.event_type,
            "X-TestLookup-Delivery": str(delivery.id),
        }
        if hmac_secret:
            headers["X-TestLookup-Signature"] = compute_signature(hmac_secret, body)

        delivery.attempt_count = int(delivery.attempt_count or 0) + 1

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(
                    subscription.target_url,
                    content=body,
                    headers=headers,
                )
        except Exception as exc:
            delivery.error = f"{type(exc).__name__}: {str(exc)[:1800]}"
            delivery.status = (
                "DLQ"
                if delivery.attempt_count >= (subscription.max_retries or 5)
                else "PENDING"
            )
            subscription.last_failure_at = datetime.now(timezone.utc)
            subscription.last_error = delivery.error[:2000]
            subscription.failure_count = int(subscription.failure_count or 0) + 1
            await db.commit()
            from app.core.metrics import webhook_delivery_attempts_total
            webhook_delivery_attempts_total.labels(
                event_type=delivery.event_type,
                result="retry" if delivery.status == "PENDING" else "failure",
            ).inc()
            return {"retry": delivery.status == "PENDING", "error": str(exc)}

        delivery.http_status = resp.status_code
        delivery.response_preview = (resp.text or "")[:2000]

        if 200 <= resp.status_code < 300:
            delivery.status = "SUCCESS"
            delivery.delivered_at = datetime.now(timezone.utc)
            delivery.error = None
            subscription.last_delivered_at = delivery.delivered_at
            subscription.last_error = None
            subscription.last_failure_at = None
            subscription.total_delivered = int(subscription.total_delivered or 0) + 1
            await db.commit()
            from app.core.metrics import webhook_delivery_attempts_total
            webhook_delivery_attempts_total.labels(
                event_type=delivery.event_type,
                result="success",
            ).inc()
            return {"status": "SUCCESS", "http_status": resp.status_code}

        # Non-2xx — retry on 5xx/429, mark FAILED on 4xx (customer bug).
        retryable = resp.status_code in (408, 425, 429, 500, 502, 503, 504)
        delivery.error = f"HTTP {resp.status_code}: {delivery.response_preview[:200]}"
        subscription.last_failure_at = datetime.now(timezone.utc)
        subscription.last_error = delivery.error[:2000]
        subscription.failure_count = int(subscription.failure_count or 0) + 1

        if retryable and delivery.attempt_count < (subscription.max_retries or 5):
            delivery.status = "PENDING"  # Celery task will retry with backoff
            await db.commit()
            from app.core.metrics import webhook_delivery_attempts_total
            webhook_delivery_attempts_total.labels(
                event_type=delivery.event_type,
                result="retry",
            ).inc()
            return {"retry": True, "http_status": resp.status_code}

        delivery.status = "DLQ" if delivery.attempt_count >= (subscription.max_retries or 5) else "FAILED"
        await db.commit()
        from app.core.metrics import webhook_delivery_attempts_total
        webhook_delivery_attempts_total.labels(
            event_type=delivery.event_type,
            result="failure",
        ).inc()
        return {"status": delivery.status, "http_status": resp.status_code}
