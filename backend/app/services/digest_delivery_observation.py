"""Durable passive observation of scheduled digest attempts, not a new relay."""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy import func, update

from app.models.postgres import DigestSubscription

logger = logging.getLogger(__name__)
OUTCOMES = ("not_observed", "generating", "generated", "sent", "failed", "skipped", "unknown")


def digest_claim_observation(*, now: datetime, token: uuid.UUID) -> dict:
    """Stage alongside the successful claim, preserving the first unmet slot."""
    return {
        "pending_delivery_since_at": func.coalesce(
            DigestSubscription.pending_delivery_since_at, DigestSubscription.next_delivery_at
        ),
        "delivery_observation_token": token,
        "last_delivery_attempt_at": now,
        "last_delivery_outcome": "generating",
    }


def digest_final_observation(status: str) -> dict:
    if status not in {"sent", "failed", "skipped", "unknown"}:
        raise ValueError("unsupported digest delivery outcome")
    values = {"last_delivery_outcome": status, "delivery_observation_token": None}
    if status in {"sent", "skipped"}:
        values["pending_delivery_since_at"] = None
    return values


def reset_digest_observation(subscription) -> None:
    """A deliberate new schedule/resume supersedes old attempt ownership.

    Historical delivery/generation timestamps remain history. Clearing the
    token prevents an old provider completion from rewriting the new slot.
    """
    subscription.pending_delivery_since_at = None
    subscription.delivery_observation_token = None
    subscription.last_delivery_outcome = None


async def observe_digest_phase(
    subscription_id: uuid.UUID, token: uuid.UUID, *, phase: str, now: datetime | None = None,
) -> bool:
    """Best-effort short transaction; a telemetry failure cannot gate egress.

    The required pending-slot receipt already committed with the claim. This
    update only improves phase detail and never advances delivery success.
    """
    from app.db.postgres import AsyncSessionLocal

    if phase == "generated":
        values = {
            "last_content_generated_at": now or datetime.now(timezone.utc),
            "last_delivery_outcome": "generated",
        }
    elif phase in {"unknown", "skipped"}:
        values = digest_final_observation(phase)
    else:
        raise ValueError("unsupported digest observation phase")
    try:
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                update(DigestSubscription)
                .where(
                    DigestSubscription.id == subscription_id,
                    DigestSubscription.delivery_observation_token == token,
                )
                .values(**values)
            )
            await db.commit()
        return int(getattr(result, "rowcount", 0) or 0) == 1
    except Exception as exc:
        logger.warning("digest_observation_update_failed error_type=%s", type(exc).__name__)
        return False
