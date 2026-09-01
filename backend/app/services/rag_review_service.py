"""RAG batch review service — accept/reject generated cases (RAG-10)."""
from __future__ import annotations

import uuid
from typing import Optional

import structlog
from fastapi import HTTPException
from sqlalchemy import case as sql_case, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_accessible_project_ids
from app.models.postgres import (
    GenerationBatch,
    GenerationCaseSource,
    ManagedTestCase,
    TestCaseAuditLog,
    TestCaseLifecycleState,
    User,
)
from app.models.schemas import RagCaseAcceptEdits
from app.routers.test_management_shared import apply_model_updates
from app.services.test_management_audit_service import audit_event
from app.services.test_case_lifecycle_service import (
    LifecycleAction,
    stage_test_case_snapshot,
    transition,
)

logger = structlog.get_logger(__name__)

GENERATION_ACCEPTED_ACTION = "generation_accepted"
GENERATION_REJECTED_ACTION = "generation_rejected"
_GENERATION_DISPOSITIONS = {
    GENERATION_ACCEPTED_ACTION: "accepted",
    GENERATION_REJECTED_ACTION: "rejected",
}
_INITIAL_DRAFT_EXIT_ACTIONS = tuple(action.value for action in LifecycleAction)


async def _get_batch_or_404(
    db: AsyncSession,
    batch_id: uuid.UUID,
    user: User,
    *,
    for_update: bool = False,
) -> GenerationBatch:
    stmt = select(GenerationBatch).where(GenerationBatch.id == batch_id)
    if for_update:
        stmt = stmt.with_for_update()
    result = await db.execute(stmt)
    batch = result.scalar_one_or_none()
    if not batch:
        raise HTTPException(status_code=404, detail="Generation batch not found")

    accessible = await get_accessible_project_ids(db, user)
    if accessible is not None and batch.project_id not in accessible:
        raise HTTPException(status_code=403, detail="You do not have access to this project")
    return batch


async def _ensure_initial_undisposed_draft(
    db: AsyncSession,
    case: ManagedTestCase,
    *,
    requested_action: str,
) -> None:
    """Reject repeat/conflicting dispositions under the managed-case lock."""
    # A disposition is authoritative even when its audit row shares a database
    # timestamp with lifecycle rows written in the same transaction.  Rank the
    # semantic disposition first instead of relying on UUID order to break a
    # timestamp tie.
    prior_action = (
        await db.execute(
            select(TestCaseAuditLog.action)
            .where(
                TestCaseAuditLog.entity_type == "test_case",
                TestCaseAuditLog.entity_id == case.id,
                TestCaseAuditLog.project_id == case.project_id,
                TestCaseAuditLog.action.in_(
                    (*_GENERATION_DISPOSITIONS, *_INITIAL_DRAFT_EXIT_ACTIONS)
                ),
            )
            .order_by(
                sql_case(
                    (TestCaseAuditLog.action.in_(tuple(_GENERATION_DISPOSITIONS)), 0),
                    else_=1,
                ),
                TestCaseAuditLog.created_at.desc(),
                TestCaseAuditLog.id.desc(),
            )
            .limit(1)
        )
    ).scalar_one_or_none()

    if prior_action in _GENERATION_DISPOSITIONS:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "generation_case_already_disposed",
                "requested_action": requested_action,
                "disposition": _GENERATION_DISPOSITIONS[prior_action],
            },
        )
    if case.status != TestCaseLifecycleState.DRAFT.value or prior_action is not None:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "generation_case_not_initial_draft",
                "requested_action": requested_action,
                "current_state": case.status,
                "prior_action": prior_action,
            },
        )


async def get_batch_preview(
    db: AsyncSession,
    batch_id: uuid.UUID,
    user: User,
) -> dict:
    """Return batch + all pending generated cases with their citations."""
    batch = await _get_batch_or_404(db, batch_id, user)

    cases_result = await db.execute(
        select(ManagedTestCase).where(
            ManagedTestCase.generation_batch_id == batch_id,
        ).order_by(ManagedTestCase.created_at)
    )
    cases = cases_result.scalars().all()

    citations_result = await db.execute(
        select(GenerationCaseSource).where(
            GenerationCaseSource.batch_id == batch_id,
        )
    )
    citations = citations_result.scalars().all()

    return {
        "batch": batch,
        "cases": cases,
        "citations": citations,
    }


async def accept_case(
    db: AsyncSession,
    batch_id: uuid.UUID,
    case_id: uuid.UUID,
    edits: Optional[RagCaseAcceptEdits | dict],
    user: User,
) -> ManagedTestCase:
    """Stage accept of a generated case. Handler commits.

    Applies optional edits and increments the batch accept counter atomically.
    A repeat or conflicting disposition returns 409 without further mutation.
    """
    batch = await _get_batch_or_404(db, batch_id, user, for_update=True)

    result = await db.execute(
        select(ManagedTestCase).where(
            ManagedTestCase.id == case_id,
            ManagedTestCase.generation_batch_id == batch_id,
        ).with_for_update()
    )
    case = result.scalar_one_or_none()
    if not case:
        raise HTTPException(status_code=404, detail="Generated case not found in this batch")
    await _ensure_initial_undisposed_draft(
        db,
        case,
        requested_action="accept",
    )

    # Faithfulness gate (Tier 2 item 9). Checked BEFORE edits are applied and
    # before the status flips, so a blocked case is left exactly as it was with
    # a reason attached rather than half-accepted.
    #
    # `check_accept` returns allow=True when the `rag_faithfulness_gate` flag is
    # off (the default), when the case predates the evaluator, or when it meets
    # the threshold — so this is a no-op for deployments that have not opted in.
    from app.services.rag_faithfulness_service import check_accept  # noqa: PLC0415

    decision = await check_accept(case, db=db)
    if not decision.get("allow"):
        logger.info(
            "case_accept_blocked_by_faithfulness",
            case_id=str(case_id),
            batch_id=str(batch_id),
            score=decision.get("score"),
            threshold=decision.get("threshold"),
        )
        # 409, not 403: the caller is permitted to do this, the case is not
        # ready. The reason is staged on the row and returned so the UI can
        # show why without a second request.
        raise HTTPException(
            status_code=409,
            detail={
                "error": "faithfulness_below_threshold",
                "message": decision.get("reason"),
                "score": decision.get("score"),
                "threshold": decision.get("threshold"),
            },
        )

    # Generated cases now enter the authored lifecycle as draft. Acceptance
    # is therefore a review-workflow decision, not a lifecycle status write.
    changed_fields: list[str] = []
    if edits:
        validated_edits = (
            edits
            if isinstance(edits, RagCaseAcceptEdits)
            else RagCaseAcceptEdits.model_validate(edits)
        )
        edit_values = validated_edits.model_dump(exclude_unset=True)
        apply_model_updates(case, edit_values)
        changed_fields.extend(edit_values)
    if changed_fields:
        case.version += 1
        stage_test_case_snapshot(
            db,
            case,
            actor_id=user.id,
            change_type="updated",
            change_summary="Edited while accepting generated case",
            changed_fields=sorted(changed_fields),
        )
    await audit_event(
        db, "test_case", case.id, case.project_id, GENERATION_ACCEPTED_ACTION, user
    )
    batch.cases_accepted = (batch.cases_accepted or 0) + 1
    await db.flush()

    logger.info("case_accepted_batch", case_id=case_id, batch_id=batch_id)
    return case


async def reject_case(
    db: AsyncSession,
    batch_id: uuid.UUID,
    case_id: uuid.UUID,
    reason: Optional[str],
    user: User,
) -> None:
    """Stage reject of a generated case. Handler commits."""
    batch = await _get_batch_or_404(db, batch_id, user, for_update=True)

    result = await db.execute(
        select(ManagedTestCase).where(
            ManagedTestCase.id == case_id,
            ManagedTestCase.generation_batch_id == batch_id,
        ).with_for_update()
    )
    case = result.scalar_one_or_none()
    if not case:
        raise HTTPException(status_code=404, detail="Generated case not found in this batch")
    await _ensure_initial_undisposed_draft(
        db,
        case,
        requested_action="reject",
    )

    normalized_reason = (reason or "").strip() or None
    if normalized_reason is not None and len(normalized_reason) > 500:
        raise HTTPException(
            status_code=422,
            detail="reason must be at most 500 characters",
        )
    if normalized_reason:
        case.description = f"[Rejected: {normalized_reason}]\n\n{case.description or ''}"
    # The RAG endpoint is QA_ENGINEER-gated. Model its rejection as an atomic
    # human review sequence so this workflow cannot bypass the lifecycle owner.
    await transition(
        db,
        case.id,
        LifecycleAction.REQUEST_REVIEW,
        user,
        changed_fields=["description"] if normalized_reason else None,
    )
    await transition(db, case.id, LifecycleAction.CLAIM_REVIEW, user)
    await transition(
        db,
        case.id,
        LifecycleAction.REJECT,
        user,
        reason=normalized_reason,
        notes=normalized_reason,
    )
    await audit_event(
        db,
        "test_case",
        case.id,
        case.project_id,
        GENERATION_REJECTED_ACTION,
        user,
        reason=normalized_reason,
    )
    batch.cases_rejected = (batch.cases_rejected or 0) + 1
    await db.flush()

    logger.info(
        "case_rejected_batch_reason",
        case_id=case_id,
        batch_id=batch_id,
        reason=normalized_reason,
    )


async def bulk_accept(
    db: AsyncSession,
    batch_id: uuid.UUID,
    case_ids: list[uuid.UUID],
    user: User,
    edits_by_case: Optional[dict[uuid.UUID, RagCaseAcceptEdits]] = None,
) -> list[ManagedTestCase]:
    """Stage acceptance of multiple cases. Handler commits once for the
    whole batch, so bulk-accept costs one transaction instead of N.
    """
    accepted = []
    seen: set[uuid.UUID] = set()
    for cid in case_ids:
        if cid in seen:
            continue
        seen.add(cid)
        case = await accept_case(
            db,
            batch_id,
            cid,
            edits=(edits_by_case or {}).get(cid),
            user=user,
        )
        accepted.append(case)
    return accepted
