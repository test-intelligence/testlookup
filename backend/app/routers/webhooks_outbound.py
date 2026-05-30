"""
Outbound webhooks API — Tier 2 item 6.

Customer-managed webhook subscriptions. QA_LEAD+ manages the
subscriptions; any project member can read the list and delivery
history so dev dashboards can show current status.

Endpoints:

* ``GET    /api/v1/webhooks``                      — list
* ``POST   /api/v1/webhooks``                      — create
* ``GET    /api/v1/webhooks/events``               — supported event catalog
* ``GET    /api/v1/webhooks/{id}``                 — detail
* ``PATCH  /api/v1/webhooks/{id}``                 — partial update
* ``DELETE /api/v1/webhooks/{id}``                 — remove
* ``POST   /api/v1/webhooks/{id}/test``            — fire a synthetic ping event
* ``GET    /api/v1/webhooks/{id}/deliveries``      — delivery history
* ``POST   /api/v1/webhooks/{id}/deliveries/{delivery_id}/replay`` — retry a failed delivery
"""
from __future__ import annotations

import uuid
from typing import Optional

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import (
    get_accessible_project_ids,
    get_current_active_user,
    get_db,
    require_role,
    resolve_project_scope,
)
from app.models.postgres import User, UserRole
from app.models.schemas import (
    WebhookDeliveryRead,
    WebhookDeliveryReplayResponse,
    WebhookEventCatalogEntry,
    WebhookEventCatalogResponse,
    WebhookSubscriptionRead,
    WebhookSubscriptionWrite,
    WebhookTestResponse,
)
from app.services import webhook_service as svc

router = APIRouter(prefix="/api/v1/webhooks", tags=["Outbound Webhooks"])
logger = structlog.get_logger("routers.webhooks_outbound")


# ── Event catalog ──────────────────────────────────────────────────────────


@router.get("/events", response_model=WebhookEventCatalogResponse)
async def list_webhook_events():
    """Return the supported event types — shown in the Settings UI so
    users can pick which events to subscribe to."""
    return WebhookEventCatalogResponse(
        events=[
            WebhookEventCatalogEntry(event_type=e["event_type"], description=e["description"])
            for e in svc.list_supported_events()
        ]
    )


# ── List / create ──────────────────────────────────────────────────────────


@router.get("", response_model=list[WebhookSubscriptionRead])
async def list_webhook_subscriptions(
    project_id: Optional[uuid.UUID] = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    if project_id is not None:
        await resolve_project_scope(db, current_user, str(project_id))
        return await svc.list_subscriptions(db, project_id=project_id)
    accessible = await get_accessible_project_ids(db, current_user)
    return await svc.list_subscriptions(db, project_ids=accessible)


@router.post(
    "",
    response_model=WebhookSubscriptionRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_webhook_subscription(
    payload: WebhookSubscriptionWrite,
    project_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.QA_LEAD)),
):
    """Create a new webhook subscription. QA_LEAD+.

    ``project_id`` must be passed as a query parameter so the tenant
    isolation check can fire before the body is validated against the
    subscription table.
    """
    await resolve_project_scope(db, current_user, str(project_id))
    return await svc.create_subscription(
        db,
        project_id=project_id,
        actor=current_user,
        name=payload.name,
        target_url=payload.target_url,
        events=payload.events,
        enabled=payload.enabled,
        max_retries=payload.max_retries,
        secret=payload.secret,
    )


# ── Detail / update / delete ───────────────────────────────────────────────


async def _load_and_scope(
    db: AsyncSession, current_user: User, subscription_id: uuid.UUID,
):
    row = await svc.get_subscription(db, subscription_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Webhook subscription not found")
    await resolve_project_scope(db, current_user, str(row.project_id))
    return row


@router.get("/{subscription_id}", response_model=WebhookSubscriptionRead)
async def get_webhook_subscription(
    subscription_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    return await _load_and_scope(db, current_user, subscription_id)


@router.patch("/{subscription_id}", response_model=WebhookSubscriptionRead)
async def update_webhook_subscription(
    subscription_id: uuid.UUID,
    payload: WebhookSubscriptionWrite,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.QA_LEAD)),
):
    await _load_and_scope(db, current_user, subscription_id)
    return await svc.update_subscription(
        db,
        subscription_id=subscription_id,
        actor=current_user,
        name=payload.name,
        target_url=payload.target_url,
        events=payload.events,
        enabled=payload.enabled,
        max_retries=payload.max_retries,
        secret=payload.secret,
    )


@router.delete("/{subscription_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_webhook_subscription(
    subscription_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.QA_LEAD)),
):
    await _load_and_scope(db, current_user, subscription_id)
    await svc.delete_subscription(db, subscription_id, current_user)
    return None


# ── Test fire ──────────────────────────────────────────────────────────────


@router.post("/{subscription_id}/test", response_model=WebhookTestResponse)
async def test_webhook_subscription(
    subscription_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.QA_LEAD)),
):
    """Emit a synthetic ``run.completed`` event to this subscription so
    the customer can verify their receiver without waiting for a real
    run. Creates a real ``WebhookDelivery`` row so history reflects the
    test alongside production deliveries.
    """
    row = await _load_and_scope(db, current_user, subscription_id)
    import time
    start = time.perf_counter()
    count = await svc.emit_event(
        "run.completed",
        project_id=row.project_id,
        payload={
            "is_test": True,
            "triggered_by": str(current_user.id),
            "subscription_id": str(row.id),
            "message": "Synthetic test event from TestLookup Settings UI",
        },
    )
    latency_ms = int((time.perf_counter() - start) * 1000)
    if count > 0:
        return WebhookTestResponse(
            success=True,
            status_code=202,
            message=f"Enqueued {count} test delivery",
            latency_ms=latency_ms,
        )
    return WebhookTestResponse(
        success=False,
        status_code=None,
        message=(
            "No delivery enqueued — check that outbound_webhooks is enabled, "
            "AI_OFFLINE_MODE is off, and the subscription is enabled with "
            "run.completed in its event list."
        ),
        latency_ms=latency_ms,
    )


# ── Delivery history ───────────────────────────────────────────────────────


@router.get(
    "/{subscription_id}/deliveries",
    response_model=list[WebhookDeliveryRead],
)
async def list_webhook_deliveries(
    subscription_id: uuid.UUID,
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    await _load_and_scope(db, current_user, subscription_id)
    return await svc.list_deliveries(db, subscription_id, limit=limit)


@router.post(
    "/{subscription_id}/deliveries/{delivery_id}/replay",
    response_model=WebhookDeliveryReplayResponse,
)
async def replay_webhook_delivery(
    subscription_id: uuid.UUID,
    delivery_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.QA_LEAD)),
):
    """Replay a failed or DLQ'd webhook delivery.

    Creates a fresh ``PENDING`` row with the same payload and enqueues
    a delivery task. The original failed row is left in place so the
    audit history is preserved. QA_LEAD+ only; caller must have project
    access to the subscription. Fails with 409 when the delivery is
    already in-flight (``PENDING``) or succeeded (``SUCCESS``), and 503
    when the ``outbound_webhooks`` feature flag is off or offline mode
    is enabled.
    """
    from sqlalchemy import select as _select
    from app.models.postgres import WebhookDelivery as _Delivery

    sub = await _load_and_scope(db, current_user, subscription_id)
    result = await db.execute(
        _select(_Delivery).where(
            _Delivery.id == delivery_id,
            _Delivery.subscription_id == sub.id,
        )
    )
    original = result.scalar_one_or_none()
    if original is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Delivery not found on this subscription",
        )
    if original.status not in ("FAILED", "DLQ"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Cannot replay a delivery in status {original.status}: "
                "only FAILED or DLQ deliveries are replayable."
            ),
        )

    new_id = await svc.replay_delivery(delivery_id)
    if new_id is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "Webhook replay is unavailable — outbound_webhooks is "
                "disabled or AI_OFFLINE_MODE is enabled."
            ),
        )
    return WebhookDeliveryReplayResponse(delivery_id=new_id, status="PENDING")
