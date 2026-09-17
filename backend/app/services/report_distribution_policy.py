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

import structlog
from sqlalchemy import select

from app.models.postgres import Project
from app.services.review_envelope import (
    ReviewEnvelope,
    not_ai_generated,
    review_envelope_for_run,
    review_envelope_for_pipeline_subject,
)

__all__ = [
    "DRAFT_WATERMARK",
    "KIND_LABELS_DRAFT_NOTE",
    "REVIEW_PENDING_NOTICE",
    "DistributionDecision",
    "apply_release_review_gate",
    "decide_run_distribution",
    "gate_ai_summary_text",
    "gate_enforced",
    "gate_kind_labels",
    "gate_investigation_excerpt",
    "gate_release_decided_delivery",
    "gate_release_decided_payload",
    "gate_release_verdict",
    "record_distribution",
    "record_distribution_detached",
    "refusal_detail",
    "release_review_projection",
]

logger = structlog.get_logger("services.report_distribution_policy")

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

_TERMINAL_REVIEW_STATES = frozenset({"rejected", "superseded"})


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
    return await _decide_envelope_distribution(
        db,
        envelope=envelope,
        project_id=project_id,
        include_unreviewed=include_unreviewed,
    )


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
    pipeline_run_id: Any = None,
    project_id: Any = None,
    actor: Any = None,
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

    envelope, enforced, withhold = await release_review_projection(
        db,
        run_id=run_id,
        synthesized=bool(getattr(council, "synthesized", False)),
        human_override=getattr(council, "human_override", None),
        pipeline_run_id=pipeline_run_id,
    )
    update: dict[str, Any] = {
        "requires_human_review": envelope.ai_generated,
        "review": ReviewBlock(**envelope.block()),
        "review_gate_enforced": enforced,
    }
    if withhold:
        recommendation = str(council.recommendation)
        if envelope.state in _TERMINAL_REVIEW_STATES:
            # Rejected and superseded content is no longer a draft. Preserve
            # only the terminal review envelope; no model narrative may leave.
            update.update(
                recommendation="PENDING_REVIEW",
                draft_recommendation=None,
                blocking_issues=[],
                conditions_for_go=[],
                reasoning=None,
                original_recommendation=None,
            )
        else:
            update["draft_recommendation"] = recommendation
            update["recommendation"] = (
                f"ADVISORY_{recommendation}" if allow_advisory else "PENDING_REVIEW"
            )
            if allow_advisory and actor is not None:
                advisory = DistributionDecision(
                    True,
                    INCLUDE_UNREVIEWED,
                    envelope,
                    watermark="ADVISORY",
                    enforced=enforced,
                )
                await record_distribution(
                    db,
                    advisory,
                    channel="release_readiness_advisory",
                    run_id=run_id,
                    project_id=project_id,
                    actor=actor,
                )
    return council.model_copy(update=update)


async def release_review_projection(
    db: Any,
    *,
    run_id: Any,
    synthesized: bool,
    human_override: Any,
    pipeline_run_id: Any = None,
) -> tuple[ReviewEnvelope, bool, bool]:
    """The one rule for a release value, shared by the release-readiness response
    and the ``release.decided`` webhook so the two can never disagree.

    Returns ``(envelope, enforced, withhold)``. ``withhold`` is true when the value
    must not leave as-is: an AI decision no human accepted and no human overrode,
    while the gate is enforced.
    """
    envelope = (
        not_ai_generated()
        if synthesized
        else (
            await review_envelope_for_pipeline_subject(db, pipeline_run_id)
            if pipeline_run_id is not None
            else await review_envelope_for_run(db, run_id, workflow_type="deep")
        )
    )
    enforced = gate_enforced()
    unreviewed = (
        envelope.ai_generated
        and envelope.state != "accepted"
        and not human_override
    )
    return envelope, enforced, bool(unreviewed and enforced)


async def gate_release_decided_payload(
    db: Any,
    payload: dict[str, Any],
    *,
    run_id: Any,
    synthesized: bool,
    human_override: Any,
    pipeline_run_id: Any = None,
    project_id: Any = None,
) -> dict[str, Any]:
    """Project the review gate onto a ``release.decided`` webhook payload.

    A pending value is sent as a watermarked draft only when the project opted
    in. Otherwise enforcement replaces it with ``PENDING_REVIEW``. The payload
    always gains ``review`` ``{state, review_id, reviewed_at}``, which says that
    a human reviewed it and when, never who.
    """
    projected, _decision = await gate_release_decided_delivery(
        db,
        payload,
        run_id=run_id,
        synthesized=synthesized,
        human_override=human_override,
        pipeline_run_id=pipeline_run_id,
        project_id=project_id,
    )
    return projected


async def gate_release_decided_delivery(
    db: Any,
    payload: dict[str, Any],
    *,
    run_id: Any,
    synthesized: bool,
    human_override: Any,
    pipeline_run_id: Any = None,
    project_id: Any = None,
) -> tuple[dict[str, Any], DistributionDecision]:
    """Gate one webhook attempt and return its auditable decision."""
    envelope, enforced, _withhold = await release_review_projection(
        db,
        run_id=run_id,
        synthesized=synthesized,
        human_override=human_override,
        pipeline_run_id=pipeline_run_id,
    )
    decision = (
        DistributionDecision(True, REVIEWED, envelope, enforced=enforced)
        if human_override
        else await _decide_envelope_distribution(
            db,
            envelope=envelope,
            project_id=project_id,
        )
    )
    projected = dict(payload)
    projected.pop("draft_watermark", None)
    projected["requires_human_review"] = envelope.ai_generated
    projected["review"] = {
        "state": envelope.state,
        "review_id": envelope.review_id,
        "reviewed_at": envelope.reviewed_at,
    }
    projected["review_gate_enforced"] = enforced
    projected["draft_recommendation"] = None
    if decision.watermark:
        projected["draft_watermark"] = decision.watermark
    elif not decision.allowed:
        if envelope.state in _TERMINAL_REVIEW_STATES:
            projected["blocking_issues"] = []
            projected["conditions_for_go"] = []
            projected["reasoning"] = None
            projected["original_recommendation"] = None
        else:
            projected["draft_recommendation"] = projected.get("recommendation")
        projected["recommendation"] = "PENDING_REVIEW"
    return projected, decision


# ── AI summary text in notifications and digests (E8.4 slice 2) ─────────────

#: What a notification says instead of an AI summary it may not distribute.
#: The event still reaches people -- only the unreviewed AI prose is withheld.
REVIEW_PENDING_NOTICE = (
    "An AI summary for this run is ready and awaiting human review. "
    "Open it in TestLookup to read and review it."
)

INVESTIGATION_REVIEW_PENDING_NOTICE = (
    "The Investigator narrative is awaiting human review. "
    "Open the investigation in TestLookup to read and review it."
)


async def _decide_envelope_distribution(
    db: Any,
    *,
    envelope: ReviewEnvelope,
    project_id: Any,
    include_unreviewed: bool = False,
) -> DistributionDecision:
    """Apply the common distribution rule to an already-resolved subject."""
    enforced = gate_enforced()
    if envelope.state == "accepted":
        return DistributionDecision(True, REVIEWED, envelope, enforced=enforced)
    if envelope.state == "not_applicable":
        return DistributionDecision(True, NOT_AI_GENERATED, envelope, enforced=enforced)
    if envelope.state == "pending_review":
        if include_unreviewed:
            return DistributionDecision(
                True, INCLUDE_UNREVIEWED, envelope, DRAFT_WATERMARK, enforced
            )
        if await _project_allows_drafts(db, project_id):
            return DistributionDecision(
                True, PROJECT_ALLOWS_DRAFTS, envelope, DRAFT_WATERMARK, enforced
            )
    if not enforced:
        return DistributionDecision(True, WOULD_REFUSE, envelope, None, False)
    return DistributionDecision(False, REFUSED, envelope, None, True)


async def gate_investigation_excerpt(
    db: Any,
    *,
    investigation_id: Any,
    project_id: Any,
    excerpt: str,
    channel: str,
    evidence_bundle_sha256: str | None = None,
) -> tuple[str, Optional[DistributionDecision]]:
    """Gate one stored Investigator narrative excerpt by its own review.

    The stable Investigator pipeline id is derived from the investigation id,
    matching the workflow runner. Withholding the excerpt never drops the
    deterministic cause, confidence, tally, or cockpit link around it.
    """
    if not excerpt:
        return excerpt, None
    try:
        subject_id = uuid.UUID(str(investigation_id))
    except (TypeError, ValueError):
        subject_id = uuid.UUID(int=0)
    pipeline_id = uuid.uuid5(
        uuid.NAMESPACE_URL, f"testlookup:investigation:{subject_id}"
    )
    envelope = await review_envelope_for_pipeline_subject(
        db,
        pipeline_id,
        evidence_bundle_sha256=evidence_bundle_sha256,
    )
    decision = await _decide_envelope_distribution(
        db, envelope=envelope, project_id=project_id
    )
    if not decision.allowed:
        return INVESTIGATION_REVIEW_PENDING_NOTICE, decision
    if decision.watermark:
        return f"{decision.watermark}\n\n{excerpt}", decision
    return excerpt, decision


async def gate_ai_summary_text(
    db: Any,
    *,
    run_id: Any,
    project_id: Any,
    summary_text: str,
    ai_generated: bool,
    channel: str,
) -> tuple[str, Optional[DistributionDecision]]:
    """Return the summary text a notification may carry, and the decision.

    * not AI-generated (the deterministic fallback), or empty -- unchanged, no
      decision: there is nothing to review;
    * refused (enforced, unreviewed) -- :data:`REVIEW_PENDING_NOTICE`, never the
      AI text;
    * a draft under the project's opt-in -- the text, led by the DRAFT line, as
      section 8.2 requires of a notification body;
    * reviewed, or not enforced -- unchanged.
    """
    if not ai_generated or not summary_text:
        return summary_text, None
    decision = await decide_run_distribution(
        db, run_id=run_id, project_id=project_id, channel=channel
    )
    if not decision.allowed:
        return REVIEW_PENDING_NOTICE, decision
    if decision.watermark:
        return f"{decision.watermark}\n\n{summary_text}", decision
    return summary_text, decision


async def gate_release_verdict(
    db: Any,
    decision: dict[str, Any],
    *,
    test_run_id: Any,
    human_override: Any = None,
) -> dict[str, Any]:
    """Project the review gate onto a release verdict embedded in a report.

    The window analysis report (downloaded, and attached to digests) quotes the
    latest release verdict. It gets the same treatment as the release-readiness
    endpoint: while enforced, an unreviewed AI verdict reads ``PENDING_REVIEW``
    with the model's value in ``draft_recommendation``; a project that allows
    drafts sees the value with ``draft_watermark``. A human override is a
    decision a person already made, and is left alone.
    """
    envelope = await review_envelope_for_run(db, test_run_id, workflow_type="deep")
    projected = dict(decision)
    projected["review_state"] = envelope.state
    if human_override or envelope.state in ("accepted", "not_applicable"):
        return projected
    if envelope.state == "pending_review" and await _project_allows_drafts(
        db, projected.get("project_id")
    ):
        projected["draft_watermark"] = DRAFT_WATERMARK
        return projected
    if gate_enforced():
        if envelope.state in _TERMINAL_REVIEW_STATES:
            projected["draft_recommendation"] = None
            projected["blocking_issues"] = []
            projected["conditions_for_go"] = []
            projected["reasoning"] = None
            projected["original_recommendation"] = None
        else:
            projected["draft_recommendation"] = projected.get("recommendation")
        projected["recommendation"] = "PENDING_REVIEW"
    return projected


# ── AI kind labels in PR / MR comments (E8.4 slice 3) ────────────────────────

#: Leads a PR/MR comment whose failure-kind labels go out as an unreviewed draft.
KIND_LABELS_DRAFT_NOTE = (
    "_DRAFT: the failure-kind labels below are AI-generated and not yet "
    "human-reviewed._"
)


async def gate_kind_labels(
    db: Any,
    *,
    run_id: Any,
    project_id: Any,
    kind_labels: dict[str, str],
    channel: str,
) -> tuple[dict[str, str], Optional[str], Optional[DistributionDecision]]:
    """Return the kind labels a PR/MR comment may carry, its draft note, and
    the decision.

    The labels are AI classifications of each failing test. They are
    decoration -- a comment without them still says what failed -- so the gate
    strips them rather than holding back the comment:

    * refused (enforced, unreviewed) -- no labels;
    * a draft under the project opt-in -- the labels, with
      :data:`KIND_LABELS_DRAFT_NOTE`;
    * reviewed, or not enforced -- unchanged;
    * the gate itself fails -- no labels while enforced (fail closed),
      unchanged otherwise. It never stops the comment from posting.
    """
    if not kind_labels:
        return kind_labels, None, None
    try:
        decision = await decide_run_distribution(
            db, run_id=run_id, project_id=project_id, channel=channel
        )
    except Exception as exc:  # noqa: BLE001 -- labels must never block the comment
        logger.warning("kind_label_gate_unavailable", channel=channel, error_type=type(exc).__name__)
        return ({} if gate_enforced() else kind_labels), None, None
    if not decision.allowed:
        return {}, None, decision
    if decision.watermark:
        return kind_labels, KIND_LABELS_DRAFT_NOTE, decision
    return kind_labels, None, decision


async def record_distribution_detached(
    decision: Optional[DistributionDecision],
    *,
    channel: str,
    run_id: Any,
    project_id: Any,
) -> None:
    """Commit a decision's audit row in its own session. Never raises.

    For worker paths whose session only reads (the PR/MR comment context
    builders) and must not grow a commit of their own. The audit row is the
    record that an unreviewed report left the system -- or would have -- so it
    is committed independently of whether the external post later succeeds.
    """
    if decision is None or decision.audit_action is None:
        return
    try:
        from app.db.postgres import AsyncSessionLocal  # noqa: PLC0415

        async with AsyncSessionLocal() as db:
            await record_distribution(
                db, decision, channel=channel, run_id=run_id, project_id=project_id
            )
            await db.commit()
    except Exception as exc:  # noqa: BLE001 -- an audit write must not block delivery
        logger.warning("distribution_audit_not_recorded", channel=channel, error_type=type(exc).__name__)
