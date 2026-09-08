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
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import httpx
import structlog
from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.postgres import AsyncSessionLocal
from app.models.postgres import Project, User, WebhookDelivery, WebhookSubscription
# SSRF guard lives in the shared ``url_safety`` module (reused by the URL
# knowledge connector). Aliased to the historical private name so the
# create/update/delivery call sites + their regression tests stay stable.
from app.services.url_safety import is_safe_public_url as _is_safe_public_url

logger = structlog.get_logger("services.webhook")

_MAX_WEBHOOK_DISPATCH_ATTEMPTS = 8


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


class WebhookGateLookupError(RuntimeError):
    """The outbound-webhook feature gate could not be resolved."""


async def _post_allowed(*, raise_on_lookup_error: bool = False) -> bool:
    """``AI_OFFLINE_MODE`` is the hard kill switch — even with the feature
    flag on, an air-gapped deployment never egresses traffic."""
    if settings.AI_OFFLINE_MODE:
        return False
    try:
        from app.services.feature_flags import is_enabled
        return await is_enabled("outbound_webhooks")
    except Exception as exc:
        logger.debug("outbound_webhooks flag check failed", error=str(exc))
        if raise_on_lookup_error:
            raise WebhookGateLookupError(
                "outbound_webhooks feature flag lookup failed"
            ) from exc
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
    *,
    delivery_id: uuid.UUID,
    dispatch_token: uuid.UUID,
    error: str,
    subscription_id: uuid.UUID | None = None,
) -> bool:
    """Token-fence a terminal failure and its subscription diagnostics."""
    now_failed = datetime.now(timezone.utc)
    return await _transition_processing_delivery(
        db,
        delivery_id=delivery_id,
        dispatch_token=dispatch_token,
        delivery_values={
            "status": "FAILED",
            "error": error,
            "dispatch_token": None,
            "dispatch_lease_expires_at": None,
            "next_dispatch_at": None,
        },
        subscription_id=subscription_id,
        subscription_values=(
            {
                "last_failure_at": now_failed,
                "last_error": error[:2000],
                "failure_count": WebhookSubscription.failure_count + 1,
            }
            if subscription_id is not None
            else None
        ),
    )


async def _transition_processing_delivery(
    db: AsyncSession,
    *,
    delivery_id: uuid.UUID,
    dispatch_token: uuid.UUID,
    delivery_values: dict[str, Any],
    subscription_id: uuid.UUID | None = None,
    subscription_values: dict[str, Any] | None = None,
) -> bool:
    """Commit an attempt outcome only while this worker still owns its lease.

    Provider I/O happens without a database lock. A relay can reclaim the row
    after the processing lease expires, so every outcome must compare the
    original token as well as the PROCESSING state. Subscription counters are
    updated in the same transaction only when that compare-and-set succeeds.
    """
    result = await db.execute(
        update(WebhookDelivery)
        .where(
            WebhookDelivery.id == delivery_id,
            WebhookDelivery.status == "PROCESSING",
            WebhookDelivery.dispatch_token == dispatch_token,
        )
        .values(**delivery_values)
    )
    if int(getattr(result, "rowcount", 0) or 0) != 1:
        await db.rollback()
        return False
    if subscription_id is not None and subscription_values:
        await db.execute(
            update(WebhookSubscription)
            .where(WebhookSubscription.id == subscription_id)
            .values(**subscription_values)
        )
    await db.commit()
    return True


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
            run_id=getattr(original, "run_id", None),
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
        # The committed PENDING row is the recovery contract. The periodic
        # relay will publish it after the broker recovers.
        return new_id

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
    delivery_scope: str | None = None,
    raise_on_persistence_error: bool = False,
) -> int:
    """Public trigger — called from the pipeline at each event boundary.

    Scans active subscriptions for this project + event type and enqueues
    a Celery delivery task for each match. Returns the number of
    subscriptions matched so callers can log telemetry.

    Gated by ``_post_allowed``: when the feature flag is off or
    ``AI_OFFLINE_MODE`` is on, returns 0 immediately without touching
    the DB. Legacy callers remain best-effort. Durable outbox consumers pass
    ``raise_on_persistence_error=True`` so PostgreSQL can retry a failed fan-out.
    """
    if event_type not in _SUPPORTED_EVENTS:
        logger.debug("emit_event called with unknown event", event_type=event_type)
        return 0
    if not await _post_allowed(
        raise_on_lookup_error=raise_on_persistence_error
    ):
        return 0

    try:
        pid = project_id if isinstance(project_id, uuid.UUID) else uuid.UUID(str(project_id))
    except (TypeError, ValueError):
        return 0
    event_run_id: uuid.UUID | None = None
    if event_type == "run.completed":
        try:
            event_run_id = uuid.UUID(str(payload.get("run_id")))
        except (TypeError, ValueError, AttributeError):
            event_run_id = None

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
            if delivery_scope and matched:
                values = []
                for sub in matched:
                    material = f"{delivery_scope}:{sub.id}:{event_type}"
                    values.append(
                        {
                            "id": uuid.uuid4(),
                            "subscription_id": sub.id,
                            "run_id": event_run_id,
                            "event_type": event_type,
                            "event_payload": payload,
                            "delivery_key": hashlib.sha256(
                                material.encode("utf-8")
                            ).hexdigest(),
                            "status": "PENDING",
                            "attempt_count": 0,
                            "dispatch_attempts": 0,
                            "dispatch_failures": 0,
                        }
                    )
                inserted = await db.execute(
                    pg_insert(WebhookDelivery)
                    .values(values)
                    .on_conflict_do_nothing(
                        index_elements=[WebhookDelivery.delivery_key]
                    )
                    .returning(WebhookDelivery.id, WebhookDelivery.subscription_id)
                )
                deliveries = [(row.id, row.subscription_id) for row in inserted.all()]
            else:
                for sub in matched:
                    delivery = WebhookDelivery(
                        subscription_id=sub.id,
                        run_id=event_run_id,
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
        if raise_on_persistence_error:
            raise
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


async def claim_pending_webhook_dispatches(
    db: AsyncSession, *, limit: int = 200, lease_seconds: int = 120
) -> list[WebhookDelivery]:
    """Lease webhook rows whose original broker publication was lost."""
    now = datetime.now(timezone.utc)
    bounded_limit = max(1, min(int(limit), 500))
    due_filter = or_(
        and_(
            WebhookDelivery.status == "PENDING",
            or_(
                WebhookDelivery.next_dispatch_at.is_(None),
                WebhookDelivery.next_dispatch_at <= now,
            ),
        ),
        and_(
            WebhookDelivery.status.in_({"SENDING", "PROCESSING"}),
            WebhookDelivery.dispatch_lease_expires_at <= now,
        ),
    )
    ranked_due = (
        select(
            WebhookDelivery.id.label("id"),
            func.row_number()
            .over(
                partition_by=WebhookSubscription.project_id,
                order_by=(WebhookDelivery.created_at, WebhookDelivery.id),
            )
            .label("tenant_rank"),
        )
        .join(
            WebhookSubscription,
            WebhookSubscription.id == WebhookDelivery.subscription_id,
        )
        .where(due_filter)
        .cte("ranked_webhook_due")
    )
    result = await db.execute(
        select(WebhookDelivery)
        .join(ranked_due, ranked_due.c.id == WebhookDelivery.id)
        .order_by(
            ranked_due.c.tenant_rank,
            WebhookDelivery.created_at,
            WebhookDelivery.id,
        )
        .with_for_update(skip_locked=True, of=WebhookDelivery)
        .limit(bounded_limit)
    )
    claimed: list[WebhookDelivery] = []
    for row in result.scalars().all():
        due = row.next_dispatch_at is None or row.next_dispatch_at <= now
        expired = (
            row.dispatch_lease_expires_at is not None
            and row.dispatch_lease_expires_at <= now
        )
        if not (
            (row.status == "PENDING" and due)
            or (row.status in {"SENDING", "PROCESSING"} and expired)
        ):
            continue
        if (
            row.status == "PENDING"
            and int(row.dispatch_failures or 0) >= _MAX_WEBHOOK_DISPATCH_ATTEMPTS
        ):
            row.status = "FAILED"
            row.error = row.error or "webhook dispatch attempts exhausted"
            row.dispatch_lease_expires_at = None
            row.next_dispatch_at = None
            continue
        previous_status = row.status
        row.status = "SENDING"
        row.dispatch_attempts = int(row.dispatch_attempts or 0) + 1
        # Preserve a published message's token across queue-wait recovery so
        # either the original or republished copy remains executable. A worker
        # whose processing lease expired is fenced with a new token.
        if previous_status == "PROCESSING" or row.dispatch_token is None:
            row.dispatch_token = uuid.uuid4()
        row.dispatch_lease_expires_at = now + timedelta(
            seconds=max(10, min(int(lease_seconds), 900))
        )
        row.next_dispatch_at = row.dispatch_lease_expires_at
        claimed.append(row)
    return claimed


async def relay_pending_webhook_deliveries(*, limit: int = 200) -> dict[str, int]:
    """Recover committed webhook rows that have no live broker delivery."""
    async with AsyncSessionLocal() as db:
        rows = await claim_pending_webhook_dispatches(db, limit=limit)
        await db.commit()

    published = failed = 0
    from app.worker.tasks import deliver_webhook as _deliver

    for row in rows:
        token = row.dispatch_token
        if token is None:
            failed += 1
            continue
        try:
            _deliver.apply_async(
                kwargs={
                    "delivery_id": str(row.id),
                    "dispatch_token": str(token),
                },
                queue="default",
                task_id=f"webhook-delivery-{row.id}",
            )
            published += 1
        except Exception as exc:  # noqa: BLE001
            failed += 1
            async with AsyncSessionLocal() as db:
                locked = (
                    await db.execute(
                        select(WebhookDelivery)
                        .where(WebhookDelivery.id == row.id)
                        .with_for_update()
                    )
                ).scalar_one_or_none()
                if (
                    locked is not None
                    and locked.status == "SENDING"
                    and locked.dispatch_token == token
                ):
                    locked.dispatch_failures = int(locked.dispatch_failures or 0) + 1
                    exhausted = locked.dispatch_failures >= _MAX_WEBHOOK_DISPATCH_ATTEMPTS
                    locked.status = "FAILED" if exhausted else "PENDING"
                    locked.error = f"broker_{type(exc).__name__}"
                    locked.dispatch_lease_expires_at = None
                    locked.next_dispatch_at = None if exhausted else datetime.now(timezone.utc) + timedelta(
                        seconds=min(300, 2 ** min(int(locked.dispatch_attempts or 0), 8))
                    )
                    await db.commit()
    return {"claimed": len(rows), "published": published, "failed": failed}


async def deliver(
    delivery_id: uuid.UUID,
    *,
    dispatch_token: uuid.UUID | None = None,
) -> dict[str, Any]:
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
                select(WebhookDelivery)
                .where(WebhookDelivery.id == delivery_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if delivery is None:
            return {"skipped": "delivery_row_missing"}
        if delivery.status in {"SUCCESS", "FAILED", "DLQ"}:
            return {"skipped": "delivery_already_terminal"}
        if dispatch_token is None:
            if delivery.status != "PENDING":
                return {"skipped": "delivery_already_claimed"}
            delivery.status = "PROCESSING"
            delivery.dispatch_attempts = int(
                getattr(delivery, "dispatch_attempts", 0) or 0
            ) + 1
            delivery.dispatch_token = uuid.uuid4()
        elif (
            delivery.status != "SENDING"
            or delivery.dispatch_token != dispatch_token
        ):
            return {"skipped": "stale_dispatch_token"}
        else:
            delivery.status = "PROCESSING"
        delivery.dispatch_lease_expires_at = datetime.now(timezone.utc) + timedelta(
            seconds=900
        )
        active_dispatch_token = delivery.dispatch_token
        if active_dispatch_token is None:  # defensive: every claimed row owns a token
            return {"skipped": "missing_dispatch_token"}
        await db.commit()

        subscription = (
            await db.execute(
                select(WebhookSubscription).where(
                    WebhookSubscription.id == delivery.subscription_id
                )
            )
        ).scalar_one_or_none()
        if subscription is None or not subscription.enabled:
            changed = await _mark_delivery_failed(
                db,
                delivery_id=delivery.id,
                dispatch_token=active_dispatch_token,
                error="subscription missing or disabled",
            )
            if not changed:
                return {"skipped": "stale_dispatch_token"}
            return {"error": "subscription missing or disabled"}

        # Feature flag / offline re-check — an admin may have disabled
        # webhooks between enqueue and delivery.
        try:
            post_allowed = await _post_allowed(raise_on_lookup_error=True)
        except WebhookGateLookupError as exc:
            retry_at = datetime.now(timezone.utc) + timedelta(seconds=30)
            changed = await _transition_processing_delivery(
                db,
                delivery_id=delivery.id,
                dispatch_token=active_dispatch_token,
                delivery_values={
                    "status": "PENDING",
                    "error": str(exc),
                    "dispatch_token": None,
                    "dispatch_lease_expires_at": None,
                    "next_dispatch_at": retry_at,
                },
            )
            if not changed:
                return {"skipped": "stale_dispatch_token"}
            return {"retry": True, "error": str(exc)}
        if not post_allowed:
            changed = await _mark_delivery_failed(
                db,
                delivery_id=delivery.id,
                dispatch_token=active_dispatch_token,
                error="webhooks disabled or offline mode",
            )
            if not changed:
                return {"skipped": "stale_dispatch_token"}
            return {"error": "gated"}

        # SSRF guard at the egress boundary. create/update validate too, but a
        # hostname can rebind to an internal IP between registration and
        # delivery, and pre-existing rows predate the create-time check — so
        # re-validate here, where the actual request is made.
        safe, reason = await asyncio.to_thread(
            _is_safe_public_url, subscription.target_url
        )
        if not safe:
            changed = await _mark_delivery_failed(
                db,
                delivery_id=delivery.id,
                dispatch_token=active_dispatch_token,
                error=f"blocked unsafe target URL: {reason}",
                subscription_id=subscription.id,
            )
            if not changed:
                return {"skipped": "stale_dispatch_token"}
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
                changed = await _mark_delivery_failed(
                    db,
                    delivery_id=delivery.id,
                    dispatch_token=active_dispatch_token,
                    error="configured signing secret unavailable",
                    subscription_id=subscription.id,
                )
                if not changed:
                    return {"skipped": "stale_dispatch_token"}
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

        attempt_count = int(delivery.attempt_count or 0) + 1
        if not await _transition_processing_delivery(
            db,
            delivery_id=delivery.id,
            dispatch_token=active_dispatch_token,
            delivery_values={"attempt_count": attempt_count},
        ):
            return {"skipped": "stale_dispatch_token"}

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(
                    subscription.target_url,
                    content=body,
                    headers=headers,
                )
        except Exception as exc:
            error = f"{type(exc).__name__}: {str(exc)[:1800]}"
            should_retry = _has_retry_budget(attempt_count, subscription.max_retries)
            now_failed = datetime.now(timezone.utc)
            next_dispatch_at = (
                now_failed
                + timedelta(seconds=min(480, 30 * (2 ** max(attempt_count - 1, 0))))
                if should_retry
                else None
            )
            changed = await _transition_processing_delivery(
                db,
                delivery_id=delivery.id,
                dispatch_token=active_dispatch_token,
                delivery_values={
                    "status": "PENDING" if should_retry else "DLQ",
                    "error": error,
                    "dispatch_token": None,
                    "dispatch_lease_expires_at": None,
                    "next_dispatch_at": next_dispatch_at,
                },
                subscription_id=subscription.id,
                subscription_values={
                    "last_failure_at": now_failed,
                    "last_error": error[:2000],
                    "failure_count": WebhookSubscription.failure_count + 1,
                },
            )
            if not changed:
                return {"skipped": "stale_dispatch_token"}
            from app.core.metrics import webhook_delivery_attempts_total
            webhook_delivery_attempts_total.labels(
                event_type=delivery.event_type,
                result="retry" if should_retry else "failure",
            ).inc()
            return {"retry": should_retry, "error": str(exc)}

        response_preview = (resp.text or "")[:2000]

        if 200 <= resp.status_code < 300:
            delivered_at = datetime.now(timezone.utc)
            changed = await _transition_processing_delivery(
                db,
                delivery_id=delivery.id,
                dispatch_token=active_dispatch_token,
                delivery_values={
                    "status": "SUCCESS",
                    "http_status": resp.status_code,
                    "response_preview": response_preview,
                    "delivered_at": delivered_at,
                    "error": None,
                    "dispatch_token": None,
                    "dispatch_lease_expires_at": None,
                    "next_dispatch_at": None,
                },
                subscription_id=subscription.id,
                subscription_values={
                    "last_delivered_at": delivered_at,
                    "last_error": None,
                    "last_failure_at": None,
                    "total_delivered": WebhookSubscription.total_delivered + 1,
                },
            )
            if not changed:
                return {"skipped": "stale_dispatch_token"}
            from app.core.metrics import webhook_delivery_attempts_total
            webhook_delivery_attempts_total.labels(
                event_type=delivery.event_type,
                result="success",
            ).inc()
            return {"status": "SUCCESS", "http_status": resp.status_code}

        # Non-2xx — retry on 5xx/429, mark FAILED on 4xx (customer bug).
        retryable = resp.status_code in (408, 425, 429, 500, 502, 503, 504)
        error = f"HTTP {resp.status_code}: {response_preview[:200]}"
        should_retry = retryable and _has_retry_budget(
            attempt_count, subscription.max_retries
        )
        now_failed = datetime.now(timezone.utc)
        final_status = "PENDING" if should_retry else ("DLQ" if retryable else "FAILED")
        changed = await _transition_processing_delivery(
            db,
            delivery_id=delivery.id,
            dispatch_token=active_dispatch_token,
            delivery_values={
                "status": final_status,
                "http_status": resp.status_code,
                "response_preview": response_preview,
                "error": error,
                "dispatch_token": None,
                "dispatch_lease_expires_at": None,
                "next_dispatch_at": (
                    now_failed
                    + timedelta(seconds=min(480, 30 * (2 ** max(attempt_count - 1, 0))))
                    if should_retry
                    else None
                ),
            },
            subscription_id=subscription.id,
            subscription_values={
                "last_failure_at": now_failed,
                "last_error": error[:2000],
                "failure_count": WebhookSubscription.failure_count + 1,
            },
        )
        if not changed:
            return {"skipped": "stale_dispatch_token"}
        from app.core.metrics import webhook_delivery_attempts_total
        webhook_delivery_attempts_total.labels(
            event_type=delivery.event_type,
            result="retry" if should_retry else "failure",
        ).inc()
        if should_retry:
            return {"retry": True, "http_status": resp.status_code}
        return {"status": final_status, "http_status": resp.status_code}
