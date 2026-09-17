"""Review requests: every AI report is a proposal until a human accepts it (E8.1).

What this stores, and when
--------------------------
Finalize calls :func:`stage_run_review_request` for a run that finished
``completed`` having produced a report -- a stage whose capability's output is a
report contract (``agent_capability_registry.is_report_producing``). A run that
produced none settled ``passed`` in E7.5 and gets no request: there is nothing
for a human to accept.

One LIVE request per run
------------------------
The reports a run produced inherit that run's review; a decision report is never
accepted separately from its run (section 8.1). So:

* re-finalizing the same run (a same-id resume, E7.2/E7.3) updates its pending
  request instead of creating a second one;
* if the run's report changed after its review SETTLED -- the evidence bundle
  hash differs -- that review no longer describes the payload, so it is
  superseded and a new pending request replaces it (section 8.3);
* a NEW run over the same test run and workflow type supersedes the older run's
  pending request. Only pending ones: an accepted review of the previous run is
  history, not something to overwrite.

Superseded rows are kept, with ``superseded_by`` pointing at their replacement,
so "who accepted the report this one replaced" stays answerable.

Transaction ownership: stage-only. Finalize owns the commit, and the request is
written in the same transaction as the run's terminal state, so there is never
a ``completed`` report-producing run with no request, or a request for a run
that did not finish. The write is wrapped in a SAVEPOINT so a failure here
cannot abort Finalize's transaction and leave the run ``running``.
"""
from __future__ import annotations

import re
import hashlib
import json
import uuid
from typing import Any, Iterable, Mapping, Optional

import structlog
from sqlalchemy import select

from app.core.metrics import review_requests_total
from app.models.postgres import AgentActionLedger, AgentPipelineRun, ReviewRequest
from app.services.agent_capability_registry import get_capability, is_report_producing
from app.services.eval_label_provenance import checksum_from_execution_metadata

logger = structlog.get_logger("services.review_request")

__all__ = [
    "AI_DISCLAIMER",
    "AI_DISCLAIMER_VERSION",
    "REVIEW_REJECTED_ERROR_PREFIX",
    "ReviewDecisionRefused",
    "create_run_review_request",
    "evidence_hash_from",
    "investigation_evidence_hash",
    "report_stages",
    "settle_review",
    "stage_run_review_request",
]

#: Bumped whenever :data:`AI_DISCLAIMER` changes. Stored on each request so a
#: later audit can say which wording a reviewer was shown.
AI_DISCLAIMER_VERSION = "2026-09-12.v1"
AI_DISCLAIMER = (
    "AI-generated. This report is a draft until a human reviewer accepts it."
)

_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def report_stages(stage_rows: Iterable[Any]) -> list[str]:
    """Names of the stages that completed AND produce a report."""
    return [
        str(row.stage_name)
        for row in stage_rows
        if getattr(row, "status", None) == "completed" and is_report_producing(str(row.stage_name))
    ]


def evidence_hash_from(final_state: Optional[Mapping[str, Any]]) -> Optional[str]:
    """The decision report's ``evidence_bundle_sha256``, when there is a valid one."""
    decision = (final_state or {}).get("decision_intelligence")
    if not isinstance(decision, Mapping):
        return None
    value = decision.get("evidence_bundle_sha256")
    return value if isinstance(value, str) and _SHA256.match(value) else None


def investigation_evidence_hash(verdict: Mapping[str, Any]) -> str:
    """Stable review identity for one persisted Investigator verdict."""
    canonical = json.dumps(
        dict(verdict), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _as_uuid(value: Any) -> Optional[uuid.UUID]:
    if value is None or value == "":
        return None
    try:
        return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))
    except (TypeError, ValueError):
        return None


def _new_request(
    *,
    project_id: uuid.UUID,
    run: Any,
    evidence_bundle_sha256: Optional[str],
    requested_by: Optional[uuid.UUID],
) -> ReviewRequest:
    return ReviewRequest(
        id=uuid.uuid4(),
        project_id=project_id,
        kind="report",
        subject_type="pipeline_run",
        subject_id=str(run.id),
        pipeline_run_id=_as_uuid(run.id),
        test_run_id=_as_uuid(getattr(run, "test_run_id", None)),
        workflow_type=getattr(run, "workflow_type", None),
        state="pending_review",
        requested_by=requested_by,
        created_by="system",
        evidence_bundle_sha256=evidence_bundle_sha256,
        eval_manifest_checksum=checksum_from_execution_metadata(
            getattr(run, "execution_metadata", None)
        ),
        ai_disclaimer_version=AI_DISCLAIMER_VERSION,
    )


async def create_run_review_request(
    db: Any,
    *,
    run: Any,
    project_id: Any,
    report_stage_names: Iterable[str],
    evidence_bundle_sha256: Optional[str] = None,
    requested_by: Any = None,
) -> Optional[ReviewRequest]:
    """Stage the live review request for ``run``. Returns it, or ``None`` when the
    run produced no report or its project is unknown. The caller commits."""
    stages = [s for s in report_stage_names if s]
    if not stages:
        return None
    pid = _as_uuid(project_id)
    if pid is None:
        logger.warning("review_request_skipped_no_project", pipeline_run_id=str(run.id))
        return None
    requester = _as_uuid(requested_by)
    subject_id = str(run.id)
    manifest_checksum = checksum_from_execution_metadata(
        getattr(run, "execution_metadata", None)
    )

    # Typed explicitly: scalar_one_or_none() is Any, and returning it would
    # widen this function's declared ReviewRequest return (mypy no-any-return).
    live: Optional[ReviewRequest] = (
        await db.execute(
            select(ReviewRequest)
            .where(
                ReviewRequest.kind == "report",
                ReviewRequest.subject_type == "pipeline_run",
                ReviewRequest.subject_id == subject_id,
                ReviewRequest.state != "superseded",
            )
            .with_for_update()
        )
    ).scalar_one_or_none()

    if live is not None:
        if live.evidence_bundle_sha256 == evidence_bundle_sha256:
            # The same review still describes the exact same evidence. A
            # pending request keeps its identity so idempotent finalization is
            # harmless; a settled request remains immutable history.
            if getattr(live, "eval_manifest_checksum", None) is None:
                live.eval_manifest_checksum = manifest_checksum
                await db.flush()
            return live
        # Evidence changed after the review was shown or settled. Keep the old
        # hash bound to its old id and mint a new pending subject so a stale tab
        # cannot accept bytes it never reviewed. Supersede first so the
        # one-live-per-subject index holds.
        live.state = "superseded"
        await db.flush()
        review_requests_total.labels(state="superseded").inc()

    request = _new_request(
        project_id=pid,
        run=run,
        evidence_bundle_sha256=evidence_bundle_sha256,
        requested_by=requester,
    )
    db.add(request)
    await db.flush()
    review_requests_total.labels(state="pending_review").inc()
    if live is not None:
        live.superseded_by = request.id

    # A newer run over the same test run and workflow replaces older pending reviews.
    test_run_id = _as_uuid(getattr(run, "test_run_id", None))
    workflow_type = getattr(run, "workflow_type", None)
    older: list[ReviewRequest] = []
    if test_run_id is not None and workflow_type and workflow_type != "investigation":
        older = (
            await db.execute(
                select(ReviewRequest)
                .where(
                    ReviewRequest.project_id == pid,
                    ReviewRequest.test_run_id == test_run_id,
                    ReviewRequest.workflow_type == workflow_type,
                    ReviewRequest.state == "pending_review",
                    ReviewRequest.subject_id != subject_id,
                )
                .with_for_update()
            )
        ).scalars().all()
        for row in older:
            row.state = "superseded"
            row.superseded_by = request.id
        if older:
            logger.info(
                "review_requests_superseded",
                pipeline_run_id=subject_id,
                superseded=len(older),
            )
    await db.flush()
    if older:
        review_requests_total.labels(state="superseded").inc(len(older))
    logger.info(
        "review_request_created",
        pipeline_run_id=subject_id,
        report_stages=stages,
        replaced_settled_review=live is not None,
    )
    return request


async def stage_run_review_request(db: Any, **kwargs: Any) -> Optional[ReviewRequest]:
    """:func:`create_run_review_request` inside a SAVEPOINT when the session has one.

    Finalize must commit the run's terminal state even if this fails. Without a
    savepoint, a failed flush (a constraint violation, say) would leave the
    session's transaction aborted and the whole Finalize commit would fail with
    it, stranding the run as ``running``. Test doubles without ``begin_nested``
    are called directly.
    """
    begin_nested = getattr(db, "begin_nested", None)
    if begin_nested is None:
        return await create_run_review_request(db, **kwargs)
    async with begin_nested():
        return await create_run_review_request(db, **kwargs)


# ── Settling a review (architecture E8.2, section 8.3) ───────────────────────


class ReviewDecisionRefused(Exception):
    """A review decision the gate refuses. ``status_code`` is the HTTP status the
    router returns; ``code`` is stable for clients to branch on."""

    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


#: Prefix of ``agent_pipeline_runs.error`` when a human rejects the run's report
#: (section 7.2: ``completed -> failed`` with ``error.code = review_rejected``).
REVIEW_REJECTED_ERROR_PREFIX = "review_rejected"

_NOTES_MAX = 4000


async def settle_review(
    db: Any,
    *,
    review: ReviewRequest,
    reviewer: Any,
    decision: str,
    reason_code: Optional[str] = None,
    notes: Optional[str] = None,
) -> ReviewRequest:
    """Accept or reject a pending review, moving its run through the state machine.

    Refusals, in the order they are checked -- the cheapest proof that the
    caller may not decide comes first, and nothing is written before all pass:

    1. **Interactive login only.** An API key is refused whatever its owner's
       role: the MCP server and CI both hold keys, and an agent acting through a
       QA lead's key could otherwise approve its own output (section 8.3).
    2. **No synthetic accounts.** Nobody logs in as them (``users.is_synthetic``).
    3. **A rejection needs a reason code** from the closed vocabulary; it is the
       only part of a rejection that becomes an eval label.
    4. **Only a pending review can be settled.** An accepted, rejected or
       superseded one is history.
    5. **Separation of duties.** The person who requested the run cannot review
       it. Enforced whenever the requester is recorded.
    6. **The run must still be awaiting review.** One guarded UPDATE moves it
       ``completed -> passed`` (accept) or ``completed -> failed`` (reject); if
       the run moved first -- re-run, resumed -- the decision is refused rather
       than recorded against a run it no longer describes.

    ``notes`` is redacted before it is stored and is never exported. The caller
    owns the commit.
    """
    from datetime import datetime, timezone  # noqa: PLC0415

    from app.core.deps import CREDENTIAL_KIND_JWT, credential_kind  # noqa: PLC0415
    from app.models.postgres import REVIEW_REASON_CODES  # noqa: PLC0415
    from app.services.privacy_service import sanitize_for_llm  # noqa: PLC0415
    from app.services.workflow_run_state import (  # noqa: PLC0415
        PipelineRunStatus,
        TransitionLost,
        guarded_transition,
    )

    if decision not in ("accepted", "rejected"):
        raise ValueError(f"unknown review decision: {decision!r}")
    if credential_kind(reviewer) != CREDENTIAL_KIND_JWT:
        raise ReviewDecisionRefused(
            403, "interactive_login_required",
            "Review decisions require an interactive login; API keys are refused.",
        )
    if bool(getattr(reviewer, "is_synthetic", False)):
        raise ReviewDecisionRefused(
            403, "synthetic_account", "Synthetic accounts cannot review AI reports.",
        )
    if decision == "rejected" and reason_code not in REVIEW_REASON_CODES:
        raise ReviewDecisionRefused(
            422, "reason_code_required",
            "A rejection needs a reason_code: " + ", ".join(REVIEW_REASON_CODES) + ".",
        )
    if review.state != "pending_review":
        raise ReviewDecisionRefused(
            409, "review_not_pending", f"This review is already {review.state}.",
        )
    same_user = (
        review.requested_by is not None
        and review.requested_by == getattr(reviewer, "id", None)
    )
    if same_user and await _run_proposes_act_actions(db, review):
        raise ReviewDecisionRefused(
            403, "separation_of_duties",
            "The person who requested this run cannot review its act-mode proposals.",
        )

    if review.pipeline_run_id is not None:
        target = PipelineRunStatus.PASSED if decision == "accepted" else PipelineRunStatus.FAILED
        run_error = None if decision == "accepted" else f"{REVIEW_REJECTED_ERROR_PREFIX}: {reason_code}"
        try:
            await guarded_transition(
                db,
                review.pipeline_run_id,
                expected=PipelineRunStatus.COMPLETED,
                to=target,
                error=run_error,
            )
        except TransitionLost as exc:
            raise ReviewDecisionRefused(
                409, "run_not_awaiting_review",
                "The run this review describes is no longer awaiting review.",
            ) from exc

    cleaned = (notes or "").strip()
    review.state = decision
    review.reviewed_by = getattr(reviewer, "id", None)
    review.reviewed_at = datetime.now(timezone.utc)
    review.reason_code = reason_code if decision == "rejected" else None
    review.notes = sanitize_for_llm(cleaned)[:_NOTES_MAX] if cleaned else None
    await db.flush()
    review_requests_total.labels(state=decision).inc()
    logger.info(
        "review_settled",
        review_id=str(review.id),
        decision=decision,
        reason_code=review.reason_code,
    )
    return review


async def _run_proposes_act_actions(db: Any, review: ReviewRequest) -> bool:
    """Whether this review covers an action proposed by an act-mode agent.

    The action ledger is the durable record that a report proposed a mutation.
    Its hashed payload carries the proposing agent id (T4). The proposing mode
    comes from the pipeline's frozen execution metadata, never the mutable live
    project config. An unidentifiable legacy proposal fails closed for
    self-review.
    """
    if review.pipeline_run_id is None:
        return False
    result = await db.execute(
        select(AgentActionLedger.request_payload).where(
            AgentActionLedger.pipeline_run_id == review.pipeline_run_id
        )
    )
    payloads = result.scalars().all()
    if not payloads:
        return False

    pipeline = await db.get(AgentPipelineRun, review.pipeline_run_id)
    metadata = (
        dict(pipeline.execution_metadata)
        if pipeline is not None and isinstance(pipeline.execution_metadata, dict)
        else {}
    )
    workflow_configs = metadata.get("workflow_agent_configs")
    invocation_configs = metadata.get("resolved_agent_configs")

    for payload in payloads:
        agent_id = (
            payload.get("proposing_agent_id")
            if isinstance(payload, dict)
            else None
        )
        if not isinstance(agent_id, str) or not agent_id:
            return True
        try:
            capability_id = get_capability(agent_id).capability_id
        except ValueError:
            return True

        # Invocation authority was accepted at the API boundary, before a
        # worker could observe a later project config. Prefer that durable
        # snapshot when present. Ordinary workflows have no invocation entry
        # and use their proposal-time workflow snapshot instead. Both maps are
        # keyed by canonical capability id, while the action ledger stores the
        # stage name.
        frozen = None
        if isinstance(invocation_configs, dict):
            snapshot = invocation_configs.get(capability_id)
            frozen = snapshot.get("config") if isinstance(snapshot, dict) else None
        if not isinstance(frozen, dict) and isinstance(workflow_configs, dict):
            frozen = workflow_configs.get(capability_id)
        if not isinstance(frozen, dict):
            return True
        mode = frozen.get("mode")
        if mode == "act":
            return True
        if mode not in {"shadow", "suggest"}:
            return True
    return False
