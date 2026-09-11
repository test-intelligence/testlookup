"""
AI feedback endpoints — capture human signals for continuous fine-tuning.

POST /api/v1/feedback/{analysis_id}      — rate an AI analysis result
PUT  /api/v1/feedback/{analysis_id}      — update a previously submitted rating
GET  /api/v1/feedback/stats              — feedback summary for dashboard
POST /api/v1/training/export             — manually trigger training data export
POST /api/v1/training/promote            — manually promote a fine-tuned model
GET  /api/v1/training/status             — model registry + pending example counts

GET  /api/v1/projects/{project_id}/analyses/lookup — latest analysis_id for a
     test fingerprint (US-2.4; lives on ``lookup_router`` so the
     ``require_project_access`` guard applies to the {project_id} scope)
POST /api/v1/projects/{project_id}/fix-outcomes — record a merged/reverted fix
     outcome for a fingerprint as an AI-F1 ``human_indirect`` training signal
     (Agentic plan AI-5; the MCP ``record_fix_outcome`` tool's endpoint)
"""
import hashlib
import hmac
import json
import time
import uuid
from datetime import datetime
from typing import Literal, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_active_user, require_project_access, require_role, require_run_access
from app.core.config import _is_placeholder_secret, settings
from app.db.postgres import get_db
from app.db.mongo import get_mongo_db
from app.models.postgres import (
    FeedbackRating,
    FailureCategory,
    User,
    UserRole
)
from app.services import feedback_service

router = APIRouter(prefix="/api/v1", tags=["Feedback & Training"])
# US-2.4 — fingerprint → analysis_id bridge for the Failure Analysis page's
# "correct classification" dialog. Separate router because the project-scoped
# path must carry the ``require_project_access`` guard (authorization ratchet).
lookup_router = APIRouter(prefix="/api/v1/projects", tags=["Feedback & Training"])
# Jira's resolution webhook. A PUBLIC router (bootstrap.PUBLIC_ROUTERS): Jira
# sends no user session and cannot send an auth header, so the credential is
# the HMAC signature it computes with the webhook's secret (see
# ``_verify_jira_signature``).
jira_webhook_router = APIRouter(prefix="/api/v1", tags=["Feedback & Training"])

#: The header Jira Cloud signs a webhook delivery with, when the webhook has a
#: secret: ``sha256=<hex HMAC-SHA256 of the raw request body>``.
JIRA_SIGNATURE_HEADER = "X-Hub-Signature"

#: What a delivery is told while no secret is configured.
JIRA_SECRET_UNSET_DETAIL = (
    "The Jira webhook is disabled: JIRA_WEBHOOK_SECRET is not configured"
)


def _verify_jira_signature(raw_body: bytes, signature: Optional[str]) -> None:
    """Refuse a delivery that Jira did not sign with the configured secret.

    403 while the secret is unset or a shipped placeholder, in every
    environment (an HMAC with an empty or published key proves nothing); 401
    for a missing or wrong signature. The comparison is constant time: ``==``
    stops at the first differing character and leaks a prefix to a caller who
    can time it.
    """
    secret = settings.JIRA_WEBHOOK_SECRET or ""
    if _is_placeholder_secret(secret):
        raise HTTPException(status_code=403, detail=JIRA_SECRET_UNSET_DETAIL)
    expected = "sha256=" + hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    try:
        # Starlette decodes a header as latin-1, so a hostile byte arrives as
        # a non-ASCII str, which compare_digest refuses with a TypeError (a
        # public 500). A signature is ASCII hex: anything else is simply not
        # a valid one (QA-B45-1).
        presented = (signature or "").strip().encode("ascii")
    except UnicodeEncodeError:
        presented = b""
    if not presented or not hmac.compare_digest(expected.encode("ascii"), presented):
        raise HTTPException(status_code=401, detail="Invalid or missing Jira webhook signature")


#: How long a delivery is remembered, and so the oldest delivery accepted.
JIRA_REPLAY_WINDOW_SECONDS = 7 * 24 * 3600
_JIRA_DELIVERY_KEY = "jira:webhook:delivery:"


def _delivery_is_too_old(payload: dict) -> bool:
    """Jira puts the event time in the body (``timestamp``, epoch ms), inside
    the signature. A delivery older than the replay window can no longer be
    matched against the dedupe record, so it is not applied."""
    stamp = payload.get("timestamp")
    if isinstance(stamp, bool) or not isinstance(stamp, (int, float)):
        return False
    return (time.time() - stamp / 1000.0) > JIRA_REPLAY_WINDOW_SECONDS


async def _claim_delivery(raw_body: bytes) -> tuple[bool, str]:
    """Record a signed delivery once (QA-B45-2). Returns ``(first_time, key)``.

    Jira's HMAC carries no timestamp or nonce, so a captured delivery stays
    validly signed forever. The identifier is the sha256 of the raw body: it
    is covered by the signature (``X-Atlassian-Webhook-Identifier`` is not,
    so an attacker could vary it) and it is stable across Jira's own retries
    of one delivery. Jira's bodies carry the event ``timestamp``, so two real
    events do not share a body. SET NX with a TTL of the replay window.

    Redis unavailable: FAIL CLOSED with 503. A resolution event is not urgent,
    and Jira retries a failed delivery, so it is applied once Redis is back;
    applying it without the record would reopen the replay.
    """
    from app.db import redis_client

    key = _JIRA_DELIVERY_KEY + hashlib.sha256(raw_body).hexdigest()
    try:
        first = await redis_client.get_redis().set(key, "1", nx=True, ex=JIRA_REPLAY_WINDOW_SECONDS)
    except Exception as exc:  # noqa: BLE001 -- any Redis failure refuses the delivery
        raise HTTPException(
            status_code=503, detail="The Jira webhook cannot record deliveries right now; retry later"
        ) from exc
    return bool(first), key


async def _release_delivery(key: str) -> None:
    """The delivery was not applied: let Jira's retry apply it."""
    from app.db import redis_client

    try:
        await redis_client.get_redis().delete(key)
    except Exception:  # noqa: BLE001 -- the TTL still bounds it
        pass


# ── Schemas ───────────────────────────────────────────────────────────────────

class FeedbackRequest(BaseModel):
    rating: FeedbackRating
    corrected_category: Optional[FailureCategory] = None
    corrected_root_cause: Optional[str] = None
    comment: Optional[str] = None



class DecisionReportFeedbackRequest(BaseModel):
    """Utility rating or claim correction for one immutable report version."""
    report_version: int = Field(..., ge=1)
    feedback_kind: Literal["utility", "claim_correction"]
    utility_rating: Optional[Literal["useful", "partially_useful", "not_useful"]] = None
    claim_id: Optional[str] = Field(default=None, min_length=1, max_length=128)
    correction_type: Optional[Literal["category", "cause", "flaky", "release"]] = None
    corrected_value: Optional[object] = None
    reason: Optional[str] = Field(default=None, max_length=4000)
    evidence_ids: list[str] = Field(default_factory=list, max_length=5)
    idempotency_key: Optional[str] = Field(default=None, min_length=1, max_length=128)

    @field_validator("evidence_ids")
    @classmethod
    def validate_evidence_ids(cls, values):
        if any(len(value) > 128 for value in values):
            raise ValueError("evidence_ids entries must be <= 128 characters")
        return values

    @model_validator(mode="after")
    def validate_feedback_shape(self):
        if self.feedback_kind == "utility":
            if self.utility_rating is None:
                raise ValueError("utility_rating is required for utility feedback")
            if any((self.claim_id, self.correction_type, self.corrected_value is not None, self.reason, self.evidence_ids)):
                raise ValueError("utility feedback cannot include claim correction fields")
        elif self.claim_id is None or self.correction_type is None:
            raise ValueError("claim_id and correction_type are required for a claim correction")
        elif not self.reason or not self.reason.strip() or not self.evidence_ids or self.corrected_value is None:
            raise ValueError("claim corrections require a value, reason, and evidence_ids")
        return self


class PromoteModelRequest(BaseModel):
    track: str           # "classifier" | "reasoning" | "embedding"
    model_name: str
    eval_accuracy: Optional[float] = None
    baseline_accuracy: Optional[float] = None


class FixOutcomeRequest(BaseModel):
    """AI-5: outcome of a fix informed by TestLookup's diagnosis.

    ``outcome`` is a closed vocabulary validated here (the values map onto
    FeedbackRating in the service); ``fingerprint`` is the stable test
    identity (sha256(class::test)[:16]) the analytics surface uses.
    """
    fingerprint: str = Field(..., min_length=1, max_length=64)
    outcome: Literal["fixed", "not_fixed", "reverted"]
    reference: Optional[str] = Field(default=None, max_length=500)
    comment: Optional[str] = Field(default=None, max_length=4000)


class AnalysisLookupResponse(BaseModel):
    """US-2.4: latest AI analysis for a (project, fingerprint) pair.

    All fields are ``None`` when the test has never been analysed — the UI
    renders a "no AI analysis recorded yet" empty state instead of a 404
    (which axios would surface as a scary error toast).

    ``failure_category`` is a plain string, NOT the ``FailureCategory``
    enum: the backing column is ``String(30)`` and a strict enum here would
    silently 422 the response if a stored value ever drifts out of vocab
    (backend/CLAUDE.md pitfall).
    """
    analysis_id: Optional[uuid.UUID] = None
    failure_category: Optional[str] = None
    analyzed_at: Optional[datetime] = None


# ── Feedback endpoints ────────────────────────────────────────────────────────

# On its own router, registered with the PUBLIC routers BEFORE the protected
# ones: FastAPI matches in registration order, and a literal segment placed
# after the same-shape ``/feedback/{analysis_id}`` is unreachable ("jira-webhook"
# parsed as a UUID: a 422 for every delivery, once). Guarded by
# tests/test_architectural_route_shadowing.py.
#
# No signed-in account (re-audit follow-up to N32). The route took any session
# or API key and let its holder close any project's defects and write AI
# training feedback with a forged payload, while a real Jira delivery, which
# carries no session, could only get in with a user's key pasted into Jira.
# The signature is the credential Jira actually has.
@jira_webhook_router.post("/feedback/jira-webhook", status_code=200)
async def jira_resolution_webhook(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    raw_body = await request.body()
    _verify_jira_signature(raw_body, request.headers.get(JIRA_SIGNATURE_HEADER))
    try:
        payload = json.loads(raw_body)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="The body is not JSON") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=422, detail="The body must be a JSON object")
    # A replayed delivery is acknowledged with 200 and changes nothing. Not a
    # 409: Jira retries any non-2xx, so a genuine retry of a delivery that was
    # already applied would be re-sent for no reason.
    if _delivery_is_too_old(payload):
        return {"message": "ignored: delivery is older than the replay window", "applied": False}
    first_time, key = await _claim_delivery(raw_body)
    if not first_time:
        return {"message": "ignored: this delivery was already received", "applied": False}
    try:
        result = await feedback_service.jira_resolution_webhook(db, payload)
        await db.commit()
    except BaseException:
        await _release_delivery(key)
        raise
    return result


@router.post("/feedback/{analysis_id}", status_code=201)
async def submit_feedback(
    analysis_id: uuid.UUID,
    body: FeedbackRequest,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_active_user),
):
    result = await feedback_service.submit_feedback(db, analysis_id, body, current_user)
    await db.commit()
    return result



@router.post("/runs/{run_id}/decision-reports/{report_id}/feedback", status_code=201)
async def submit_decision_report_feedback(
    run_id: uuid.UUID,
    report_id: str,
    body: DecisionReportFeedbackRequest,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_run_access()),
):
    result = await feedback_service.submit_decision_report_feedback(
        db,
        get_mongo_db(),
        run_id=run_id,
        report_id=report_id,
        body=body,
        current_user=current_user,
    )
    await db.commit()
    return result
@router.put("/feedback/{analysis_id}", status_code=200)
async def update_feedback(
    analysis_id: uuid.UUID,
    body: FeedbackRequest,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_active_user),
):
    result = await feedback_service.update_feedback(db, analysis_id, body, current_user)
    await db.commit()
    return result


@lookup_router.get(
    "/{project_id}/analyses/lookup",
    response_model=AnalysisLookupResponse,
)
async def lookup_latest_analysis(
    project_id: uuid.UUID,
    fingerprint: str = Query(..., min_length=1, max_length=64),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_project_access()),
):
    """Latest AI analysis for a test fingerprint, project-scoped (US-2.4).

    Bridges the Failure Analysis page (which identifies tests by
    ``test_fingerprint``) to the feedback endpoints (which key on
    ``analysis_id``). Returns 200 with null fields when no analysis exists.
    """
    found = await feedback_service.latest_analysis_for_fingerprint(
        db, project_id, fingerprint,
    )
    if found is None:
        return AnalysisLookupResponse()
    return AnalysisLookupResponse(**found)


@lookup_router.post("/{project_id}/fix-outcomes", status_code=201)
async def record_fix_outcome(
    project_id: uuid.UUID,
    body: FixOutcomeRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_project_access()),
):
    """Record a fix outcome for a fingerprint (Agentic plan AI-5).

    Writes an ``ai_feedback`` row with ``source="fix_outcome"`` against the
    latest analysis for the (project, fingerprint) pair — the AI-F1 label
    system maps that source into the ``human_indirect`` provenance bucket.
    404 when the fingerprint has never been analysed (nothing to grade).
    Project-scoped via ``require_project_access`` (authorization ratchet);
    any active project member may record an outcome, matching the feedback
    endpoints above.
    """
    result = await feedback_service.record_fix_outcome(
        db,
        project_id,
        fingerprint=body.fingerprint,
        outcome=body.outcome,
        reference=body.reference,
        comment=body.comment,
        current_user=current_user,
    )
    await db.commit()
    return result


@router.get("/feedback/stats")
async def get_feedback_stats(
    db: AsyncSession = Depends(get_db),
    _=Depends(get_current_active_user),
):
    return await feedback_service.get_feedback_stats(db)


# ── Training management endpoints ─────────────────────────────────────────────

@router.post("/training/export", status_code=202)
async def trigger_export(
    _=Depends(require_role(UserRole.QA_LEAD)),
):
    return feedback_service.trigger_export()


@router.post("/training/finetune", status_code=202)
async def trigger_finetune(
    track: str = Body(..., embed=True),
    _=Depends(require_role(UserRole.QA_LEAD)),
):
    return feedback_service.trigger_finetune(track)


@router.post("/training/promote", status_code=200)
async def promote_model(
    body: PromoteModelRequest,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_role(UserRole.ADMIN)),
):
    result = await feedback_service.promote_model(db, body, settings_provider())
    await db.commit()
    return result


@router.get("/training/status")
async def get_training_status(
    db: AsyncSession = Depends(get_db),
    _=Depends(get_current_active_user),
):
    return await feedback_service.get_training_status(db, settings)


def settings_provider() -> str:
    from app.core.config import settings
    return settings.LLM_PROVIDER
