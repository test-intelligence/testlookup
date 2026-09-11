from __future__ import annotations

from datetime import datetime, timezone
import uuid

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.mongo import Collections
from app.services.privacy_service import sanitize_for_persistence

from app.models.postgres import AIAnalysis, AIFeedback, DecisionReportFeedback, Defect, FeedbackRating, ModelVersion, TestRun


async def submit_feedback(db: AsyncSession, analysis_id: uuid.UUID, body, current_user) -> dict:
    analysis = (await db.execute(select(AIAnalysis).where(AIAnalysis.id == analysis_id))).scalar_one_or_none()
    if not analysis:
        raise HTTPException(404, detail="Analysis not found")

    # ``AIAnalysis`` carries no project of its own; it is reached through
    # ``TestCase -> TestRun.project_id``. Without this the endpoint was a
    # cross-tenant WRITE behind nothing but an authenticated session (no role
    # gate at all): a ``rating=INCORRECT`` submission overwrites another
    # tenant's ``failure_category`` and ``root_cause_summary``, clears their
    # ``requires_human_review`` flag, and evicts their semantic-cache entry.
    # ``_invalidate_analysis_cache_for`` below already performs exactly this
    # join -- the scope was one line away the whole time.
    await _require_analysis_access(db, analysis, current_user)

    feedback = AIFeedback(
        analysis_id=analysis_id,
        test_case_id=analysis.test_case_id,
        user_id=current_user.id,
        rating=body.rating,
        corrected_category=body.corrected_category,
        corrected_root_cause=body.corrected_root_cause,
        comment=body.comment,
        source="manual",
        exported=False,
    )
    db.add(feedback)

    if body.corrected_category and body.rating == FeedbackRating.INCORRECT:
        analysis.failure_category = body.corrected_category
        if body.corrected_root_cause:
            analysis.root_cause_summary = body.corrected_root_cause
        analysis.requires_human_review = False
        # Close the correction loop: evict the stale (now-known-wrong) verdict
        # from the semantic analysis cache so it isn't re-served to this or a
        # similar test. Best-effort — never fail the feedback submission.
        await _invalidate_analysis_cache_for(db, analysis.test_case_id)

    # stage-only: router handler commits
    return {"feedback_id": str(feedback.id), "message": "Feedback recorded — thank you!"}


async def _require_analysis_access(db: AsyncSession, analysis, current_user) -> None:
    """Verify the caller may act on the project that owns ``analysis``.

    An analysis with no reachable test case (the test run was purged) is
    treated as not found rather than silently allowed -- failing closed, since
    an unreachable owner cannot be checked against.
    """
    from app.core.deps import resolve_project_scope
    from app.models.postgres import TestCase

    project_id = (
        await db.execute(
            select(TestRun.project_id)
            .join(TestCase, TestCase.test_run_id == TestRun.id)
            .where(TestCase.id == analysis.test_case_id)
        )
    ).scalar_one_or_none()
    if project_id is None:
        raise HTTPException(404, detail="Analysis not found")
    await resolve_project_scope(db, current_user, str(project_id))


async def _invalidate_analysis_cache_for(db: AsyncSession, test_case_id) -> None:
    """Evict the semantic-cache entry for a corrected test's error signature."""
    try:
        from app.models.postgres import TestCase, TestRun
        from app.services.semantic_cache import semantic_cache_invalidate

        row = (
            await db.execute(
                select(
                    TestCase.test_name,
                    TestCase.error_message,
                    TestCase.stack_trace,
                    TestRun.project_id,
                )
                .join(TestRun, TestCase.test_run_id == TestRun.id)
                .where(TestCase.id == test_case_id)
            )
        ).first()
        if row is None:
            return
        await semantic_cache_invalidate(
            test_name=row.test_name or "",
            error_message=row.error_message or "",
            stack_trace=row.stack_trace or "",
            project_id=str(row.project_id) if row.project_id else None,
        )
    except Exception:  # pragma: no cover — best-effort, must not block feedback
        pass


async def latest_analysis_for_fingerprint(
    db: AsyncSession, project_id: uuid.UUID, fingerprint: str,
) -> dict | None:
    """Latest AI analysis for a ``(project, test_fingerprint)`` pair (US-2.4).

    The Failure Analysis page identifies tests by fingerprint (from the
    analytics top-failing/flaky lists) but the feedback endpoints key on
    ``analysis_id`` — this bridges the two. Project-scoped via the
    ``TestCase → TestRun`` join because ``test_fingerprint`` is not salted
    per-project (two projects with a same-named test share a fingerprint).

    Read-only; returns ``None`` when the test has never been analysed.
    """
    from app.models.postgres import TestCase, TestRun

    row = (
        await db.execute(
            select(
                AIAnalysis.id,
                AIAnalysis.failure_category,
                AIAnalysis.created_at,
            )
            .join(TestCase, AIAnalysis.test_case_id == TestCase.id)
            .join(TestRun, TestCase.test_run_id == TestRun.id)
            .where(
                TestRun.project_id == project_id,
                TestCase.test_fingerprint == fingerprint,
            )
            .order_by(AIAnalysis.created_at.desc())
            .limit(1)
        )
    ).first()
    if row is None:
        return None
    category = row.failure_category
    return {
        "analysis_id": row.id,
        # Normalise enum instances to their wire value; the column is a plain
        # String(30) so raw driver rows already come back as str.
        "failure_category": getattr(category, "value", category),
        "analyzed_at": row.created_at,
    }


# ── Fix outcomes (Agentic plan AI-5) ─────────────────────────────────────────

# Outcome vocabulary for record_fix_outcome. "fixed" = a fix informed by the
# diagnosis merged and verified green; "not_fixed" = the attempted fix did not
# resolve the failure; "reverted" = the fix was rolled back.
FIX_OUTCOMES = ("fixed", "not_fixed", "reverted")

# Outcome → FeedbackRating mapping. A merged-and-verified fix confirms the
# analysis verdict (CORRECT ⇒ the analysis category becomes a usable training
# label via label_provenance.resolve_feedback_label). A failed or reverted fix
# marks the verdict suspect (INCORRECT) — but carries NO corrected category,
# so it is honestly unusable as a classification label while still counting
# as negative-rating signal.
_FIX_OUTCOME_RATINGS = {
    "fixed": FeedbackRating.CORRECT,
    "not_fixed": FeedbackRating.INCORRECT,
    "reverted": FeedbackRating.INCORRECT,
}


def compose_fix_outcome_comment(
    outcome: str, reference: str | None = None, comment: str | None = None,
) -> str:
    """Fold outcome + reference + free text into the AIFeedback comment.

    ``ai_feedback`` has no dedicated reference column and this signal does not
    justify a migration — the structured prefix keeps the outcome and the
    PR/commit reference greppable in exports and the audit UI.
    """
    parts = [f"[fix_outcome:{outcome}]"]
    if reference:
        parts.append(f"ref={reference}")
    if comment:
        parts.append(comment)
    return " ".join(parts)


async def record_fix_outcome(
    db: AsyncSession,
    project_id: uuid.UUID,
    fingerprint: str,
    outcome: str,
    reference: str | None,
    comment: str | None,
    current_user,
) -> dict:
    """Record a fix outcome for a test fingerprint as an AI-F1 training signal.

    Closes the coding-agent loop (Agentic plan AI-5): after an agent (or
    human) ships a fix informed by TestLookup's diagnosis, the merged /
    reverted outcome lands as an ``ai_feedback`` row with
    ``source="fix_outcome"`` — mapped by ``label_provenance`` into the
    ``human_indirect`` bucket (human-originated, but not an explicit rating),
    never ``human_direct`` and never ``llm_pseudo``.

    Stage-only: the router handler commits.
    """
    if outcome not in FIX_OUTCOMES:
        raise HTTPException(
            422, detail=f"outcome must be one of {list(FIX_OUTCOMES)}",
        )

    from app.models.postgres import TestCase, TestRun

    # Latest analysis for the (project, fingerprint) pair — same
    # project-scoped join discipline as latest_analysis_for_fingerprint
    # (fingerprints are not salted per project), plus test_case_id which the
    # feedback row needs.
    row = (
        await db.execute(
            select(
                AIAnalysis.id,
                AIAnalysis.test_case_id,
                AIAnalysis.failure_category,
            )
            .join(TestCase, AIAnalysis.test_case_id == TestCase.id)
            .join(TestRun, TestCase.test_run_id == TestRun.id)
            .where(
                TestRun.project_id == project_id,
                TestCase.test_fingerprint == fingerprint,
            )
            .order_by(AIAnalysis.created_at.desc())
            .limit(1)
        )
    ).first()
    if row is None:
        raise HTTPException(
            404,
            detail=(
                "No AI analysis recorded for this fingerprint in this project "
                "yet — there is no diagnosis to grade. Trigger an analysis "
                "first, then record the fix outcome."
            ),
        )

    rating = _FIX_OUTCOME_RATINGS[outcome]
    feedback = AIFeedback(
        analysis_id=row.id,
        test_case_id=row.test_case_id,
        user_id=current_user.id,
        rating=rating,
        comment=compose_fix_outcome_comment(outcome, reference, comment),
        source="fix_outcome",
        exported=False,
    )
    db.add(feedback)
    # Flush so the Python-side uuid default is applied and the returned
    # feedback_id is real (stage-only — the router handler still commits).
    await db.flush()

    category = row.failure_category
    return {
        "feedback_id": str(feedback.id),
        "analysis_id": str(row.id),
        "fingerprint": fingerprint,
        "outcome": outcome,
        "rating": rating.value,
        "analysis_category": getattr(category, "value", category),
        "label_provenance": "human_indirect",
        "message": (
            "Fix outcome recorded as a training signal "
            f"(source=fix_outcome, rating={rating.value})."
        ),
    }


async def update_feedback(db: AsyncSession, analysis_id: uuid.UUID, body, current_user) -> dict:
    feedback = (
        await db.execute(
            select(AIFeedback)
            .where(AIFeedback.analysis_id == analysis_id)
            .where(AIFeedback.user_id == current_user.id)
            .where(AIFeedback.source == "manual")
            .order_by(AIFeedback.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if not feedback:
        raise HTTPException(404, detail="No feedback found for this analysis from current user")

    feedback.rating = body.rating
    feedback.corrected_category = body.corrected_category
    feedback.corrected_root_cause = body.corrected_root_cause
    feedback.comment = body.comment
    feedback.exported = False
    # stage-only: router handler commits
    return {"message": "Feedback updated"}


async def get_feedback_stats(db: AsyncSession) -> dict:
    rows = await db.execute(select(AIFeedback.rating, func.count(AIFeedback.id)).group_by(AIFeedback.rating))
    counts = {str(row[0]): row[1] for row in rows.all()}
    # total + unexported in one aggregate query instead of two separate COUNTs
    # over the same table. FILTER (exported IS FALSE) matches the prior
    # ``WHERE exported.is_(False)`` exactly (NULL is excluded by both).
    totals = (
        await db.execute(
            select(
                func.count(AIFeedback.id).label("total"),
                func.count(AIFeedback.id)
                .filter(AIFeedback.exported.is_(False))
                .label("unexported"),
                # Folded into this same aggregate rather than issued as a
                # second round-trip: test_feedback_stats_single_aggregate pins
                # execute() to ONE call, and it is right to -- a status
                # endpoint should not fan out queries to answer one question.
                func.count(AIFeedback.id)
                .filter(AIFeedback.corrected_category.isnot(None))
                .label("labelled"),
            )
        )
    ).one()
    return {
        "total_feedback": totals.total,
        "unexported": totals.unexported,
        "by_rating": counts,
    }


def trigger_export() -> dict:
    from app.worker.training_tasks import export_training_data

    task = export_training_data.apply_async(queue="default")
    return {"message": "Training data export queued", "task_id": task.id}


def trigger_finetune(track: str) -> dict:
    if track not in ("classifier", "reasoning", "embedding"):
        raise HTTPException(400, detail="track must be classifier | reasoning | embedding")

    from app.worker.training_tasks import run_finetune_pipeline

    task = run_finetune_pipeline.apply_async(kwargs={"track": track}, queue="default")
    return {"message": f"Fine-tuning queued for track={track}", "task_id": task.id}


async def promote_model(db: AsyncSession, body, provider: str) -> dict:
    from app.services.model_registry import ModelRegistry

    await ModelRegistry.promote(
        track=body.track,
        model_name=body.model_name,
        metrics={
            "eval_accuracy": body.eval_accuracy,
            "baseline_accuracy": body.baseline_accuracy,
            "promoted_by": "manual",
        },
    )

    version = ModelVersion(
        track=body.track,
        model_name=body.model_name,
        provider=provider,
        status="active",
        eval_accuracy=body.eval_accuracy,
        baseline_accuracy=body.baseline_accuracy,
        promoted_at=datetime.now(timezone.utc),
    )
    db.add(version)
    # stage-only: router handler commits
    return {"message": f"Model {body.model_name} promoted for track={body.track}"}


async def get_training_status(db: AsyncSession, settings) -> dict:
    from app.services.model_registry import ModelRegistry

    registry = await ModelRegistry.get_all_status()
    # total + unexported in one aggregate query (was two separate COUNTs).
    totals = (
        await db.execute(
            select(
                func.count(AIFeedback.id).label("total"),
                func.count(AIFeedback.id)
                .filter(AIFeedback.exported.is_(False))
                .label("unexported"),
            )
        )
    ).one()
    total = totals.total
    unexported = totals.unexported

    # F-10: this endpoint reported the FINE-TUNE thresholds but not the ML
    # activation gate -- the one that actually decides whether feedback changes
    # anything. `auto` resolves ML -> LLM -> rules and ML only wins once a
    # trained model exists, which needs ML_MIN_TRAINING_SAMPLES labels. Below
    # that the LLM runs and learns nothing from corrections except
    # exact-fingerprint replay, and the only mechanism that would improve the
    # LLM itself (fine-tuning) is off by default, OpenAI-only, and refused under
    # AI_OFFLINE_MODE.
    #
    # None of that was visible anywhere. Measured on a live deployment: 4,849
    # analyses and ZERO feedback rows -- the loop had never been started, and
    # nothing said so. Reporting the gate here makes the cold start legible
    # instead of leaving someone to infer it from an unchanging model registry.
    labelled = totals.labelled
    try:
        from app.services.ml.classifier import MLClassifier

        model_available = bool(MLClassifier.is_available())
    except Exception:  # pragma: no cover - readiness must not raise
        model_available = False

    min_required = int(getattr(settings, "ML_MIN_TRAINING_SAMPLES", 0) or 0)
    if model_available:
        blocked_reason = None
    elif labelled < min_required:
        blocked_reason = (
            f"{labelled} of {min_required} labelled corrections collected; "
            "until a model is trained, corrections only replay on an exact "
            "test-fingerprint match"
        )
    else:
        blocked_reason = (
            f"{labelled} labelled corrections meet the {min_required} threshold "
            "but no trained model is present yet"
        )

    return {
        "finetune_enabled": settings.FINETUNE_ENABLED,
        "feedback": {"total": total, "unexported": unexported},
        "ml_activation": {
            "labelled_corrections": labelled,
            "min_required": min_required,
            "model_available": model_available,
            # What `auto` would pick right now, so the answer to "is feedback
            # doing anything?" is one field rather than an inference.
            "auto_resolves_to": "ml" if model_available else "llm_or_rules",
            "blocked_reason": blocked_reason,
        },
        "thresholds": {
            "classifier": settings.FINETUNE_CLASSIFIER_MIN_EXAMPLES,
            "reasoning": settings.FINETUNE_REASONING_MIN_EXAMPLES,
            "embedding": settings.FINETUNE_EMBED_MIN_PAIRS,
            "incremental_retrigger": settings.FINETUNE_INCREMENTAL_TRIGGER,
        },
        "active_models": registry,
    }


async def jira_resolution_webhook(db: AsyncSession, payload: dict) -> dict:
    # ``or {}`` / ``or ""``: Jira sends explicit nulls (QA-B45-2 note).
    issue = payload.get("issue") or {}
    fields = issue.get("fields") or {} if isinstance(issue, dict) else {}
    issue_key = (issue.get("key") if isinstance(issue, dict) else "") or ""
    status = str(((fields.get("status") or {}).get("name")) or "").lower()
    resolution = str(((fields.get("resolution") or {}).get("name")) or "").lower()

    is_resolved = status in ("done", "resolved", "closed", "fixed")
    is_invalid = resolution in ("won't fix", "duplicate", "invalid", "not a bug", "cannot reproduce")

    if not is_resolved and not is_invalid:
        return {"message": "no action — not a resolution event"}

    defect = (await db.execute(select(Defect).where(Defect.jira_ticket_id == issue_key))).scalar_one_or_none()
    if not defect:
        return {"message": f"no defect found for {issue_key}"}

    defect.jira_status = status
    defect.resolution_status = "INVALID" if is_invalid else "RESOLVED"
    if is_resolved and not is_invalid:
        defect.resolved_at = datetime.now(timezone.utc)

    analysis = (await db.execute(select(AIAnalysis).where(AIAnalysis.test_case_id == defect.test_case_id))).scalar_one_or_none()
    rating = None
    if analysis:
        rating = FeedbackRating.INCORRECT if is_invalid else FeedbackRating.CORRECT
        source = "jira_invalid" if is_invalid else "jira_resolved"
        db.add(
            AIFeedback(
                analysis_id=analysis.id,
                test_case_id=defect.test_case_id,
                user_id=None,
                rating=rating,
                source=source,
                exported=False,
            )
        )

    # stage-only: router handler commits
    return {
        "message": f"Feedback recorded: {issue_key} → {rating if analysis else 'no analysis found'}",
        "defect_id": str(defect.id),
    }

# ── Immutable DecisionReport feedback (Phase 5 FR-D4) ────────────────────────

_UTILITY_RATINGS = {"useful", "partially_useful", "not_useful"}
_CORRECTION_TYPES = {"category", "cause", "flaky", "release"}
_EVIDENCE_REF_FIELDS = ("id", "evidence_id", "type", "source", "kind", "checksum_sha256", "definition_version")


def _claim_map(report: dict) -> dict[str, dict]:
    intelligence = report.get("decision_intelligence") or {}
    claims = intelligence.get("claims") or []
    return {
        str(claim.get("claim_id")): claim
        for claim in claims
        if isinstance(claim, dict) and claim.get("claim_id")
    }


def _approved_claim_evidence(claim: dict, evidence_ids: list[str]) -> list[dict]:
    """Return only evidence projections already signed into the claim.

    The client submits IDs, never arbitrary evidence payloads. This prevents a
    reviewer from laundering a foreign URI/excerpt into the feedback ledger.
    """
    available: dict[str, dict] = {}
    for bucket in (claim.get("evidence") or [], claim.get("counter_evidence") or []):
        for ref in bucket:
            if not isinstance(ref, dict):
                continue
            ref_id = ref.get("id") or ref.get("evidence_id")
            if ref_id:
                available[str(ref_id)] = ref
    approved: list[dict] = []
    for requested in evidence_ids:
        ref = available.get(str(requested))
        if ref is None:
            raise HTTPException(422, detail=f"Evidence reference is not bound to claim: {requested}")
        projection = {
            key: ref[key]
            for key in _EVIDENCE_REF_FIELDS
            if key in ref and isinstance(ref[key], (str, int, float, bool))
        }
        projection["id"] = str(ref.get("id") or ref.get("evidence_id"))
        approved.append(projection)
    return approved


async def submit_decision_report_feedback(
    db: AsyncSession,
    mongo_db,
    *,
    run_id: uuid.UUID,
    report_id: str,
    body,
    current_user,
) -> dict:
    """Record utility/correction feedback against an exact published report.

    The SQL row is an immutable audit signal. The report itself is resolved from
    Mongo by project, run, report ID, version and published status; caller
    supplied hashes, claim text, and evidence payloads are never trusted.
    """
    run_row = (
        await db.execute(select(TestRun.project_id).where(TestRun.id == run_id))
    ).scalar_one_or_none()
    if run_row is None:
        raise HTTPException(404, detail="Test run not found")
    project_id = str(run_row)
    report = await mongo_db[Collections.DECISION_REPORTS].find_one(
        {
            "project_id": project_id,
            "test_run_id": str(run_id),
            "report_id": report_id,
            "report_version": int(body.report_version),
            "status": "published",
        },
        {"_id": 0},
    )
    if report is None:
        raise HTTPException(404, detail="Published DecisionReport version not found")
    report_hash = str(
        report.get("evidence_bundle_sha256")
        or (report.get("decision_intelligence") or {}).get("evidence_bundle_sha256")
        or ""
    )
    if len(report_hash) != 64:
        raise HTTPException(409, detail="Published report has no valid evidence fingerprint")

    idempotency_key = str(body.idempotency_key or uuid.uuid4())
    existing = (
        await db.execute(
            select(DecisionReportFeedback).where(
                DecisionReportFeedback.user_id == current_user.id,
                DecisionReportFeedback.idempotency_key == idempotency_key,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        if (
            str(existing.test_run_id) != str(run_id)
            or existing.report_id != report_id
            or int(existing.report_version) != int(body.report_version)
        ):
            raise HTTPException(409, detail="Idempotency key is bound to another report")
        return {
            "feedback_id": str(existing.id),
            "status": "already_recorded",
            "report_id": report_id,
            "report_version": int(body.report_version),
        }

    feedback_kind = str(body.feedback_kind)
    utility_rating = getattr(body, "utility_rating", None)
    claim_id = getattr(body, "claim_id", None)
    correction_type = getattr(body, "correction_type", None)
    corrected_value = getattr(body, "corrected_value", None)
    reason = getattr(body, "reason", None)
    evidence_ids = [str(item) for item in (getattr(body, "evidence_ids", None) or [])]
    claim_kind = None
    evidence_refs: list[dict] = []
    if feedback_kind == "utility":
        if utility_rating not in _UTILITY_RATINGS:
            raise HTTPException(422, detail="utility_rating is required for utility feedback")
        if claim_id or correction_type or corrected_value is not None or evidence_ids:
            raise HTTPException(422, detail="utility feedback cannot include claim correction fields")
    elif feedback_kind == "claim_correction":
        if not claim_id or correction_type not in _CORRECTION_TYPES:
            raise HTTPException(422, detail="claim_id and a valid correction_type are required")
        if not isinstance(reason, str) or not reason.strip():
            raise HTTPException(422, detail="A correction reason is required")
        if not evidence_ids or len(evidence_ids) > 5:
            raise HTTPException(422, detail="A correction must cite 1 to 5 claim evidence IDs")
        if not isinstance(corrected_value, (str, bool)) or (isinstance(corrected_value, str) and not corrected_value.strip()):
            raise HTTPException(422, detail="A corrected value is required")
        claim = _claim_map(report).get(str(claim_id))
        if claim is None:
            raise HTTPException(422, detail="Claim is not present in the selected report")
        claim_kind = str(claim.get("kind")) if claim.get("kind") else None
        evidence_refs = _approved_claim_evidence(claim, evidence_ids)
        reason = sanitize_for_persistence(reason)[:4000]
    else:
        raise HTTPException(422, detail="feedback_kind must be utility or claim_correction")

    feedback = DecisionReportFeedback(
        project_id=run_row,
        test_run_id=run_id,
        report_id=report_id,
        report_version=int(body.report_version),
        report_evidence_sha256=report_hash,
        user_id=current_user.id,
        feedback_kind=feedback_kind,
        utility_rating=utility_rating,
        claim_id=str(claim_id) if claim_id else None,
        claim_kind=claim_kind,
        correction_type=correction_type,
        corrected_value={"value": corrected_value} if corrected_value is not None else None,
        reason=reason,
        evidence_refs=evidence_refs,
        idempotency_key=idempotency_key,
    )
    db.add(feedback)
    await db.flush()
    return {
        "feedback_id": str(feedback.id),
        "status": "recorded",
        "report_id": report_id,
        "report_version": int(body.report_version),
        "feedback_kind": feedback_kind,
    }
