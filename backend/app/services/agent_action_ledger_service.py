"""Typed, tenant-scoped action proposal and execution ledger.

This service is stage-only: the owning request/worker commits the transaction.
External side effects must receive an approved ledger row and persist the
result against the same idempotency key before reporting success.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.postgres import AsyncSessionLocal
from app.models.postgres import AgentActionDispatchOutbox, AgentActionLedger
from app.services.canonical_json import stable_json_sha256
from app.services.evidence_sanitizer import sanitize_persistence_payload

_STATUSES = frozenset({
    "proposed", "pending_review", "approved", "executing", "executed",
    "failed", "rejected", "rolled_back",
})
_TRANSITIONS = {
    "proposed": frozenset({"pending_review", "approved", "rejected"}),
    "pending_review": frozenset({"approved", "rejected"}),
    "approved": frozenset({"executing", "rejected"}),
    "executing": frozenset({"executed", "failed"}),
    "executed": frozenset({"rolled_back"}),
    "failed": frozenset(),
    "rejected": frozenset(),
    "rolled_back": frozenset(),
}


def _safe_payload(payload: Any) -> dict[str, Any]:
    value, _stats = sanitize_persistence_payload(payload if isinstance(payload, dict) else {})
    if not isinstance(value, dict):
        raise ValueError("action_payload_invalid")
    # Hash the exact bounded/redacted projection that is persisted.
    stable_json_sha256(value, max_bytes=128_000)
    return value


async def create_action_proposal(
    db: AsyncSession,
    *,
    project_id: uuid.UUID,
    action_type: str,
    target_type: str,
    target_id: str | None,
    idempotency_key: str,
    request_payload: dict[str, Any],
    approval_required: bool = True,
    test_run_id: uuid.UUID | None = None,
    pipeline_run_id: uuid.UUID | None = None,
    actor_user_id: uuid.UUID | None = None,
) -> tuple[AgentActionLedger, bool]:
    """Create or return an identical proposal for this tenant/key.

    Reusing a key with a different action or payload fails closed; no external
    request can be smuggled under an existing approved proposal.
    """
    if not action_type or len(action_type) > 60 or not target_type or len(target_type) > 60:
        raise ValueError("action_identity_invalid")
    if not idempotency_key or len(idempotency_key) > 128:
        raise ValueError("action_idempotency_invalid")
    payload = _safe_payload(request_payload)
    payload_hash = stable_json_sha256(payload, max_bytes=128_000)
    existing = await db.execute(
        select(AgentActionLedger).where(
            AgentActionLedger.project_id == project_id,
            AgentActionLedger.idempotency_key == idempotency_key,
        )
    )
    row = existing.scalar_one_or_none()
    if row is not None:
        if (
            row.action_type != action_type
            or row.target_type != target_type
            or row.target_id != target_id
            or row.request_sha256 != payload_hash
        ):
            raise ValueError("action_idempotency_conflict")
        return row, False
    row = AgentActionLedger(
        project_id=project_id,
        test_run_id=test_run_id,
        pipeline_run_id=pipeline_run_id,
        actor_user_id=actor_user_id,
        action_type=action_type,
        target_type=target_type,
        target_id=target_id,
        status="pending_review" if approval_required else "approved",
        approval_required=approval_required,
        idempotency_key=idempotency_key,
        request_sha256=payload_hash,
        request_payload=payload,
    )
    db.add(row)
    await db.flush()
    if not approval_required:
        await enqueue_action_dispatch(db, action=row)
    return row, True


async def transition_action(
    db: AsyncSession,
    *,
    project_id: uuid.UUID,
    action_id: uuid.UUID,
    to_status: str,
    actor_user_id: uuid.UUID | None = None,
    result_payload: dict[str, Any] | None = None,
    rollback_payload: dict[str, Any] | None = None,
    error_code: str | None = None,
) -> AgentActionLedger:
    """Apply one guarded lifecycle transition and return the locked row."""
    if to_status not in _STATUSES:
        raise ValueError("action_status_invalid")
    result = await db.execute(
        select(AgentActionLedger).where(
            AgentActionLedger.id == action_id,
            AgentActionLedger.project_id == project_id,
        ).with_for_update()
    )
    row = result.scalar_one_or_none()
    if row is None:
        raise ValueError("action_not_found")
    if to_status not in _TRANSITIONS.get(row.status, frozenset()):
        raise ValueError("action_transition_invalid")
    now = datetime.now(timezone.utc)
    row.status = to_status
    if actor_user_id is not None and to_status in {"approved", "rejected"}:
        row.approved_by = actor_user_id
        row.approved_at = now
    if to_status == "executing":
        row.execution_started_at = now
    if to_status in {"executed", "failed", "rolled_back"}:
        row.execution_completed_at = now
    if result_payload is not None:
        row.result_payload = _safe_payload(result_payload)
    if rollback_payload is not None:
        row.rollback_payload = _safe_payload(rollback_payload)
    row.error_code = error_code[:100] if error_code else None
    if to_status == "approved":
        await enqueue_action_dispatch(db, action=row)
    return row


async def get_action(
    db: AsyncSession, *, project_id: uuid.UUID, action_id: uuid.UUID
) -> AgentActionLedger | None:
    result = await db.execute(
        select(AgentActionLedger).where(
            AgentActionLedger.project_id == project_id,
            AgentActionLedger.id == action_id,
        )
    )
    return result.scalar_one_or_none()


async def enqueue_action_dispatch(
    db: AsyncSession, *, action: AgentActionLedger
) -> AgentActionDispatchOutbox:
    """Create one delivery intent for an approved action."""
    if action.status != "approved":
        raise ValueError("action_dispatch_requires_approval")
    existing = await db.execute(
        select(AgentActionDispatchOutbox).where(
            AgentActionDispatchOutbox.action_id == action.id,
        )
    )
    row = existing.scalar_one_or_none()
    if row is not None:
        if row.idempotency_key != action.idempotency_key:
            raise ValueError("action_dispatch_idempotency_conflict")
        return row
    row = AgentActionDispatchOutbox(
        action_id=action.id,
        project_id=action.project_id,
        idempotency_key=action.idempotency_key,
        status="pending",
    )
    db.add(row)
    await db.flush()
    return row


async def claim_action_dispatches(
    db: AsyncSession, *, limit: int = 50, lease_seconds: int = 120
) -> list[AgentActionDispatchOutbox]:
    """Lease pending or expired-sending delivery intents for one relay pass."""
    now = datetime.now(timezone.utc)
    limit = max(1, min(int(limit), 200))
    result = await db.execute(
        select(AgentActionDispatchOutbox)
        .where(
            or_(
                and_(
                    AgentActionDispatchOutbox.status == "pending",
                    or_(
                        AgentActionDispatchOutbox.next_attempt_at.is_(None),
                        AgentActionDispatchOutbox.next_attempt_at <= now,
                    ),
                ),
                and_(
                    AgentActionDispatchOutbox.status == "sending",
                    AgentActionDispatchOutbox.lease_expires_at <= now,
                ),
            )
        )
        .order_by(AgentActionDispatchOutbox.created_at, AgentActionDispatchOutbox.id)
        .with_for_update(skip_locked=True)
        .limit(limit)
    )
    rows = list(result.scalars().all())
    for row in rows:
        row.status = "sending"
        row.attempts = int(row.attempts or 0) + 1
        row.lease_expires_at = now + timedelta(seconds=max(10, min(int(lease_seconds), 900)))
        row.next_attempt_at = row.lease_expires_at
        row.last_error = None
    return rows


async def mark_action_dispatch_sent(
    db: AsyncSession, *, outbox_id: uuid.UUID
) -> bool:
    result = await db.execute(
        select(AgentActionDispatchOutbox).where(
            AgentActionDispatchOutbox.id == outbox_id,
        ).with_for_update()
    )
    row = result.scalar_one_or_none()
    if row is None or row.status != "sending":
        return False
    row.status = "sent"
    row.sent_at = datetime.now(timezone.utc)
    row.lease_expires_at = None
    row.next_attempt_at = None
    return True


async def mark_action_dispatch_failed(
    db: AsyncSession,
    *,
    outbox_id: uuid.UUID,
    error_code: str,
    permanent: bool = False,
) -> bool:
    result = await db.execute(
        select(AgentActionDispatchOutbox).where(
            AgentActionDispatchOutbox.id == outbox_id,
        ).with_for_update()
    )
    row = result.scalar_one_or_none()
    if row is None or row.status != "sending":
        return False
    row.status = "failed" if permanent else "pending"
    row.last_error = str(error_code)[:200]
    row.lease_expires_at = None
    row.next_attempt_at = None if permanent else datetime.now(timezone.utc) + timedelta(
        seconds=min(300, 2 ** min(int(row.attempts or 0), 8))
    )
    return True


async def _proposing_run_review_accepted(db: AsyncSession, action: AgentActionLedger) -> bool:
    """True when the action has no proposing run, or that run's review is accepted.

    Architecture section 7.5: a mutating call runs only on behalf of an AI run a
    person has accepted. An action a human created directly has no proposing
    run and no report to review.
    """
    pipeline_run_id = getattr(action, "pipeline_run_id", None)
    if pipeline_run_id is None:
        return True
    from app.models.postgres import ReviewRequest  # noqa: PLC0415

    result = await db.execute(
        select(ReviewRequest.id).where(
            ReviewRequest.pipeline_run_id == pipeline_run_id,
            ReviewRequest.state == "accepted",
        ).limit(1)
    )
    return result.scalar_one_or_none() is not None


async def execute_agent_action(
    *, project_id: uuid.UUID, action_id: uuid.UUID
) -> dict[str, Any]:
    """Consume one approved action without permitting unregistered effects.

    The generic ledger intentionally has no implicit external side effects. A
    future adapter must be explicitly registered and reviewed; until then the
    action is terminally failed with a bounded, user-visible reason. This keeps
    queue delivery honest while preventing an approved row from being treated
    as an executed mutation.
    """
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(AgentActionLedger).where(
                AgentActionLedger.id == action_id,
                AgentActionLedger.project_id == project_id,
            ).with_for_update()
        )
        action = result.scalar_one_or_none()
        if action is None:
            return {"status": "not_found", "action_id": str(action_id)}
        if action.status == "executed":
            return {"status": "already_executed", "action_id": str(action_id)}
        if action.status != "approved":
            return {
                "status": "not_executable",
                "action_id": str(action_id),
                "reason": "action_not_approved",
            }
        now = datetime.now(timezone.utc)
        # E8.6 (section 7.5): approving the action is not enough. The AI run
        # that proposed it must itself have been accepted by a person, or the
        # mutation would act on a report nobody has vouched for.
        if not await _proposing_run_review_accepted(db, action):
            action.status = "failed"
            action.execution_started_at = now
            action.execution_completed_at = now
            action.error_code = "policy_denied"
            await db.commit()
            return {"status": "failed", "action_id": str(action_id), "reason": "policy_denied"}
        action.status = "failed"
        action.execution_started_at = now
        action.execution_completed_at = now
        action.error_code = "action_executor_not_configured"
        await db.commit()
        return {
            "status": "failed",
            "action_id": str(action_id),
            "reason": "action_executor_not_configured",
        }


async def relay_action_dispatch_outbox(*, limit: int = 50) -> dict[str, int]:
    """Publish approved action IDs to the guarded executor task.

    Only identifiers cross Celery; payloads are re-resolved from PostgreSQL by
    the executor. A publish acknowledgement is recorded as ``sent``. Any
    broker failure leaves a bounded retryable outbox row.
    """
    from app.worker.tasks import execute_agent_action

    async with AsyncSessionLocal() as db:
        rows = await claim_action_dispatches(db, limit=limit)
        claimed = [(row.id, row.project_id, row.action_id) for row in rows]
        await db.commit()

    sent = failed = 0
    for outbox_id, project_id, action_id in claimed:
        try:
            execute_agent_action.apply_async(
                args=[str(project_id), str(action_id)],
                queue="default",
                task_id=f"agent-action-{action_id}",
            )
            async with AsyncSessionLocal() as db:
                acknowledged = await mark_action_dispatch_sent(
                    db, outbox_id=outbox_id
                )
                if acknowledged:
                    await db.commit()
                    sent += 1
                else:
                    failed += 1
        except Exception as exc:  # noqa: BLE001
            failed += 1
            async with AsyncSessionLocal() as db:
                if await mark_action_dispatch_failed(
                    db,
                    outbox_id=outbox_id,
                    error_code=f"broker_{type(exc).__name__}",
                ):
                    await db.commit()
    return {"claimed": len(claimed), "sent": sent, "failed": failed}


async def persist_report_action_proposals(
    db: AsyncSession,
    *,
    project_id: uuid.UUID,
    test_run_id: uuid.UUID,
    pipeline_run_id: uuid.UUID,
    proposed_actions: list[dict[str, Any]] | None,
) -> int:
    """Materialize typed DecisionReport proposals without executing them."""
    persisted = 0
    for action in (proposed_actions or [])[:20]:
        if not isinstance(action, dict):
            continue
        action_id = str(action.get("action_id") or "")[:160]
        idempotency_key = str(action.get("idempotency_key") or "")[:128]
        if not action_id or not idempotency_key:
            continue
        await create_action_proposal(
            db,
            project_id=project_id,
            test_run_id=test_run_id,
            pipeline_run_id=pipeline_run_id,
            action_type="decision_report_action",
            target_type=str(action.get("required_permission") or "human_review")[:60],
            target_id=action_id,
            idempotency_key=idempotency_key,
            request_payload={
                "action_id": action_id,
                "title": action.get("title"),
                "owner": action.get("owner"),
                "rationale": action.get("rationale"),
                "evidence": action.get("evidence") or [],
                "risk": action.get("risk") or "unknown",
                "required_permission": action.get("required_permission"),
            },
            approval_required=True,
        )
        persisted += 1
    return persisted
