"""
Action Policy Service -- Phase 4 Safety & HITL.

Centralised pre-mutation policy checks for high-impact actions.
Ensures that mutating actions (defect promotion, Jira ticket creation,
release overrides) go through explicit approval before execution.

Action lifecycle:  suggested -> pending_review -> approved -> executed
                                               |-> rejected
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from enum import Enum as PyEnum
from typing import Any, Optional

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger("services.action_policy")


# ── Action states ────────────────────────────────────────────────────────────

class ActionStatus(str, PyEnum):
    SUGGESTED = "suggested"
    PENDING_REVIEW = "pending_review"
    APPROVED = "approved"
    EXECUTED = "executed"
    REJECTED = "rejected"


class ActionType(str, PyEnum):
    DEFECT_PROMOTION = "defect_promotion"
    JIRA_TICKET_CREATION = "jira_ticket_creation"
    RELEASE_OVERRIDE = "release_override"


# ── Severity thresholds that determine auto-approve vs. manual review ────────

# Defects at or above this severity always require human review
_SEVERITY_REQUIRES_REVIEW = {"CRITICAL", "HIGH"}

# Release overrides that flip from NO_GO to GO always require review
_RISKY_OVERRIDES = {("NO_GO", "GO"), ("NO_GO", "CONDITIONAL_GO")}


def requires_approval(
    action_type: ActionType,
    *,
    severity: Optional[str] = None,
    has_jira_key: bool = False,
    override_from: Optional[str] = None,
    override_to: Optional[str] = None,
    confidence_score: Optional[int] = None,
) -> bool:
    """
    Determine whether an action requires human approval before execution.

    Returns True when the action should be held in pending_review state.
    """
    if action_type == ActionType.DEFECT_PROMOTION:
        # All Jira-bound promotions require approval
        if has_jira_key:
            return True
        # High/critical severity always needs review
        if severity and severity.upper() in _SEVERITY_REQUIRES_REVIEW:
            return True
        # Low AI confidence means human should verify
        if confidence_score is not None and confidence_score < 60:
            return True
        return False

    if action_type == ActionType.JIRA_TICKET_CREATION:
        # External system mutation always requires approval
        return True

    if action_type == ActionType.RELEASE_OVERRIDE:
        # Risky direction changes require extra scrutiny
        if override_from and override_to:
            if (override_from, override_to) in _RISKY_OVERRIDES:
                return True
        return False

    return True  # Unknown action types require approval by default


async def check_defect_promotion_policy(
    db: AsyncSession,
    *,
    project_id: str,
    severity: str,
    has_jira_key: bool,
    confidence_score: Optional[int] = None,
    is_duplicate: bool = False,
) -> dict[str, Any]:
    """
    Evaluate policy for a defect promotion action.

    Returns:
        {
            "requires_approval": bool,
            "initial_status": ActionStatus,
            "policy_reasons": list[str],
        }
    """
    reasons: list[str] = []

    if is_duplicate:
        reasons.append("Duplicate defect detected — promotion blocked")
        return {
            "requires_approval": True,
            "initial_status": ActionStatus.REJECTED,
            "policy_reasons": reasons,
        }

    needs_approval = requires_approval(
        ActionType.DEFECT_PROMOTION,
        severity=severity,
        has_jira_key=has_jira_key,
        confidence_score=confidence_score,
    )

    if needs_approval:
        if has_jira_key:
            reasons.append("Jira ticket creation requires human approval")
        if severity and severity.upper() in _SEVERITY_REQUIRES_REVIEW:
            reasons.append(f"Severity {severity} requires human review")
        if confidence_score is not None and confidence_score < 60:
            reasons.append(f"Low AI confidence ({confidence_score}%) requires review")

    return {
        "requires_approval": needs_approval,
        "initial_status": ActionStatus.PENDING_REVIEW if needs_approval else ActionStatus.APPROVED,
        "policy_reasons": reasons,
    }


async def check_release_override_policy(
    *,
    current_recommendation: str,
    new_recommendation: str,
    actor_role: str,
) -> dict[str, Any]:
    """
    Evaluate policy for a release override action.

    Returns policy evaluation result with reasons.
    """
    reasons: list[str] = []

    needs_approval = requires_approval(
        ActionType.RELEASE_OVERRIDE,
        override_from=current_recommendation,
        override_to=new_recommendation,
    )

    if needs_approval:
        reasons.append(
            f"Override from {current_recommendation} to {new_recommendation} "
            "is a high-risk direction change"
        )

    # QA_LEAD and ADMIN can self-approve non-risky overrides
    if actor_role in ("QA_LEAD", "ADMIN") and not needs_approval:
        return {
            "requires_approval": False,
            "initial_status": ActionStatus.APPROVED,
            "policy_reasons": ["Self-approved by authorized role"],
        }

    return {
        "requires_approval": needs_approval,
        "initial_status": ActionStatus.PENDING_REVIEW if needs_approval else ActionStatus.APPROVED,
        "policy_reasons": reasons,
    }


async def approve_action(
    db: AsyncSession,
    *,
    model_class: Any,
    record_id: uuid.UUID,
    approver_id: uuid.UUID,
    approver_name: str,
) -> bool:
    """
    Approve a pending action (defect or release decision).
    Updates approval_status, approved_by, approved_at.
    Returns True if the record was found and updated.

    Stage-only: the router handler owns ``db.commit()`` so the approval
    status flip can land in the same transaction as any follow-up work
    (Jira ticket linking, audit row, etc.).
    """
    result = await db.execute(
        update(model_class)
        .where(
            model_class.id == record_id,
            model_class.approval_status == ActionStatus.PENDING_REVIEW,
        )
        .values(
            approval_status=ActionStatus.APPROVED,
            approved_by=approver_id,
            approved_at=datetime.now(timezone.utc),
        )
    )
    if result.rowcount > 0:
        logger.info(
            "Action approved: %s %s by %s",
            model_class.__tablename__,
            record_id,
            approver_name,
        )
        return True
    return False


async def reject_action(
    db: AsyncSession,
    *,
    model_class: Any,
    record_id: uuid.UUID,
    rejector_id: uuid.UUID,
    rejector_name: str,
    reason: str,
) -> bool:
    """
    Reject a pending action.
    Returns True if the record was found and updated.

    Stage-only: the router handler owns ``db.commit()``.
    """
    result = await db.execute(
        update(model_class)
        .where(
            model_class.id == record_id,
            model_class.approval_status == ActionStatus.PENDING_REVIEW,
        )
        .values(
            approval_status=ActionStatus.REJECTED,
            approved_by=rejector_id,
            approved_at=datetime.now(timezone.utc),
        )
    )
    if result.rowcount > 0:
        logger.info(
            "Action rejected: %s %s by %s — reason: %s",
            model_class.__tablename__,
            record_id,
            rejector_name,
            reason,
        )
        return True
    return False
