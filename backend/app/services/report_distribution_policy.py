"""Whether an AI report may leave the system unreviewed (architecture E8.4, section 8.2).

A report response inside the app carries its review state (E8.3) and a person
reading it can see that state. A file, a share link or a CI gate value is
different: once it leaves, nothing travels with it that says "a human has not
looked at this yet". So distribution is decided here, per run and per channel:

* **reviewed** (``accepted``) or **not AI-generated** -- goes out as before;
* **pending review** -- goes out only when the project sets
  ``allow_unreviewed_distribution``, or a QA lead explicitly asks for it on an
  interactive export (``include_unreviewed``). Either way it is watermarked
  ``DRAFT - AI-generated, not human-reviewed`` and the inclusion is audited;
* **rejected or superseded** -- never distributed as a draft. A report a person
  rejected is not a draft awaiting review.

Rollout: ``settings.REVIEW_GATE_ENFORCED``
------------------------------------------
Every run that finished before E8.1 has no accepted review, so enforcing this on
merge would, for every project at once, stop report files and share links and
flip the value CI pipelines read from ``GO`` to ``PENDING_REVIEW``. That is a
product decision about timing, not something to happen as a side effect of a
deploy. With the flag off (the default) nothing is refused and no value
changes; each refusal that WOULD have happened is recorded in
``access_audit_logs`` as ``ai_report.distribution_would_refuse``, so the impact
can be measured before the switch is thrown.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any, Optional

from sqlalchemy import select

from app.models.postgres import Project
from app.services.review_envelope import (
    ReviewEnvelope,
    not_ai_generated,
    review_envelope_for_run,
)

__all__ = [
    "DRAFT_WATERMARK",
    "DistributionDecision",
    "apply_release_review_gate",
    "decide_run_distribution",
    "gate_enforced",
    "record_distribution",
    "refusal_detail",
]

DRAFT_WATERMARK = "DRAFT - AI-generated, not human-reviewed"

REVIEWED = "reviewed"
NOT_AI_GENERATED = "not_ai_generated"
PROJECT_ALLOWS_DRAFTS = "project_allows_drafts"
INCLUDE_UNREVIEWED = "include_unreviewed_requested"
REFUSED = "refused_unreviewed"
WOULD_REFUSE = "not_enforced_would_refuse"

_AUDIT_ACTIONS = {
    PROJECT_ALLOWS_DRAFTS: "ai_report.distributed_unreviewed",
    INCLUDE_UNREVIEWED: "ai_report.distributed_unreviewed",
    REFUSED: "ai_report.distribution_refused",
    WOULD_REFUSE: "ai_report.distribution_would_refuse",
}


@dataclass(frozen=True)
class DistributionDecision:
    allowed: bool
    reason: str
    envelope: ReviewEnvelope
    watermark: Optional[str] = None
    enforced: bool = False

    @property
    def audit_action(self) -> Optional[str]:
        """The access-audit action this decision records, or None for a
        reviewed / non-AI report that needs no trail."""
        return _AUDIT_ACTIONS.get(self.reason)


def gate_enforced() -> bool:
    from app.core.config import settings  # noqa: PLC0415

    return bool(settings.REVIEW_GATE_ENFORCED)


def _as_uuid(value: Any) -> Optional[uuid.UUID]:
    if value is None or value == "":
        return None
    try:
        return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))
    except (TypeError, ValueError):
        return None


async def _project_allows_drafts(db: Any, project_id: Any) -> bool:
    pid = _as_uuid(project_id)
    if pid is None or db is None:
        return False
    allowed = (
        await db.execute(select(Project.allow_unreviewed_distribution).where(Project.id == pid))
    ).scalar_one_or_none()
    return bool(allowed)


async def decide_run_distribution(
    db: Any,
    *,
    run_id: Any,
    project_id: Any,
    channel: str,
    include_unreviewed: bool = False,
    workflow_type: Optional[str] = None,
) -> DistributionDecision:
    """Decide whether ``run_id``'s AI report may go out on ``channel``.

    ``include_unreviewed`` is honoured only for pending reviews and only when the
    caller has already checked the requester may ask for it (QA lead or above).
    """
    envelope = await review_envelope_for_run(db, run_id, workflow_type=workflow_type)
    enforced = gate_enforced()
    if envelope.state == "accepted":
        return DistributionDecision(True, REVIEWED, envelope, enforced=enforced)
    if envelope.state == "not_applicable":
        return DistributionDecision(True, NOT_AI_GENERATED, envelope, enforced=enforced)
    if envelope.state == "pending_review":
        if include_unreviewed:
            return DistributionDecision(True, INCLUDE_UNREVIEWED, envelope, DRAFT_WATERMARK, enforced)
        if await _project_allows_drafts(db, project_id):
            return DistributionDecision(True, PROJECT_ALLOWS_DRAFTS, envelope, DRAFT_WATERMARK, enforced)
    # Pending with no opt-in, or rejected / superseded.
    if not enforced:
        return DistributionDecision(True, WOULD_REFUSE, envelope, None, False)
    return DistributionDecision(False, REFUSED, envelope, None, True)


async def record_distribution(
    db: Any,
    decision: DistributionDecision,
    *,
    channel: str,
    run_id: Any,
    project_id: Any,
    actor: Any = None,
) -> None:
    """Stage the access-audit row a decision calls for. The caller commits."""
    action = decision.audit_action
    if action is None:
        return
    from app.services.access_audit_service import log_access_change  # noqa: PLC0415

    await log_access_change(
        db,
        action=action,
        actor=actor,
        project_id=_as_uuid(project_id),
        after_value={
            "channel": channel,
            "run_id": str(run_id),
            "review_state": decision.envelope.state,
            "review_id": decision.envelope.review_id,
            "reason": decision.reason,
            "watermarked": bool(decision.watermark),
            "enforced": decision.enforced,
        },
    )


def refusal_detail(decision: DistributionDecision) -> dict[str, Any]:
    """The 409 body for a refused distribution: what to do, not just no."""
    review_id = decision.envelope.review_id
    return {
        "code": "report_pending_review",
        "message": (
            "This AI-generated report has not been accepted by a human reviewer, "
            "so it cannot be distributed."
        ),
        "review_state": decision.envelope.state,
        "review_id": review_id,
        "links": {"review": f"/api/v1/reviews/{review_id}"} if review_id else {},
    }


async def apply_release_review_gate(
    db: Any,
    council: Any,
    *,
    run_id: Any,
    allow_advisory: bool = False,
) -> Any:
    """Project the human-review gate onto a release-readiness response.

    Section 8.2: an unreviewed AI decision must change the value clients already
    read -- ``PENDING_REVIEW`` -- rather than add a flag every existing CI
    consumer would ignore and fail open on. ``allow_advisory=true`` returns
    ``ADVISORY_GO`` / ``ADVISORY_NO_GO`` / ``ADVISORY_CONDITIONAL_GO`` for a
    consumer that knowingly wants the draft. The original value is kept in
    ``draft_recommendation``; ``original_recommendation`` belongs to overrides.

    Unchanged: a synthesised quick-look decision (no model wrote it), a decision
    a human overrode (a person already decided), an accepted review, and every
    decision while the gate is not enforced.
    """
    from app.models.schemas import ReviewBlock  # noqa: PLC0415

    synthesized = bool(getattr(council, "synthesized", False))
    envelope = (
        not_ai_generated()
        if synthesized
        else await review_envelope_for_run(db, run_id, workflow_type="deep")
    )
    enforced = gate_enforced()
    update: dict[str, Any] = {
        "requires_human_review": envelope.ai_generated,
        "review": ReviewBlock(**envelope.block()),
        "review_gate_enforced": enforced,
    }
    unreviewed = (
        envelope.ai_generated
        and envelope.state != "accepted"
        and not getattr(council, "human_override", None)
    )
    if unreviewed and enforced:
        recommendation = str(council.recommendation)
        update["draft_recommendation"] = recommendation
        update["recommendation"] = (
            f"ADVISORY_{recommendation}" if allow_advisory else "PENDING_REVIEW"
        )
    return council.model_copy(update=update)
