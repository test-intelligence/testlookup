"""Authoritative lifecycle state machine for authored test cases.

Every public helper in this module stages database mutations only.  Routers own
the transaction boundary so state, review ownership, immutable snapshots, and
audit records are committed atomically.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.postgres import (
    ManagedTestCase,
    TestCaseLifecycleState,
    TestCaseReview,
    TestCaseVersion,
    User,
    UserRole,
)
from app.services.test_management_audit_service import audit_event
from app.services.test_management_metrics_service import (
    stage_test_case_state_refresh,
    stage_test_management_counter,
)


class LifecycleAction(str, Enum):
    REQUEST_REVIEW = "request_review"
    CLAIM_REVIEW = "claim_review"
    WITHDRAW_REVIEW = "withdraw_review"
    UNCLAIM = "unclaim"
    APPROVE = "approve"
    REJECT = "reject"
    REQUEST_CHANGES = "request_changes"
    ACTIVATE = "activate"
    FLAG_STALE = "flag_stale"
    REVISE = "revise"
    DEPRECATE = "deprecate"
    REINSTATE = "reinstate"
    ARCHIVE = "archive"


# Public so policy tests and API documentation can ratchet the contract.
ALLOWED_TRANSITIONS: dict[LifecycleAction, dict[str, str]] = {
    LifecycleAction.REQUEST_REVIEW: {
        TestCaseLifecycleState.DRAFT.value: TestCaseLifecycleState.REVIEW_REQUESTED.value,
        TestCaseLifecycleState.REJECTED.value: TestCaseLifecycleState.REVIEW_REQUESTED.value,
    },
    LifecycleAction.CLAIM_REVIEW: {
        TestCaseLifecycleState.REVIEW_REQUESTED.value: TestCaseLifecycleState.UNDER_REVIEW.value,
    },
    LifecycleAction.WITHDRAW_REVIEW: {
        TestCaseLifecycleState.REVIEW_REQUESTED.value: TestCaseLifecycleState.DRAFT.value,
    },
    LifecycleAction.UNCLAIM: {
        TestCaseLifecycleState.UNDER_REVIEW.value: TestCaseLifecycleState.REVIEW_REQUESTED.value,
    },
    LifecycleAction.APPROVE: {
        TestCaseLifecycleState.UNDER_REVIEW.value: TestCaseLifecycleState.APPROVED.value,
    },
    LifecycleAction.REJECT: {
        TestCaseLifecycleState.UNDER_REVIEW.value: TestCaseLifecycleState.REJECTED.value,
    },
    LifecycleAction.REQUEST_CHANGES: {
        TestCaseLifecycleState.UNDER_REVIEW.value: TestCaseLifecycleState.DRAFT.value,
    },
    LifecycleAction.ACTIVATE: {
        TestCaseLifecycleState.APPROVED.value: TestCaseLifecycleState.ACTIVE.value,
    },
    LifecycleAction.FLAG_STALE: {
        TestCaseLifecycleState.APPROVED.value: TestCaseLifecycleState.NEEDS_UPDATE.value,
        TestCaseLifecycleState.ACTIVE.value: TestCaseLifecycleState.NEEDS_UPDATE.value,
    },
    LifecycleAction.REVISE: {
        TestCaseLifecycleState.REJECTED.value: TestCaseLifecycleState.DRAFT.value,
        TestCaseLifecycleState.NEEDS_UPDATE.value: TestCaseLifecycleState.DRAFT.value,
    },
    LifecycleAction.DEPRECATE: {
        TestCaseLifecycleState.DRAFT.value: TestCaseLifecycleState.DEPRECATED.value,
        TestCaseLifecycleState.REJECTED.value: TestCaseLifecycleState.DEPRECATED.value,
        TestCaseLifecycleState.APPROVED.value: TestCaseLifecycleState.DEPRECATED.value,
        TestCaseLifecycleState.ACTIVE.value: TestCaseLifecycleState.DEPRECATED.value,
        TestCaseLifecycleState.NEEDS_UPDATE.value: TestCaseLifecycleState.DEPRECATED.value,
    },
    LifecycleAction.REINSTATE: {
        TestCaseLifecycleState.DEPRECATED.value: TestCaseLifecycleState.DRAFT.value,
        TestCaseLifecycleState.ARCHIVED.value: TestCaseLifecycleState.DRAFT.value,
    },
    LifecycleAction.ARCHIVE: {
        TestCaseLifecycleState.DEPRECATED.value: TestCaseLifecycleState.ARCHIVED.value,
    },
}

REVIEWER_ROLES = {UserRole.QA_ENGINEER.value, UserRole.QA_LEAD.value, UserRole.ADMIN.value}
LEAD_ROLES = {UserRole.QA_LEAD.value, UserRole.ADMIN.value}
REASON_REQUIRED_ACTIONS = {
    LifecycleAction.FLAG_STALE,
    LifecycleAction.DEPRECATE,
    LifecycleAction.REINSTATE,
    LifecycleAction.ARCHIVE,
}
DECISION_ACTIONS = {
    LifecycleAction.APPROVE,
    LifecycleAction.REJECT,
    LifecycleAction.REQUEST_CHANGES,
}
LIFECYCLE_FLAG_KEY = "test_case_lifecycle_v2"


@dataclass(frozen=True)
class LifecycleTransitionResult:
    case: ManagedTestCase
    review: Optional[TestCaseReview] = None


def _role_value(user: User) -> str:
    role = user.role
    return role.value if isinstance(role, Enum) else str(role)


def _conflict(detail: str) -> HTTPException:
    return HTTPException(status_code=409, detail=detail)


def _policy_snapshot() -> dict:
    return {
        "author_cannot_approve": True,
        "decision_requires_current_claimant": True,
        "reviewer_roles": sorted(REVIEWER_ROLES),
        "lead_only_actions": ["archive", "deprecate", "reinstate"],
    }


def _can_withdraw(test_case: ManagedTestCase, actor: User) -> bool:
    return test_case.author_id == actor.id or _role_value(actor) in LEAD_ROLES


def _can_unclaim(test_case: ManagedTestCase, actor: User) -> bool:
    return test_case.reviewer_id == actor.id or _role_value(actor) in LEAD_ROLES


async def require_lifecycle_v2_enabled(
    db: AsyncSession,
    test_case: ManagedTestCase,
    actor: User,
) -> None:
    """Gate direct lifecycle APIs while the compatibility shims remain live."""
    from app.services.feature_flags import is_enabled

    enabled = await is_enabled(
        LIFECYCLE_FLAG_KEY,
        db=db,
        project_id=test_case.project_id,
        user=actor,
    )
    if not enabled:
        raise HTTPException(
            status_code=404,
            detail=(
                "Direct test-case lifecycle endpoints are disabled for this "
                "project; use the compatibility review endpoints"
            ),
        )


async def lifecycle_actions_for(
    db: AsyncSession,
    test_case: ManagedTestCase,
    actor: User,
) -> list[str]:
    """Return advertised direct actions only when lifecycle v2 is enabled."""
    from app.services.feature_flags import is_enabled

    if not await is_enabled(
        LIFECYCLE_FLAG_KEY,
        db=db,
        project_id=test_case.project_id,
        user=actor,
    ):
        return []
    return allowed_actions_for(test_case, actor)


def stage_test_case_snapshot(
    db: AsyncSession,
    test_case: ManagedTestCase,
    *,
    actor_id: Optional[uuid.UUID],
    change_type: str,
    change_summary: Optional[str],
    changed_fields: Optional[list[str]] = None,
) -> TestCaseVersion:
    """Stage a complete immutable snapshot at ``test_case.version``."""
    version = TestCaseVersion(
        test_case_id=test_case.id,
        version=test_case.version,
        title=test_case.title,
        description=getattr(test_case, "description", None),
        objective=getattr(test_case, "objective", None),
        preconditions=getattr(test_case, "preconditions", None),
        steps=getattr(test_case, "steps", None),
        parameters=getattr(test_case, "parameters", None),
        expected_result=getattr(test_case, "expected_result", None),
        test_data=getattr(test_case, "test_data", None),
        test_type=getattr(test_case, "test_type", None),
        priority=getattr(test_case, "priority", None),
        severity=getattr(test_case, "severity", None),
        feature_area=getattr(test_case, "feature_area", None),
        suite_name=getattr(test_case, "suite_name", None),
        test_suite_id=getattr(test_case, "test_suite_id", None),
        tags=getattr(test_case, "tags", None),
        estimated_duration_minutes=getattr(test_case, "estimated_duration_minutes", None),
        is_automated=getattr(test_case, "is_automated", None),
        automation_status=getattr(test_case, "automation_status", None),
        test_fingerprint=getattr(test_case, "test_fingerprint", None),
        status=test_case.status,
        changed_by_id=actor_id,
        change_summary=change_summary,
        change_type=change_type,
        changed_fields=changed_fields,
    )
    db.add(version)
    stage_test_case_state_refresh(db, test_case.project_id)
    return version


def allowed_actions_for(test_case: ManagedTestCase, actor: User) -> list[str]:
    """Return actions this actor can currently attempt without database I/O."""
    role = _role_value(actor)
    actions: list[str] = []
    for action, by_source in ALLOWED_TRANSITIONS.items():
        if test_case.status not in by_source:
            continue
        if action in {LifecycleAction.CLAIM_REVIEW, *DECISION_ACTIONS} and role not in REVIEWER_ROLES:
            continue
        if action in {LifecycleAction.DEPRECATE, LifecycleAction.REINSTATE, LifecycleAction.ARCHIVE} and role not in LEAD_ROLES:
            continue
        if action in DECISION_ACTIONS and test_case.reviewer_id != actor.id:
            continue
        if action == LifecycleAction.UNCLAIM and not _can_unclaim(test_case, actor):
            continue
        if action == LifecycleAction.APPROVE and test_case.author_id == actor.id:
            continue
        if action == LifecycleAction.WITHDRAW_REVIEW and not _can_withdraw(test_case, actor):
            continue
        actions.append(action.value)
    return actions


def transition_availability_for(test_case: ManagedTestCase, actor: User) -> list[dict]:
    """Return the direct-array API contract for every lifecycle action."""
    allowed = set(allowed_actions_for(test_case, actor))
    result: list[dict] = []
    for action in LifecycleAction:
        is_allowed = action.value in allowed
        blocked_reason: Optional[str] = None
        if not is_allowed:
            if test_case.status not in ALLOWED_TRANSITIONS[action]:
                blocked_reason = f"Not available from {test_case.status}"
            elif action == LifecycleAction.APPROVE and test_case.author_id == actor.id:
                blocked_reason = "Authors cannot approve their own test cases"
            elif action in DECISION_ACTIONS:
                blocked_reason = "Only the current claimant may perform this action"
            elif action == LifecycleAction.UNCLAIM:
                blocked_reason = "Only the current claimant or a lead may unclaim"
            elif action in {LifecycleAction.DEPRECATE, LifecycleAction.REINSTATE, LifecycleAction.ARCHIVE}:
                blocked_reason = "Requires QA_LEAD or ADMIN"
            else:
                blocked_reason = "Insufficient permissions"
        result.append(
            {
                "action": action.value,
                "allowed": is_allowed,
                "blocked_reason": blocked_reason,
            }
        )
    return result


async def _lock_case(db: AsyncSession, case_id: uuid.UUID) -> ManagedTestCase:
    case = (
        await db.execute(
            select(ManagedTestCase)
            .where(ManagedTestCase.id == case_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if case is None:
        raise HTTPException(status_code=404, detail="Test case not found")
    return case


async def _open_review(
    db: AsyncSession,
    case_id: uuid.UUID,
    *,
    status: str,
) -> Optional[TestCaseReview]:
    return (
        await db.execute(
            select(TestCaseReview)
            .where(
                TestCaseReview.test_case_id == case_id,
                TestCaseReview.status == status,
            )
            .order_by(TestCaseReview.created_at.asc())
            .limit(1)
            .with_for_update()
        )
    ).scalars().first()


async def transition(
    db: AsyncSession,
    case_id: uuid.UUID,
    action: LifecycleAction | str,
    actor: User,
    *,
    reason: Optional[str] = None,
    notes: Optional[str] = None,
    changed_fields: Optional[list[str]] = None,
) -> LifecycleTransitionResult:
    """Stage one validated lifecycle transition under a row lock."""
    try:
        action = LifecycleAction(action)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"Unknown lifecycle action: {action}") from exc

    normalized_reason = (reason or "").strip() or None
    if normalized_reason is not None and len(normalized_reason) > 500:
        raise HTTPException(status_code=422, detail="reason must be at most 500 characters")
    if action == LifecycleAction.FLAG_STALE and normalized_reason is not None and len(normalized_reason) > 100:
        raise HTTPException(
            status_code=422,
            detail="reason must be at most 100 characters for flag_stale",
        )
    if action in REASON_REQUIRED_ACTIONS and normalized_reason is None:
        raise HTTPException(status_code=422, detail=f"reason is required for {action.value}")

    test_case = await _lock_case(db, case_id)
    old_status = test_case.status
    new_status = ALLOWED_TRANSITIONS.get(action, {}).get(old_status)
    if new_status is None:
        raise HTTPException(
            status_code=409,
            detail={
                "current_state": old_status,
                "attempted_action": action.value,
                "allowed_actions": allowed_actions_for(test_case, actor),
            },
        )

    role = _role_value(actor)
    if action in {LifecycleAction.CLAIM_REVIEW, *DECISION_ACTIONS} and role not in REVIEWER_ROLES:
        raise HTTPException(status_code=403, detail="Insufficient permissions to review")
    if action in {LifecycleAction.DEPRECATE, LifecycleAction.REINSTATE, LifecycleAction.ARCHIVE} and role not in LEAD_ROLES:
        raise HTTPException(status_code=403, detail="This action requires QA_LEAD or ADMIN")
    if action == LifecycleAction.APPROVE and test_case.author_id == actor.id:
        raise HTTPException(status_code=403, detail="A test case author cannot approve their own case")

    now = datetime.now(timezone.utc)
    review: Optional[TestCaseReview] = None

    if action == LifecycleAction.REQUEST_REVIEW:
        if await _open_review(db, case_id, status="pending") is not None:
            raise _conflict("A pending review already exists")
        review = TestCaseReview(
            test_case_id=case_id,
            requested_by_id=actor.id,
            status="pending",
        )
        db.add(review)
    elif action == LifecycleAction.CLAIM_REVIEW:
        review = await _open_review(db, case_id, status="pending")
        if review is None:
            raise _conflict("No pending review is available to claim")
        review.status = "in_progress"
        review.reviewer_id = actor.id
        test_case.reviewer_id = actor.id
    elif action == LifecycleAction.WITHDRAW_REVIEW:
        review = await _open_review(db, case_id, status="pending")
        if review is None:
            raise _conflict("No pending review is available to withdraw")
        if not _can_withdraw(test_case, actor):
            raise HTTPException(
                status_code=403,
                detail="Only the author or a lead may withdraw this review",
            )
        review.status = "changes_requested"
        review.human_notes = notes or "Review withdrawn"
        review.reviewed_at = now
    elif action in DECISION_ACTIONS or action == LifecycleAction.UNCLAIM:
        review = await _open_review(db, case_id, status="in_progress")
        if review is None:
            raise _conflict("No claimed review exists")
        if action == LifecycleAction.UNCLAIM:
            if not _can_unclaim(test_case, actor):
                raise HTTPException(
                    status_code=403,
                    detail="Only the current claimant or a lead may unclaim",
                )
        elif review.reviewer_id != actor.id or test_case.reviewer_id != actor.id:
            raise HTTPException(status_code=403, detail="Only the current claimant may decide")
        if action == LifecycleAction.UNCLAIM:
            review.status = "pending"
            review.reviewer_id = None
        else:
            review.status = {
                LifecycleAction.APPROVE: "approved",
                LifecycleAction.REJECT: "rejected",
                LifecycleAction.REQUEST_CHANGES: "changes_requested",
            }[action]
            review.human_notes = notes
            review.reviewed_at = now
        test_case.reviewer_id = None

    test_case.status = new_status
    test_case.lifecycle_state_changed_at = now
    test_case.version += 1

    if action == LifecycleAction.APPROVE:
        test_case.approved_at = now
        test_case.approved_by_id = actor.id
    if action == LifecycleAction.FLAG_STALE:
        test_case.needs_update_reason = normalized_reason
    elif action in {LifecycleAction.REVISE, LifecycleAction.REINSTATE}:
        test_case.needs_update_reason = None
    if action == LifecycleAction.DEPRECATE:
        test_case.deprecation_reason = normalized_reason
        test_case.deprecated_at = now
        test_case.deprecated_by_id = actor.id
    elif action == LifecycleAction.REINSTATE:
        test_case.deprecation_reason = None
        test_case.deprecated_at = None
        test_case.deprecated_by_id = None
        test_case.archived_at = None
        test_case.archived_by_id = None
    elif action == LifecycleAction.ARCHIVE:
        test_case.archived_at = now
        test_case.archived_by_id = actor.id

    stage_test_case_snapshot(
        db,
        test_case,
        actor_id=actor.id,
        change_type=action.value,
        change_summary=normalized_reason or notes or action.value.replace("_", " ").title(),
        changed_fields=sorted(set(changed_fields or []) | {"status"}),
    )
    await audit_event(
        db,
        "test_case",
        test_case.id,
        test_case.project_id,
        action.value,
        actor,
        old_values={"status": old_status},
        new_values={"status": new_status},
        details=notes,
        reason=normalized_reason,
        policy_snapshot=_policy_snapshot(),
        transition_from=old_status,
        transition_to=new_status,
    )
    stage_test_management_counter(
        db,
        "transition",
        (str(test_case.project_id), old_status, new_status, role),
    )
    await db.flush()
    return LifecycleTransitionResult(case=test_case, review=review)


async def auto_claim_and_decide(
    db: AsyncSession,
    case_id: uuid.UUID,
    action: LifecycleAction | str,
    actor: User,
    *,
    notes: Optional[str] = None,
) -> LifecycleTransitionResult:
    """Compatibility shim: atomically claim ``review_requested`` then decide."""
    try:
        action = LifecycleAction(action)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"Unknown lifecycle action: {action}") from exc
    if action not in DECISION_ACTIONS:
        raise HTTPException(status_code=422, detail="action must be approve|reject|request_changes")
    test_case = await _lock_case(db, case_id)
    if test_case.status == TestCaseLifecycleState.REVIEW_REQUESTED.value:
        await transition(db, case_id, LifecycleAction.CLAIM_REVIEW, actor)
    return await transition(db, case_id, action, actor, notes=notes)
