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

* ``run.completed``             — a test run finished post-ingestion
* ``defect.promoted``           — a failure cluster was promoted into a defect
* ``defect.create_requested``   — a user asked for a defect with target="webhook"
                                  (US-6.3 Jira-less fallback; payload carries the
                                  full prefilled ticket)
* ``release.decided``           — a ReleaseDecision was written (GO/NO_GO/CONDITIONAL)
* ``flaky.quarantined``         — a QA Lead approved a quarantine request
* ``quota.exceeded``            — the LLM cost budget hit its hard cap

Every public entry point is gated by the ``outbound_webhooks`` feature
flag AND by ``AI_OFFLINE_MODE``. Both off → egress goes through; either
on (flag off OR offline mode on) → the whole subsystem is a silent no-op.
"""
from __future__ import annotations

import asyncio
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
from app.models.postgres import Project, User, WebhookDelivery, WebhookSubscription
# SSRF guard lives in the shared ``url_safety`` module (reused by the URL
# knowledge connector). Aliased to the historical private name so the
# create/update/delivery call sites + their regression tests stay stable.
from app.services.url_safety import is_safe_public_url as _is_safe_public_url

logger = structlog.get_logger("services.webhook")


# ── Event catalog ──────────────────────────────────────────────────────────

_SUPPORTED_EVENTS: dict[str, str] = {
    "run.completed": "Fired after a test run has finished ingestion and post-processing.",
    "defect.promoted": "Fired when a failure cluster is promoted into a defect with a ticket link.",
    "defect.create_requested": (
        "Fired when a user files a one-click defect with target=\"webhook\" "
        "(US-6.3 — Jira-less fallback). data carries the full prefilled ticket: "
        "signature, summary, description, issue_type, test_name, suite_name, "
        "cluster_id, occurrences {first_seen, last_seen, failing_runs}, "
        "context {branch, build_number, ci_run_url}, ai_analysis "
        "{root_cause, confidence, failure_category} | null, deep_link, "
        "extra_comment, requested_by. Point a receiver at it to open tickets "
        "in GitHub Issues, Azure Boards, or anything else."
    ),
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


# ── SSRF guard ───────────────────────────────────────────────────────────────


async def _assert_safe_target_url(target_url: str) -> None:
    """Raise 422 when ``target_url`` is unsafe (SSRF guard at registration)."""
    safe, reason = await asyncio.to_thread(_is_safe_public_url, target_url)
    if not safe:
        from fastapi import HTTPException, status as _s
        raise HTTPException(
            status_code=_s.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Webhook target URL rejected: {reason}",
        )


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


def _has_retry_budget(attempt_count: int, max_retries: Optional[int]) -> bool:
    """Return whether another attempt is allowed without treating zero as unset."""
    attempt_limit = 5 if max_retries is None else max_retries
    return attempt_limit > 0 and attempt_count < attempt_limit


async def _mark_delivery_failed(
    db: AsyncSession,
    delivery: WebhookDelivery,
    error: str,
    subscription: Optional[WebhookSubscription] = None,
) -> None:
    """Persist a terminal delivery failure, with subscription diagnostics."""
    delivery.status = "FAILED"
    delivery.error = error
    if subscription is not None:
        subscription.last_failure_at = datetime.now(timezone.utc)
        subscription.last_error = error[:2000]
        subscription.failure_count = int(subscription.failure_count or 0) + 1
    await db.commit()


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
    await _assert_safe_target_url(target_url)  # SSRF guard
    # Project-existence guard. The router-level access check rejects ids
    # outside the caller's membership, but admins and stale UI sessions
    # can still target a deleted project — we'd otherwise blow up with
    # an opaque FK-violation 500 on commit after writing the secret +
    # audit row. Surface the actionable 404 up front.
    from fastapi import HTTPException
    project = await db.get(Project, project_id)
    if project is None:
        raise HTTPException(
            status_code=404,
            detail=f"Project {project_id} not found — refresh the page or pick a different project.",
        )
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
    # Flush so ``row.id`` (used to compute the secret key + audit row key)
    # is populated without committing. The caller (get_db dependency)
    # commits the subscription, has_secret flag, and any other pending
    # state in one atomic transaction. Audit is independent — see
    # ``_audit``, which opens its own session.
    await db.flush()

    if secret:
        from app.services import secret_service
        await secret_service.store_secret(
            db, SECRET_SCOPE, _secret_key(row.id), secret, actor.id,
        )
        row.has_secret = True

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
        await _assert_safe_target_url(target_url)  # SSRF guard
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

    # All mutations land in get_db's single commit at request end. Audit
    # is independent (own session via ``_audit``).
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
    # get_db commits at request end. Audit is independent (own session).


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


async def replay_delivery(
    delivery_id: uuid.UUID,
) -> Optional[uuid.UUID]:
    """Create a fresh PENDING delivery from a failed/DLQ'd one and enqueue it.

    Returns the new delivery's id, or ``None`` when:
    * The feature flag / offline-mode gate is off (no-op).
    * The source delivery is missing.
    * The source is not in a replayable state (``SUCCESS`` needs no
      replay, ``PENDING`` is still in-flight).

    A replay never mutates the original row — the old delivery stays
    in place so customers can audit "this failed, we retried, here's
    the new outcome". The new row gets a fresh ``attempt_count=0`` so
    the existing exponential-backoff retry policy applies to it from
    scratch.

    Service owns its own transaction boundary for the same reason
    ``emit_event`` does: the Celery task has to be enqueued *after*
    the delivery row is committed or the worker may look up a row
    that doesn't exist yet. See the COMMIT_ALLOWLIST entry for
    webhook_service.py.
    """
    if not await _post_allowed():
        return None

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(WebhookDelivery).where(WebhookDelivery.id == delivery_id)
        )
        original = result.scalar_one_or_none()
        if original is None:
            return None
        if original.status not in ("FAILED", "DLQ"):
            return None

        new_row = WebhookDelivery(
            subscription_id=original.subscription_id,
            event_type=original.event_type,
            event_payload=original.event_payload,
            status="PENDING",
            attempt_count=0,
        )
        db.add(new_row)
        await db.flush()
        new_id = new_row.id
        await db.commit()

    try:
        from app.worker.tasks import deliver_webhook as _deliver
        _deliver.delay(delivery_id=str(new_id))
    except Exception as exc:
        logger.warning(
            "webhook replay enqueue failed",
            delivery_id=str(new_id),
            error=str(exc),
        )
        try:
            async with AsyncSessionLocal() as db:
                result = await db.execute(
                    select(WebhookDelivery).where(WebhookDelivery.id == new_id)
                )
                failed_delivery = result.scalar_one_or_none()
                if failed_delivery is not None:
                    await _mark_delivery_failed(
                        db,
                        failed_delivery,
                        "webhook replay enqueue failed",
                    )
        except Exception as persist_exc:  # noqa: BLE001 — preserve original failure
            logger.warning(
                "webhook replay enqueue failure state could not be persisted",
                delivery_id=str(new_id),
                error=str(persist_exc),
            )
        return None

    logger.info(
        "webhook_delivery_replayed",
        original_delivery_id=str(delivery_id),
        new_delivery_id=str(new_id),
    )
    return new_id


async def _audit(
    db: AsyncSession, actor: User, action: str, row: WebhookSubscription,
) -> None:
    """Write a SettingsAuditLog row for a webhook-subscription change.

    Uses a fresh ``AsyncSessionLocal()`` (not the caller's ``db``) so:

    * Transient DB faults can be retried without poisoning the caller's
      session.
    * The audit row is durable across the primary mutation's transaction
      boundary, matching the historical webhook_service contract
      ("subscription persists even if audit fails"). The previous
      implementation achieved this via a double-commit in the caller —
      this version achieves it via session isolation, which is also the
      pattern adopted by ``flaky_quarantine_service._audit`` (P2-4).

    Never raises. Final failure emits a structured WARNING so dropped
    audit rows are greppable.
    """
    # ``db`` parameter retained for call-site compatibility.
    del db
    from app.db.postgres import AsyncSessionLocal
    from app.models.postgres import SettingsAuditLog
    from app.services.resilience import DB_RETRYABLE_EXCEPTIONS, async_retry

    entry_kwargs = dict(
        setting_key=f"webhook_subscription:{row.id}",
        action=action,
        actor_id=actor.id,
        actor_name=getattr(actor, "username", None) or getattr(actor, "email", None),
        changed_fields=["name", "target_url", "events", "enabled"],
    )

    async def _do_write() -> None:
        async with AsyncSessionLocal() as audit_db:
            try:
                audit_db.add(SettingsAuditLog(**entry_kwargs))
                await audit_db.commit()
            except Exception:
                await audit_db.rollback()
                raise

    try:
        await async_retry(
            _do_write,
            max_retries=2,
            base_delay=0.1,
            max_delay=2.0,
            retryable_exceptions=DB_RETRYABLE_EXCEPTIONS,
            operation_name="webhook_audit",
        )
    except Exception as exc:
        logger.warning(
            "webhook_audit_dropped",
            action=action,
            subscription_id=str(row.id),
            actor_id=str(actor.id),
            error_type=type(exc).__name__,
            error=str(exc),
        )


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

    try:
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
    except Exception as exc:  # noqa: BLE001 — webhook fan-out is best-effort
        logger.warning(
            "webhook emit persistence failed",
            event_type=event_type,
            project_id=str(pid),
            error_type=type(exc).__name__,
            error=str(exc),
        )
        return 0

    # Enqueue outside the session so celery errors don't roll back the
    # delivery rows — we'd rather have a PENDING row with no enqueued
    # task (visible as stuck) than silently drop an event.
    if deliveries:
        try:
            from app.worker.tasks import deliver_webhook as _deliver
        except Exception as exc:
            logger.warning("webhook dispatch failed", event_type=event_type, error=str(exc))
        else:
            for delivery_id, sub_id in deliveries:
                try:
                    _deliver.delay(delivery_id=str(delivery_id))
                except Exception as exc:
                    logger.warning(
                        "webhook delivery enqueue failed",
                        event_type=event_type,
                        delivery_id=str(delivery_id),
                        subscription_id=str(sub_id),
                        error=str(exc),
                    )

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
            await _mark_delivery_failed(
                db, delivery, "webhooks disabled or offline mode"
            )
            return {"error": "gated"}

        # SSRF guard at the egress boundary. create/update validate too, but a
        # hostname can rebind to an internal IP between registration and
        # delivery, and pre-existing rows predate the create-time check — so
        # re-validate here, where the actual request is made.
        safe, reason = await asyncio.to_thread(
            _is_safe_public_url, subscription.target_url
        )
        if not safe:
            delivery.status = "FAILED"
            delivery.error = f"blocked unsafe target URL: {reason}"
            await db.commit()
            from app.core.metrics import webhook_delivery_attempts_total
            webhook_delivery_attempts_total.labels(
                event_type=delivery.event_type,
                result="failure",
            ).inc()
            return {"error": "blocked_unsafe_target", "reason": reason}

        # Load HMAC secret, if configured.
        hmac_secret: Optional[str] = None
        if subscription.has_secret:
            from app.services import secret_service
            hmac_secret = await secret_service.read_secret(
                db, SECRET_SCOPE, _secret_key(subscription.id),
            )
            if not hmac_secret:
                await _mark_delivery_failed(
                    db,
                    delivery,
                    "configured signing secret unavailable",
                    subscription,
                )
                from app.core.metrics import webhook_delivery_attempts_total
                webhook_delivery_attempts_total.labels(
                    event_type=delivery.event_type,
                    result="failure",
                ).inc()
                return {"error": "signing_secret_unavailable"}

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
            should_retry = _has_retry_budget(
                delivery.attempt_count, subscription.max_retries
            )
            delivery.status = "PENDING" if should_retry else "DLQ"
            subscription.last_failure_at = datetime.now(timezone.utc)
            subscription.last_error = delivery.error[:2000]
            subscription.failure_count = int(subscription.failure_count or 0) + 1
            await db.commit()
            from app.core.metrics import webhook_delivery_attempts_total
            webhook_delivery_attempts_total.labels(
                event_type=delivery.event_type,
                result="retry" if should_retry else "failure",
            ).inc()
            return {"retry": should_retry, "error": str(exc)}

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

        if retryable and _has_retry_budget(
            delivery.attempt_count, subscription.max_retries
        ):
            delivery.status = "PENDING"  # Celery task will retry with backoff
            await db.commit()
            from app.core.metrics import webhook_delivery_attempts_total
            webhook_delivery_attempts_total.labels(
                event_type=delivery.event_type,
                result="retry",
            ).inc()
            return {"retry": True, "http_status": resp.status_code}

        delivery.status = "DLQ" if retryable else "FAILED"
        await db.commit()
        from app.core.metrics import webhook_delivery_attempts_total
        webhook_delivery_attempts_total.labels(
            event_type=delivery.event_type,
            result="failure",
        ).inc()
        return {"status": delivery.status, "http_status": resp.status_code}
