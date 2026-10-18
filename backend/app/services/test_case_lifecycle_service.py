"""Authoritative lifecycle state machine for authored test cases.

Every public helper in this module stages database mutations only.  Routers own
the transaction boundary so state, review ownership, immutable snapshots, and
audit records are committed atomically.

W28 (TCM-02, owner decision OR-04): a project may or may not require a review
before a case is Active (``projects.require_case_review``, default off).
``ALLOWED_TRANSITIONS`` is the review-ON table and is unchanged; with review
off :data:`DIRECT_TRANSITIONS` is merged in (Draft / Needs update -> Active,
Active -> Draft). Every helper takes ``review_required`` as a keyword whose
default (``True``) is today's behaviour, so a caller that does not know the
project's setting keeps the governed flow. A VIEWER changes nothing; a QA lead
or admin may approve a case they wrote, with a reason. Deprecate, Archive and
Reinstate stay QA_LEAD or above (the W28 contract's amendment).
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

#: Merged into the table when the project does NOT require review (W28 TCM-02).
DIRECT_TRANSITIONS: dict[LifecycleAction, dict[str, str]] = {
    LifecycleAction.ACTIVATE: {
        TestCaseLifecycleState.DRAFT.value: TestCaseLifecycleState.ACTIVE.value,
        TestCaseLifecycleState.NEEDS_UPDATE.value: TestCaseLifecycleState.ACTIVE.value,
    },
    LifecycleAction.REVISE: {
        TestCaseLifecycleState.ACTIVE.value: TestCaseLifecycleState.DRAFT.value,
    },
}

#: Merged in when review IS required and the case was edited after its approval
#: (``managed_test_cases.edited_since_approval_version`` set, W28 TCM-01).
EDITED_AFTER_APPROVAL_TRANSITIONS: dict[LifecycleAction, dict[str, str]] = {
    LifecycleAction.REQUEST_REVIEW: {
        TestCaseLifecycleState.APPROVED.value: TestCaseLifecycleState.REVIEW_REQUESTED.value,
        TestCaseLifecycleState.ACTIVE.value: TestCaseLifecycleState.REVIEW_REQUESTED.value,
    },
}

REVIEWER_ROLES = {UserRole.QA_ENGINEER.value, UserRole.QA_LEAD.value, UserRole.ADMIN.value}
LEAD_ROLES = {UserRole.QA_LEAD.value, UserRole.ADMIN.value}
#: Every action's floor: a VIEWER changes nothing (W28, the API role gap).
EDITOR_ROLES = {
    UserRole.TESTER.value,
    UserRole.QA_ENGINEER.value,
    UserRole.QA_LEAD.value,
    UserRole.ADMIN.value,
}
#: Deprecate / Reinstate / Archive: LEAD_ROLES, as before W28 (the contract's
#: amendment keeps them QA_LEAD or above).
RETIRE_ACTIONS = {
    LifecycleAction.DEPRECATE,
    LifecycleAction.REINSTATE,
    LifecycleAction.ARCHIVE,
}
RETIRE_ROLES = LEAD_ROLES
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
#: The states from which a lead may approve a case they wrote (with a reason).
SELF_APPROVAL_SOURCES = (
    TestCaseLifecycleState.REVIEW_REQUESTED.value,
    TestCaseLifecycleState.UNDER_REVIEW.value,
)
LIFECYCLE_FLAG_KEY = "test_case_lifecycle_v2"

# ── W28 vocabularies (each in ONE module; tests and G-B import them) ──────────
VERSION_SOURCES = ("person", "ai", "api")
#: Out of new plans, coverage counts and the duplicate queue.
RETIRED_STATES = (
    TestCaseLifecycleState.DEPRECATED.value,
    TestCaseLifecycleState.ARCHIVED.value,
)
#: What a new plan picks by default (the AI plan's pinned rule).
RUNNABLE_STATES = (
    TestCaseLifecycleState.APPROVED.value,
    TestCaseLifecycleState.ACTIVE.value,
)
#: An edit of these marks "edited after approval" (review on, approved / active).
DEFINITION_FIELDS = ("steps", "expected_result", "preconditions")

VIEWER_REFUSAL = "Viewers cannot change test cases"
RETIRE_REFUSAL = "This action requires QA_LEAD or ADMIN"
SELF_APPROVAL_REASON_REQUIRED = "A reason is required to approve your own test case"


@dataclass(frozen=True)
class LifecycleTransitionResult:
    case: ManagedTestCase
    review: Optional[TestCaseReview] = None


def _role_value(user: User) -> str:
    role = user.role
    return role.value if isinstance(role, Enum) else str(role)


def _conflict(detail: str | dict[str, object]) -> HTTPException:
    return HTTPException(status_code=409, detail=detail)


def _policy_snapshot(*, review_required: bool = True, self_approval: bool = False) -> dict:
    return {
        "author_cannot_approve": "unless_lead_with_reason",
        "decision_requires_current_claimant": True,
        "reviewer_roles": sorted(REVIEWER_ROLES),
        "lead_only_actions": ["archive", "deprecate", "reinstate"],
        "review_required": review_required,
        "self_approval": self_approval,
    }


def _can_withdraw(test_case: ManagedTestCase, actor: User) -> bool:
    return test_case.author_id == actor.id or _role_value(actor) in LEAD_ROLES


def _can_unclaim(test_case: ManagedTestCase, actor: User) -> bool:
    return test_case.reviewer_id == actor.id or _role_value(actor) in LEAD_ROLES


def transitions_for(
    *, review_required: bool, edited_after_approval: bool
) -> dict[LifecycleAction, dict[str, str]]:
    """The effective move table for one case, as a fresh copy.

    ``ALLOWED_TRANSITIONS`` (the review-ON table), plus ``DIRECT_TRANSITIONS``
    when the project does not require review, plus
    ``EDITED_AFTER_APPROVAL_TRANSITIONS`` when it does and the case was edited
    after its approval. Never mutates the module constants.
    """
    table: dict[LifecycleAction, dict[str, str]] = {
        action: dict(by_source) for action, by_source in ALLOWED_TRANSITIONS.items()
    }
    extras: list[dict[LifecycleAction, dict[str, str]]] = []
    if not review_required:
        extras.append(DIRECT_TRANSITIONS)
    elif edited_after_approval:
        extras.append(EDITED_AFTER_APPROVAL_TRANSITIONS)
    for extra in extras:
        for action, by_source in extra.items():
            table.setdefault(action, {}).update(by_source)
    return table


def _edited_after_approval(test_case: ManagedTestCase, review_required: bool) -> bool:
    marker = getattr(test_case, "edited_since_approval_version", None)
    return bool(review_required) and marker is not None


def _case_table(
    test_case: ManagedTestCase, review_required: bool
) -> dict[LifecycleAction, dict[str, str]]:
    return transitions_for(
        review_required=review_required,
        edited_after_approval=_edited_after_approval(test_case, review_required),
    )


def is_self_approval(test_case: ManagedTestCase, actor: User) -> bool:
    """A QA lead or admin approving a case they wrote (W28 TCM-02, with a reason).

    From ``review_requested`` (the claim folds into the approval), or from
    ``under_review`` when they hold the claim. A fake without a field is not one.
    """
    author_id = getattr(test_case, "author_id", None)
    actor_id = getattr(actor, "id", None)
    if author_id is None or actor_id is None or author_id != actor_id:
        return False
    if _role_value(actor) not in LEAD_ROLES:
        return False
    status = getattr(test_case, "status", None)
    if status not in SELF_APPROVAL_SOURCES:
        return False
    if status == TestCaseLifecycleState.REVIEW_REQUESTED.value:
        return True
    return bool(getattr(test_case, "reviewer_id", None) == actor_id)


def _target_status(
    test_case: ManagedTestCase,
    actor: User,
    action: LifecycleAction,
    table: dict[LifecycleAction, dict[str, str]],
) -> Optional[str]:
    """Where ``action`` moves the case for this actor, or None when it cannot."""
    target = table.get(action, {}).get(test_case.status)
    if (
        target is None
        and action == LifecycleAction.APPROVE
        and test_case.status == TestCaseLifecycleState.REVIEW_REQUESTED.value
        and is_self_approval(test_case, actor)
    ):
        return TestCaseLifecycleState.APPROVED.value
    return target


def version_source(actor) -> str:
    """``"api"`` for a request authenticated by an API key, else ``"person"``.

    The AI call sites pass ``source="ai"`` themselves (W28 TCM-04).
    """
    from app.core.deps import CREDENTIAL_KIND_API_KEY, credential_kind  # noqa: PLC0415

    if actor is not None and credential_kind(actor) == CREDENTIAL_KIND_API_KEY:
        return "api"
    return "person"


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
    *,
    review_required: Optional[bool] = None,
) -> list[str]:
    """Return advertised direct actions only when lifecycle v2 is enabled.

    ``review_required`` None reads the project's setting (only when the flag
    is on: a flag-off answer is ``[]`` before anything else, as before W28).
    """
    from app.services.feature_flags import is_enabled

    if not await is_enabled(
        LIFECYCLE_FLAG_KEY,
        db=db,
        project_id=test_case.project_id,
        user=actor,
    ):
        return []
    if review_required is None:
        from app.services.test_case_review_policy import (  # noqa: PLC0415
            project_requires_case_review,
        )

        review_required = await project_requires_case_review(db, test_case.project_id)
    return allowed_actions_for(test_case, actor, review_required=review_required)


def stage_test_case_snapshot(
    db: AsyncSession,
    test_case: ManagedTestCase,
    *,
    actor_id: Optional[uuid.UUID],
    change_type: str,
    change_summary: Optional[str],
    changed_fields: Optional[list[str]] = None,
    source: Optional[str] = None,
) -> TestCaseVersion:
    """Stage a complete immutable snapshot at ``test_case.version``.

    ``source`` (W28 TCM-04) is ``person`` / ``ai`` / ``api`` (VERSION_SOURCES),
    or None when the caller does not know; the snapshot also keeps the case's
    owner (``assignee_id``) at this version.
    """
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
        source=source,
        assignee_id=getattr(test_case, "assignee_id", None),
    )
    db.add(version)
    stage_test_case_state_refresh(db, test_case.project_id)
    return version


def allowed_actions_for(
    test_case: ManagedTestCase,
    actor: User,
    *,
    review_required: bool = True,
) -> list[str]:
    """Return actions this actor can currently attempt without database I/O.

    ``review_required`` is the project's setting; the default (True) is the
    governed table every caller had before W28.
    """
    role = _role_value(actor)
    if role not in EDITOR_ROLES:
        return []
    table = _case_table(test_case, review_required)
    self_approval = is_self_approval(test_case, actor)
    actions: list[str] = []
    for action in table:
        if _target_status(test_case, actor, action, table) is None:
            continue
        if action in {LifecycleAction.CLAIM_REVIEW, *DECISION_ACTIONS} and role not in REVIEWER_ROLES:
            continue
        if action in RETIRE_ACTIONS and role not in RETIRE_ROLES:
            continue
        if action == LifecycleAction.APPROVE and self_approval:
            actions.append(action.value)
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


def reason_required_actions_for(
    test_case: ManagedTestCase,
    actor: User,
    *,
    review_required: bool = True,
) -> list[str]:
    """The allowed actions that need a reason FOR THIS ACTOR, in allowed order.

    ``REASON_REQUIRED_ACTIONS``, plus ``approve`` when it is a lead's
    self-approval.
    """
    reasoned = {action.value for action in REASON_REQUIRED_ACTIONS}
    if is_self_approval(test_case, actor):
        reasoned.add(LifecycleAction.APPROVE.value)
    return [
        action
        for action in allowed_actions_for(test_case, actor, review_required=review_required)
        if action in reasoned
    ]


def transition_availability_for(
    test_case: ManagedTestCase,
    actor: User,
    *,
    review_required: bool = True,
) -> list[dict]:
    """Return the direct-array API contract for every lifecycle action."""
    allowed = set(allowed_actions_for(test_case, actor, review_required=review_required))
    table = _case_table(test_case, review_required)
    is_viewer = _role_value(actor) not in EDITOR_ROLES
    self_approval = is_self_approval(test_case, actor)
    result: list[dict] = []
    for action in LifecycleAction:
        is_allowed = action.value in allowed
        blocked_reason: Optional[str] = None
        if not is_allowed:
            if is_viewer:
                blocked_reason = VIEWER_REFUSAL
            elif _target_status(test_case, actor, action, table) is None:
                if (
                    review_required
                    and test_case.status in DIRECT_TRANSITIONS.get(action, {})
                    and action == LifecycleAction.ACTIVATE
                ):
                    blocked_reason = "Review is required in this project: request a review first"
                else:
                    blocked_reason = f"Not available from {test_case.status}"
            elif action == LifecycleAction.APPROVE and test_case.author_id == actor.id:
                blocked_reason = "Authors cannot approve their own test cases"
            elif action in DECISION_ACTIONS:
                blocked_reason = "Only the current claimant may perform this action"
            elif action == LifecycleAction.UNCLAIM:
                blocked_reason = "Only the current claimant or a lead may unclaim"
            elif action in RETIRE_ACTIONS:
                blocked_reason = "Requires QA_LEAD or ADMIN"
            else:
                blocked_reason = "Insufficient permissions"
        result.append(
            {
                "action": action.value,
                "allowed": is_allowed,
                "blocked_reason": blocked_reason,
                "requires_reason": action in REASON_REQUIRED_ACTIONS
                or (action == LifecycleAction.APPROVE and self_approval),
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
    expected_version: Optional[int] = None,
    review_required: bool = True,
) -> LifecycleTransitionResult:
    """Stage one validated lifecycle transition under a row lock.

    ``review_required`` is the project's setting (W28 TCM-02); the default is
    the governed table every caller had before W28. The order of the checks is
    pinned by the tests: an unknown action, then the reason (before the lock),
    the lock, a stale ``expected_version``, a move the table does not allow,
    and only then the roles.
    """
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
    if expected_version is not None and test_case.version != expected_version:
        raise _conflict(
            {
                "current_version": test_case.version,
                "expected_version": expected_version,
                "message": "Test case changed; refresh before applying this action",
            }
        )
    old_status = test_case.status
    table = _case_table(test_case, review_required)
    new_status = _target_status(test_case, actor, action, table)
    if new_status is None:
        raise HTTPException(
            status_code=409,
            detail={
                "current_state": old_status,
                "attempted_action": action.value,
                "allowed_actions": allowed_actions_for(
                    test_case, actor, review_required=review_required
                ),
            },
        )

    role = _role_value(actor)
    if role not in EDITOR_ROLES:
        raise HTTPException(status_code=403, detail=VIEWER_REFUSAL)
    if action in {LifecycleAction.CLAIM_REVIEW, *DECISION_ACTIONS} and role not in REVIEWER_ROLES:
        raise HTTPException(status_code=403, detail="Insufficient permissions to review")
    if action in RETIRE_ACTIONS and role not in RETIRE_ROLES:
        raise HTTPException(status_code=403, detail=RETIRE_REFUSAL)
    self_approval = False
    if action == LifecycleAction.APPROVE and test_case.author_id == actor.id:
        if role in LEAD_ROLES and old_status in SELF_APPROVAL_SOURCES:
            if not is_self_approval(test_case, actor):
                # Under review, claimed by someone else: theirs to decide.
                raise HTTPException(status_code=403, detail="Only the current claimant may decide")
            approval_reason = normalized_reason or (notes or "").strip()
            if not approval_reason:
                raise HTTPException(status_code=422, detail=SELF_APPROVAL_REASON_REQUIRED)
            # The audit reason and the version summary are 500 characters.
            normalized_reason = normalized_reason or approval_reason[:500]
            self_approval = True
        else:
            raise HTTPException(status_code=403, detail="A test case author cannot approve their own case")

    now = datetime.now(timezone.utc)
    review: Optional[TestCaseReview] = None

    if self_approval and old_status == TestCaseLifecycleState.REVIEW_REQUESTED.value:
        # A lead approving their own case straight from Review requested: the
        # pending review row is decided in THIS transition (one version, one
        # audit row; no separate claim version).
        review = await _open_review(db, case_id, status="pending")
        if review is None:
            raise _conflict("No pending review is available to approve")
        review.status = "approved"
        review.reviewer_id = actor.id
        review.human_notes = notes or normalized_reason
        review.reviewed_at = now
        test_case.reviewer_id = None
    elif action == LifecycleAction.REQUEST_REVIEW:
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
            review.human_notes = (notes or normalized_reason) if self_approval else notes
            review.reviewed_at = now
        test_case.reviewer_id = None

    test_case.status = new_status
    test_case.lifecycle_state_changed_at = now
    test_case.version += 1
    # "Edited after approval" (W28 TCM-01): an approval settles it, and so does
    # leaving Approved / Active. Activate keeps it.
    if getattr(test_case, "edited_since_approval_version", None) is not None and (
        action == LifecycleAction.APPROVE or new_status not in RUNNABLE_STATES
    ):
        test_case.edited_since_approval_version = None

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
        source=version_source(actor),
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
        policy_snapshot=_policy_snapshot(
            review_required=bool(review_required), self_approval=self_approval
        ),
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
