"""Human decisions on the generic agent action ledger (W15 AGT-01 / AGT-03).

* ``GET   /api/v1/projects/{project_id}/agent-actions`` -- the queue (QA_LEAD; a
  project-bound API key reaches its own project only). Reads only.
* ``PATCH /api/v1/projects/{project_id}/agent-actions/{action_id}`` -- approve,
  reject (a reason code from the reviews' closed list) or undo an executed
  action, to the reviews standard (``routers/reviews.py``): an interactive login
  only (API keys are refused for EVERY row, D-1) and no synthetic account; the
  approve check refuses up front what the executor would refuse (D-2) and the
  requester of an investigation cannot approve its effects (D-3); the access-audit
  row and the activity event are staged in the SAME transaction as the decision,
  ONE commit, the pipeline event after it. Reject notes are redacted, stored and
  never returned (D-11).

Every refusal is ``detail = {"code", "message"}``.
"""
from __future__ import annotations

import uuid
from typing import Any, Literal, Optional

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CREDENTIAL_KIND_JWT, credential_kind, require_project_access, require_role
from app.db.postgres import get_db
from app.models.postgres import REVIEW_REASON_CODES, AgentActionLedger, User, UserRole
from app.models.schemas import AgentActionListResponse, AgentActionRead
from app.services import agent_action_queue_service as queue
from app.services.access_audit_service import log_access_change
from app.services.activity.service import ActorRef, record as record_activity
from app.services.agent_action_adapters.adapter_base import ActionRefused, get_adapter, load_approver
from app.services.agent_action_ledger_service import transition_action
from app.services.agent_action_types import (
    LEDGER_STATUSES,
    QUEUE_VIEWS,
    ActionTypeSpec,
    activity_entity_label,
    canonical_action_type,
    resolve_action_type,
)
from app.services.cache_service import bump_analytics_epoch
from app.services.privacy_service import sanitize_for_llm

logger = structlog.get_logger("routers.agent_actions")

router = APIRouter(prefix="/api/v1/projects", tags=["Agent Actions"])

NOTES_MAX = 2000


class ActionTransitionRequest(BaseModel):
    status: Literal["approved", "rejected", "rolled_back"]
    # Kept for old clients; IGNORED -- the error code is server-owned.
    error_code: str | None = Field(default=None, max_length=100)
    reason_code: Optional[str] = Field(default=None, max_length=40)   # REVIEW_REASON_CODES; reject only
    notes: Optional[str] = Field(default=None, max_length=NOTES_MAX)  # redacted, stored, never returned


def _refused(status_code: int, code: str, *, status: Optional[str] = None) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail={"code": code, "message": queue.refusal_message(code, status=status)},
    )


def _invalid(code: str, message: str) -> HTTPException:
    return HTTPException(status_code=422, detail={"code": code, "message": message})


@router.get("/{project_id}/agent-actions", response_model=AgentActionListResponse)
async def list_agent_actions(
    project_id: uuid.UUID,
    view: Optional[str] = Query(default=None, max_length=20),
    status: Optional[str] = Query(default=None, max_length=20),
    action_type: Optional[str] = Query(default=None, max_length=60),
    pipeline_run_id: Optional[uuid.UUID] = Query(default=None),
    test_run_id: Optional[uuid.UUID] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.QA_LEAD, allow_project_key=True)),
    __: User = Depends(require_project_access()),
) -> AgentActionListResponse:
    """The project's agent actions. ``view=pending`` is the queue (oldest first);
    ``view=decided`` the recent decisions (newest decision first). ``pipeline_run_id``
    / ``test_run_id`` filter INSIDE the path project: a foreign id matches nothing."""
    if view is not None and view not in QUEUE_VIEWS:
        raise _invalid("view_invalid", "view must be pending or decided.")
    if status is not None and status not in LEDGER_STATUSES:
        raise _invalid("status_invalid", "status must be one of " + ", ".join(LEDGER_STATUSES) + ".")
    if view is not None and status is not None:
        raise _invalid("view_and_status", "Send view or status, not both.")
    return await queue.list_actions(
        db, project_id=project_id, view=view, status=status, action_type=action_type or None,
        pipeline_run_id=pipeline_run_id, test_run_id=test_run_id, limit=limit, viewer=current_user,
    )


async def _transition(
    db: AsyncSession, row: AgentActionLedger, to_status: str, actor: User,
    *, result_payload: Optional[dict[str, Any]] = None,
) -> AgentActionLedger:
    try:
        return await transition_action(
            db, project_id=row.project_id, action_id=row.id, to_status=to_status,
            actor_user_id=actor.id, result_payload=result_payload,
        )
    except ValueError as exc:
        code = str(exc)
        if code == "action_not_found":
            raise _refused(404, "action_not_found") from None
        raise _refused(409, "action_transition_invalid") from None


def _facts(row: AgentActionLedger, spec: Optional[ActionTypeSpec]) -> dict[str, Any]:
    """Ids, codes and statuses only -- never notes, a rationale or a title (§1.4)."""
    return {
        "action_id": str(row.id),
        "action_type": row.action_type,
        "canonical_type": canonical_action_type(row.action_type),
        "target_type": row.target_type,
        "target_id": row.target_id,
        "pipeline_run_id": str(row.pipeline_run_id) if row.pipeline_run_id else None,
        "side_effect": spec.side_effect if spec is not None else None,
    }


async def _audit(
    db: AsyncSession, row: AgentActionLedger, spec: Optional[ActionTypeSpec], actor: User,
    *, action: str, before: str, **extra: Any,
) -> None:
    await log_access_change(
        db, action=action, actor=actor, project_id=row.project_id,
        before_value={"status": before},
        after_value={**_facts(row, spec), "status": row.status, **extra},
    )


async def _approve(
    db: AsyncSession, row: AgentActionLedger, spec: Optional[ActionTypeSpec], actor: User,
) -> AgentActionLedger:
    before = row.status
    refusal = await queue.approval_refusal(db, action=row, spec=spec, approver=actor)
    if refusal is not None:
        raise _refused(refusal[0], refusal[1], status=row.status)
    row = await _transition(db, row, "approved", actor)  # stages the dispatch outbox row
    await _audit(db, row, spec, actor, action="agent_action.approved", before=before)
    return row


async def _reject(
    db: AsyncSession, row: AgentActionLedger, spec: Optional[ActionTypeSpec], actor: User,
    *, reason_code: str, notes: Optional[str],
) -> AgentActionLedger:
    if row.status not in queue.REJECTABLE_STATUSES:
        raise _refused(409, "action_not_pending", status=row.status)
    before = row.status
    cleaned = (notes or "").strip()
    rejection: dict[str, Any] = {"reason_code": reason_code}
    if cleaned:
        rejection["notes"] = sanitize_for_llm(cleaned)[:NOTES_MAX]  # stored, never returned (D-11)
    row = await _transition(db, row, "rejected", actor, result_payload={"rejection": rejection})
    await _audit(db, row, spec, actor, action="agent_action.rejected", before=before, reason_code=reason_code)
    return row


def _result_kind(row: AgentActionLedger) -> Optional[str]:
    result = row.result_payload if isinstance(row.result_payload, dict) else {}
    link = result.get("result_link")
    kind = link.get("kind") if isinstance(link, dict) else None
    return kind if isinstance(kind, str) else None


async def _undo(
    db: AsyncSession, row: AgentActionLedger, spec: Optional[ActionTypeSpec], actor: User,
) -> AgentActionLedger:
    """§2.3 rollback: the adapter's compare-and-set first, then RE-LOCK and the transition."""
    if row.status != "executed":
        raise _refused(409, "action_not_executed")
    adapter = get_adapter(row.action_type) if spec is not None and spec.reversible else None
    if spec is None or adapter is None:
        raise _refused(409, "action_not_reversible")
    try:
        await load_approver(db, user_id=actor.id, project_id=row.project_id, required_role=spec.required_role)
    except ActionRefused:
        raise _refused(403, "approver_not_authorized") from None
    project_id, action_id = row.project_id, row.id
    try:
        outcome = await adapter.rollback(
            db, action=row, rollback_payload=dict(row.rollback_payload or {}), actor=actor,
        )
    except ActionRefused as exc:
        status_code = exc.status_code if exc.status_code in (403, 404, 409, 422) else 409
        raise _refused(status_code, exc.code) from None
    # A target service may have committed the request session (the quarantine
    # release does), releasing the row lock: take it again and require the row
    # still executed -- a concurrent Undo loses here, having changed nothing twice.
    locked = await queue.get_action_for_update(db, project_id, action_id)
    if locked is None or locked.status != "executed":
        raise _refused(409, "action_not_executed")
    result = dict(locked.result_payload or {})
    result["rollback"] = {**dict(outcome.result_payload or {}), "rolled_back_by": str(actor.id)}
    row = await _transition(db, locked, "rolled_back", actor, result_payload=result)
    await _audit(
        db, row, spec, actor, action="agent_action.rolled_back", before="executed",
        result_kind=_result_kind(row),
    )
    return row


async def _emit_action_event(row: AgentActionLedger, status: str) -> None:
    """Best-effort pipeline event AFTER the commit (the reviews' order)."""
    if row.pipeline_run_id is None:
        return
    try:
        from app.services.pipeline_event_log import emit_event  # noqa: PLC0415

        await emit_event(
            str(row.pipeline_run_id),
            f"agent_action_{status}",
            detail={"action_id": str(row.id), "action_type": row.action_type},
        )
    except Exception as exc:  # noqa: BLE001 -- the decision is already committed
        logger.warning("agent_action_event_not_logged", error_type=type(exc).__name__)


@router.patch("/{project_id}/agent-actions/{action_id}", response_model=AgentActionRead)
async def transition_agent_action(
    project_id: uuid.UUID,
    action_id: uuid.UUID,
    body: ActionTransitionRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.QA_LEAD)),
    _: User = Depends(require_project_access()),
) -> AgentActionRead:
    """Approve, reject or undo one agent action. Nothing is written before every check passes."""
    if credential_kind(current_user) != CREDENTIAL_KIND_JWT:
        raise _refused(403, "interactive_login_required")  # EVERY row (D-1), whatever the key's role
    if bool(getattr(current_user, "is_synthetic", False)):
        raise _refused(403, "synthetic_account")
    if body.status == "rejected" and body.reason_code not in REVIEW_REASON_CODES:
        raise _refused(422, "reason_code_required")
    if body.status != "rejected" and body.reason_code is not None:
        raise _refused(422, "reason_code_on_reject_only")
    row = await queue.get_action_for_update(db, project_id, action_id)  # WHERE id AND project_id
    if row is None:
        raise _refused(404, "action_not_found")
    spec = resolve_action_type(row.action_type)
    actor = ActorRef.from_user(current_user)
    entity_label = activity_entity_label(spec, row.request_payload)
    facts = _facts(row, spec)
    if body.status == "approved":
        row = await _approve(db, row, spec, current_user)
        await record_activity(
            db, project_id=project_id, event_type="agent.action_approved", actor=actor,
            entity_id=row.id, entity_label=entity_label, context=facts,
        )
    elif body.status == "rejected":
        row = await _reject(db, row, spec, current_user, reason_code=str(body.reason_code), notes=body.notes)
        await record_activity(
            db, project_id=project_id, event_type="agent.action_denied", actor=actor,
            entity_id=row.id, entity_label=entity_label,
            context={**facts, "reason_code": body.reason_code},  # notes NEVER (D-11)
        )
    else:
        row = await _undo(db, row, spec, current_user)
        await record_activity(
            db, project_id=project_id, event_type="agent.action_rolled_back", actor=actor,
            entity_id=row.id, entity_label=entity_label,
            context={**facts, "result_kind": _result_kind(row)},
        )
    await db.commit()
    if body.status == "rolled_back":
        await bump_analytics_epoch(project_id)  # the undone effect changed analytics data; after the commit
    await db.refresh(row)  # updated_at is onupdate: refresh before serialising
    await _emit_action_event(row, body.status)
    try:
        return await queue.read_action(db, project_id=project_id, action_id=action_id, viewer=current_user)
    except LookupError:
        raise _refused(404, "action_not_found") from None
