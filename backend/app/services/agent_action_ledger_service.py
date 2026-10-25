"""Typed, tenant-scoped action proposal and execution ledger.

Proposals, transitions, the dispatch outbox and the reaper's sweep are
stage-only: the owning request / worker commits the transaction. The ledger's
own commits are the worker-owned ones the transaction-boundary test caps at 5:

* the executor (``execute_agent_action``, W15 AGT-03) runs outside any request
  and owns exactly two -- ``_commit_claim``, the DURABLE ``approved →
  executing`` claim committed BEFORE any effect (a target service may commit on
  its own, so the effect and its record are two transactions), and
  ``_finish``, the ONE terminal write (every refusal, ``executed``,
  ``failed``);
* the dispatch relay owns three (claim, sent, failed), each in its own session.

Effects run only through the reviewed adapters of
``agent_action_adapters.agent_action_registry`` (owner decision D16: three
internal, reversible types plus a no-effect acknowledge), only for an approved
row whose proposing report a person accepted, whose type the project ticked in
Act (read LIVE), whose payload still matches its hash, whose approver is still
authorised and whose every target is re-loaded inside the action's project. A
failure after the claim is recorded and NEVER retried; a claim left
``executing`` by a dead worker is settled by ``reap_stuck_actions``
(``outcome_unknown``).
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import structlog
from pydantic import ValidationError
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.postgres import AsyncSessionLocal
from app.models.postgres import AgentActionDispatchOutbox, AgentActionLedger
from app.services.agent_action_adapters.adapter_base import (
    ACT_GATE_AGENT_ID,
    ActionAdapter,
    ActionRefused,
    act_refusal,
    get_adapter,
    load_approver,
)
from app.services.agent_action_types import (
    ACTION_TYPE_ALIASES,
    ACTION_TYPES,
    EXECUTION_LEASE_SECONDS,
    REAPER_BATCH,
    ActionTypeSpec,
    activity_entity_label,
    proposer_label,
    resolve_action_type,
)
from app.services.agent_config_resolver import resolve_for_project
from app.services.canonical_json import stable_json_sha256
from app.services.evidence_sanitizer import sanitize_persistence_payload

logger = structlog.get_logger(__name__)

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
            ReviewRequest.project_id == action.project_id,
            ReviewRequest.kind == "report",
            ReviewRequest.subject_type == "pipeline_run",
            ReviewRequest.subject_id == str(pipeline_run_id),
            ReviewRequest.pipeline_run_id == pipeline_run_id,
            ReviewRequest.state == "accepted",
        ).limit(1)
    )
    return result.scalar_one_or_none() is not None


async def _proposing_agent_mode_is_act(
    db: AsyncSession, action: AgentActionLedger
) -> bool:
    """Require the live project config to authorize an AI-originated mutation.

    Directly created human actions have no proposing pipeline and retain their
    existing approval path. Pipeline actions must carry the immutable agent id
    that was hashed into their request payload when proposed. Missing identity
    on a legacy row fails closed because no capability config can be proven.
    """
    if getattr(action, "pipeline_run_id", None) is None:
        return True
    payload = getattr(action, "request_payload", None)
    agent_id = payload.get("proposing_agent_id") if isinstance(payload, dict) else None
    if not isinstance(agent_id, str) or not agent_id:
        return False
    resolved = await resolve_for_project(db, action.project_id, agent_id)
    return resolved.config.mode == "act"


async def _commit_claim(db: AsyncSession, action: AgentActionLedger, *, now: datetime) -> None:
    """The DURABLE claim ``approved → executing``, committed BEFORE any effect.

    A crash after this commit leaves ``executing``: the reaper settles it as
    ``outcome_unknown`` (never a retry). ``execution_started_at`` is the claim's
    fencing value -- the effect runs only while the row still carries it.
    """
    action.status = "executing"
    action.execution_started_at = now
    action.execution_completed_at = None
    action.error_code = None
    await db.commit()


async def _finish(
    db: AsyncSession,
    action: AgentActionLedger,
    *,
    status: str,
    error_code: str | None = None,
    result_payload: dict[str, Any] | None = None,
    rollback_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """The executor's ONE terminal write (``executed`` / ``failed``), committed."""
    now = datetime.now(timezone.utc)
    action.status = status
    if action.execution_started_at is None:
        action.execution_started_at = now
    action.execution_completed_at = now
    action.error_code = error_code[:100] if error_code else None
    if result_payload is not None:
        action.result_payload = _safe_payload(result_payload)
    if rollback_payload is not None:
        action.rollback_payload = _safe_payload(rollback_payload)
    await db.commit()
    answer: dict[str, Any] = {"status": status, "action_id": str(action.id)}
    if error_code:
        answer["reason"] = error_code
    return answer


async def _stage_failure_audit(
    db: AsyncSession,
    action: AgentActionLedger,
    *,
    approver: Any,
    canonical_type: str | None,
    error_code: str,
) -> None:
    """``agent_action.failed`` -- ids, the type and the code only (never payload text)."""
    from app.services.access_audit_service import log_access_change  # noqa: PLC0415

    await log_access_change(
        db,
        "agent_action.failed",
        approver,
        project_id=action.project_id,
        after_value={
            "action_id": str(action.id),
            "canonical_type": canonical_type,
            "status": "failed",
            "error_code": error_code,
            "approved_by": str(action.approved_by) if action.approved_by else None,
        },
    )


async def _refuse(
    db: AsyncSession,
    action: AgentActionLedger,
    code: str,
    *,
    spec: ActionTypeSpec | None = None,
    approver: Any = None,
) -> dict[str, Any]:
    """A refusal before the claim: ``failed`` with its code, nothing executed."""
    logger.info(
        "agent_action_refused",
        action_id=str(action.id),
        project_id=str(action.project_id),
        error_code=code,
    )
    await _stage_failure_audit(
        db, action, approver=approver,
        canonical_type=spec.action_type if spec is not None else None, error_code=code,
    )
    return await _finish(db, action, status="failed", error_code=code)


async def _lock_claimed(db: AsyncSession, claim: "_Claim") -> AgentActionLedger | None:
    """The row, locked, ONLY while it is still this claim's (``executing``, same start)."""
    row = (
        await db.execute(
            select(AgentActionLedger).where(
                AgentActionLedger.id == claim.action_id,
                AgentActionLedger.project_id == claim.project_id,
            ).with_for_update()
        )
    ).scalar_one_or_none()
    if row is None or row.status != "executing" or row.execution_started_at != claim.started_at:
        return None
    return row


@dataclass(frozen=True)
class _Claim:
    """What the effect session needs from the claim session (plain values)."""

    project_id: uuid.UUID
    action_id: uuid.UUID
    started_at: datetime
    spec: ActionTypeSpec
    adapter: ActionAdapter
    payload: Any
    approver: Any
    pipeline_run_id: uuid.UUID | None


async def execute_agent_action(
    *, project_id: uuid.UUID, action_id: uuid.UUID
) -> dict[str, Any]:
    """Execute one approved action through its reviewed adapter (W15 AGT-03, §2.3).

    Session 1 (steps 1-9) refuses or CLAIMS: lock; a registered adapter; the
    proposing run's accepted review; the LIVE Act gate; the payload re-hashed
    and re-validated; the approver re-authorised; the adapter's project-
    filtered preflight; external effects refused offline; then the durable
    ``approved → executing`` commit BEFORE any effect. Session 2 (step 10) runs
    the effect fenced by the claim and finishes ``executed`` with the audit and
    activity rows. Any failure after the claim is recorded on session 3 and
    NEVER retried (``outcome_unknown`` when the target service commits itself).
    """
    async with AsyncSessionLocal() as db:
        claimed = await _claim_or_refuse(db, project_id=project_id, action_id=action_id)
    if isinstance(claimed, dict):
        return claimed
    return await _run_effect(claimed)


async def _claim_or_refuse(
    db: AsyncSession, *, project_id: uuid.UUID, action_id: uuid.UUID
) -> dict[str, Any] | _Claim:
    """Steps 1-9 on one session: a terminal answer, or the committed claim."""
    action = (
        await db.execute(
            select(AgentActionLedger).where(
                AgentActionLedger.id == action_id,
                AgentActionLedger.project_id == project_id,
            ).with_for_update()
        )
    ).scalar_one_or_none()
    # 1. Only an approved row is executable; a second dispatch of a claimed id does nothing.
    if action is None:
        return {"status": "not_found", "action_id": str(action_id)}
    if action.status == "executed":
        return {"status": "already_executed", "action_id": str(action_id)}
    if action.status == "executing":
        return {"status": "already_claimed", "action_id": str(action_id)}
    if action.status != "approved":
        return {"status": "not_executable", "action_id": str(action_id), "reason": "action_not_approved"}
    now = datetime.now(timezone.utc)
    # 2. A reviewed adapter, or today's honest answer.
    spec = resolve_action_type(action.action_type)
    adapter = get_adapter(action.action_type) if spec is not None else None
    if spec is None or adapter is None:
        return await _refuse(db, action, "action_executor_not_configured")
    # 3. E8.6: the AI run that proposed it must itself have been accepted by a person.
    if not await _proposing_run_review_accepted(db, action):
        return await _refuse(db, action, "review_not_accepted", spec=spec)
    raw = action.request_payload if isinstance(action.request_payload, dict) else {}
    proposer = raw.get("proposing_agent_id")
    # 4. The LIVE Act gate: a project that left Act or unticked the type since wins.
    refusal = await act_refusal(db, project_id=action.project_id, spec=spec, proposing_agent_id=proposer)
    if refusal is None and spec.side_effect != "none" and proposer != ACT_GATE_AGENT_ID:
        # An effectful row of a registry capability answers to its OWN live config.
        if not await _proposing_agent_mode_is_act(db, action):
            refusal = "act_mode_required"
    if refusal is not None:
        return await _refuse(db, action, refusal, spec=spec)
    # 5. The stored proposal is the one that was hashed, and still a valid one.
    try:
        tampered = stable_json_sha256(raw, max_bytes=128_000) != action.request_sha256
    except (TypeError, ValueError):
        tampered = True
    if tampered:
        return await _refuse(db, action, "payload_tampered", spec=spec)
    try:
        payload = spec.payload_model.model_validate(raw)
    except ValidationError:
        return await _refuse(db, action, "payload_invalid", spec=spec)
    if getattr(payload, "proposing_agent_id", None) not in spec.proposers:
        return await _refuse(db, action, "proposer_not_permitted", spec=spec)
    # 6. The approver, re-authorised NOW (demoted / removed / deactivated / synthetic).
    try:
        approver = await load_approver(
            db, user_id=action.approved_by, project_id=action.project_id,
            required_role=spec.required_role,
        )
    except ActionRefused as exc:
        return await _refuse(db, action, exc.code, spec=spec)
    # 7. Every target re-loaded inside action.project_id (the IDOR boundary).
    try:
        await adapter.preflight(db, action=action, payload=payload, approver=approver)
    except ActionRefused as exc:
        return await _refuse(db, action, exc.code, spec=spec, approver=approver)
    # 8. External effects never run offline (no external adapter exists: D16).
    if spec.side_effect == "external" and settings.AI_OFFLINE_MODE:
        return await _refuse(db, action, "offline_mode_blocked", spec=spec, approver=approver)
    # 9. The durable claim, BEFORE the effect.
    await _commit_claim(db, action, now=now)
    return _Claim(
        project_id=action.project_id, action_id=action.id, started_at=now, spec=spec,
        adapter=adapter, payload=payload, approver=approver, pipeline_run_id=action.pipeline_run_id,
    )


def _display_name(user: Any) -> str | None:
    return getattr(user, "full_name", None) or getattr(user, "username", None)


async def _run_effect(claim: _Claim) -> dict[str, Any]:
    """Step 10: the effect on its own session, fenced by the claim; ``executed`` or recorded failure."""
    from app.services.access_audit_service import log_access_change  # noqa: PLC0415
    from app.services.activity.service import ActorRef  # noqa: PLC0415
    from app.services.activity.service import record as record_activity  # noqa: PLC0415

    spec, adapter = claim.spec, claim.adapter
    lost = {"status": "claim_lost", "action_id": str(claim.action_id)}
    failure: str | None = None
    answer: dict[str, Any] = {}
    result_payload: dict[str, Any] = {}
    async with AsyncSessionLocal() as db:
        action = await _lock_claimed(db, claim)
        if action is None:
            return lost
        try:
            outcome = await adapter.execute(db, action=action, payload=claim.payload, approver=claim.approver)
            if adapter.commits_effect:
                # The target service committed this session (and released our lock).
                action = await _lock_claimed(db, claim)
                if action is None:
                    logger.warning("agent_action_claim_lost_after_effect", action_id=str(claim.action_id))
                    return lost
            link = dict(outcome.result_link) if outcome.result_link else None
            result_payload = {**outcome.result_payload, "result_link": link}
            await log_access_change(
                db,
                "agent_action.executed",
                claim.approver,
                project_id=action.project_id,
                after_value={
                    "action_id": str(action.id),
                    "canonical_type": spec.action_type,
                    "status": "executed",
                    "result_kind": link.get("kind") if link else None,
                    "target_id": action.target_id,
                },
            )
            proposer = getattr(claim.payload, "proposing_agent_id", None)
            await record_activity(
                db,
                project_id=action.project_id,
                event_type="agent.action_executed",
                actor=ActorRef.agent(proposer_label(proposer) or "Agent"),
                entity_id=action.id,
                entity_label=activity_entity_label(spec, claim.payload),
                entity_type="agent_action",
                target_type=spec.target_type,
                target_id=action.target_id,
                context={
                    "approved_by": _display_name(claim.approver),
                    "canonical_type": spec.action_type,
                    "result_kind": link.get("kind") if link else None,
                    "target_id": action.target_id,
                },
            )
            answer = await _finish(
                db, action, status="executed",
                result_payload=result_payload, rollback_payload=outcome.rollback_payload,
            )
        except ActionRefused as exc:
            # A race the preflight could not see; a committing service may have acted.
            failure = "outcome_unknown" if adapter.commits_effect else exc.code
        except Exception as exc:  # noqa: BLE001 -- recorded below, never retried
            failure = "outcome_unknown" if adapter.commits_effect else "execution_error"
            logger.warning(
                "agent_action_effect_failed",
                action_id=str(claim.action_id),
                canonical_type=spec.action_type,
                error_type=type(exc).__name__,
            )
    # Leaving the session discarded whatever the failed effect staged.
    if failure is not None:
        return await _record_effect_failure(claim, failure)
    if spec.side_effect != "none":
        # After the finish commit, never on the failure path: the cached
        # dashboard reads defects, quarantine requests and test cases.
        from app.services.cache_service import bump_analytics_epoch  # noqa: PLC0415

        await bump_analytics_epoch(claim.project_id)
    await _after_executed(claim, result_payload)
    return answer


async def _record_effect_failure(claim: _Claim, code: str) -> dict[str, Any]:
    """Session 3: ``failed`` with a bounded code + ``agent_action.failed``, while still ours."""
    async with AsyncSessionLocal() as db:
        action = await _lock_claimed(db, claim)
        if action is None:
            return {"status": "claim_lost", "action_id": str(claim.action_id)}
        await _stage_failure_audit(
            db, action, approver=claim.approver, canonical_type=claim.spec.action_type, error_code=code,
        )
        return await _finish(db, action, status="failed", error_code=code)


async def _after_executed(claim: _Claim, result_payload: dict[str, Any]) -> None:
    """Best effort, after the finish commit: the defect webhook and the pipeline event."""
    if claim.spec.action_type == "defect.promote" and result_payload.get("approval_status") in (
        "approved", "executed",
    ):
        try:
            from app.services.defect_promoted_webhook import emit_defect_promoted  # noqa: PLC0415

            await emit_defect_promoted(str(result_payload.get("defect_id")))
        except Exception as exc:  # noqa: BLE001 -- the promotion stands
            logger.warning("agent_action_defect_webhook_failed", error_type=type(exc).__name__)
    if claim.pipeline_run_id is None:
        return
    try:
        from app.services.pipeline_event_log import emit_event  # noqa: PLC0415

        await emit_event(
            str(claim.pipeline_run_id),
            "agent_action_executed",
            detail={"action_id": str(claim.action_id), "action_type": claim.spec.action_type},
        )
    except Exception as exc:  # noqa: BLE001 -- the action is already committed
        logger.warning("agent_action_event_not_logged", error_type=type(exc).__name__)


#: The stored types the executor can claim: the catalogue's and the legacy
#: aliases. ``tool_call_idempotency`` keeps its OWN ``executing`` rows in this
#: table (``approval_required=False``, a tool name as the type) and re-claims a
#: ``failed`` one -- reaping those would let a crashed tool call run twice.
_EXECUTOR_ACTION_TYPES: tuple[str, ...] = tuple(sorted(set(ACTION_TYPES) | set(ACTION_TYPE_ALIASES)))


async def reap_stuck_actions(
    db: AsyncSession,
    *,
    now: datetime,
    lease_seconds: int = EXECUTION_LEASE_SECONDS,
    limit: int = REAPER_BATCH,
) -> int:
    """Settle claims whose executor died: ``executing`` past the lease → ``failed`` / ``outcome_unknown``.

    Stage-only (the beat task commits). Never a retry: the executor acts only on
    ``approved`` rows, the outbox row is already ``sent`` and no transition leaves
    ``failed``. ``SKIP LOCKED``: a row an executor still holds is not touched.
    """
    cutoff = now - timedelta(seconds=max(1, int(lease_seconds)))
    rows = (
        await db.execute(
            select(AgentActionLedger)
            .where(
                AgentActionLedger.status == "executing",
                AgentActionLedger.action_type.in_(_EXECUTOR_ACTION_TYPES),
                AgentActionLedger.execution_started_at < cutoff,
            )
            .order_by(AgentActionLedger.execution_started_at, AgentActionLedger.id)
            .with_for_update(skip_locked=True)
            .limit(max(1, min(int(limit), REAPER_BATCH)))
        )
    ).scalars().all()
    for row in rows:
        row.status = "failed"
        row.error_code = "outcome_unknown"
        row.execution_completed_at = now
        spec = resolve_action_type(row.action_type)
        await _stage_failure_audit(
            db, row, approver=None,
            canonical_type=spec.action_type if spec is not None else None, error_code="outcome_unknown",
        )
    if rows:
        logger.warning("agent_actions_reaped", count=len(rows))
    return len(rows)


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
    proposing_agent_mode: str | None = None,
) -> int:
    """Materialize typed DecisionReport proposals without executing them.

    ``proposing_agent_mode`` is the pipeline's FROZEN decision-report mode
    (§0.5): written into the payload -- and so hashed into the row -- only when
    given, so the review's separation-of-duties check reads the mode the agent
    actually ran in. Absent, the payload is byte-for-byte today's.
    """
    persisted = 0
    for action in (proposed_actions or [])[:20]:
        if not isinstance(action, dict):
            continue
        action_id = str(action.get("action_id") or "")[:160]
        idempotency_key = str(action.get("idempotency_key") or "")[:128]
        if not action_id or not idempotency_key:
            continue
        request_payload: dict[str, Any] = {
            "proposing_agent_id": "agent.decision_report.v1",
            "action_id": action_id,
            "title": action.get("title"),
            "owner": action.get("owner"),
            "rationale": action.get("rationale"),
            "evidence": action.get("evidence") or [],
            "risk": action.get("risk") or "unknown",
            "required_permission": action.get("required_permission"),
        }
        if proposing_agent_mode is not None:
            request_payload["proposing_agent_mode"] = proposing_agent_mode
        await create_action_proposal(
            db,
            project_id=project_id,
            test_run_id=test_run_id,
            pipeline_run_id=pipeline_run_id,
            action_type="decision_report_action",
            target_type=str(action.get("required_permission") or "human_review")[:60],
            target_id=action_id,
            idempotency_key=idempotency_key,
            request_payload=request_payload,
            approval_required=True,
        )
        persisted += 1
    return persisted
