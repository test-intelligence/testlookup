"""Root-cause correction learning loop.

Closes the loop opened by the AIFeedback capture path
(``feedback_service.submit_feedback``): when a QA engineer marks an AI
classification INCORRECT and supplies the right ``failure_category`` /
``root_cause_summary``, that correction is now APPLIED on future re-analysis of
the *same logical test* (matched by ``test_fingerprint``), so a mistake the team
already fixed is never silently recomputed and repeated. This turns per-user
corrections into compounding accuracy — the same pattern the flaky-confidence
model uses for quarantine decisions, but deterministic (no training needed).

Capture (already existed): ``AIFeedback`` rows + a patch to the current
``AIAnalysis`` row. This module adds the read side:

  * ``get_corrections_for_fingerprints`` — ONE batched, project-scoped query for
    the most-recent human correction per fingerprint (mirrors how the analysis
    agent batch-fetches historical counts; never N+1).
  * ``build_corrected_analysis`` — a pure builder that turns a correction into
    the analysis-result dict the pipeline would otherwise compute, bypassing the
    known-wrong rules/ML/LLM path with a high-confidence, human-provenanced
    verdict.

The semantic analysis cache is separately invalidated on correction
(``feedback_service`` → ``semantic_cache_invalidate``) so a stale wrong answer
isn't re-served to a similar test.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.postgres import (
    AIAnalysis,
    AIFeedback,
    FailureCategory,
    FeedbackRating,
    TestCase,
    TestRun,
)

# A human correction is authoritative — it carries near-certain confidence.
_HUMAN_CORRECTION_CONFIDENCE = 95


async def get_corrections_for_fingerprints(
    db: AsyncSession,
    project_id: Any,
    fingerprints: list[str],
) -> dict[str, dict]:
    """Return ``{test_fingerprint: correction}`` for the MOST RECENT human
    INCORRECT correction of each fingerprint within *project_id*.

    ``correction`` = ``{corrected_category, corrected_root_cause, feedback_id,
    corrected_at}``. Fingerprints with no correction are absent. One batched
    query — safe to call once per run over all failing fingerprints.
    """
    fps = [fp for fp in dict.fromkeys(fingerprints) if fp]  # dedup, drop falsy
    if not fps:
        return {}

    rows = (
        await db.execute(
            select(
                TestCase.test_fingerprint,
                AIFeedback.corrected_category,
                AIFeedback.corrected_root_cause,
                AIFeedback.id,
                AIFeedback.created_at,
            )
            .join(AIAnalysis, AIFeedback.analysis_id == AIAnalysis.id)
            .join(TestCase, AIAnalysis.test_case_id == TestCase.id)
            .join(TestRun, TestCase.test_run_id == TestRun.id)
            .where(
                TestRun.project_id == project_id,
                TestCase.test_fingerprint.in_(fps),
                AIFeedback.rating == FeedbackRating.INCORRECT,
                AIFeedback.corrected_category.isnot(None),
            )
            .order_by(TestCase.test_fingerprint, AIFeedback.created_at.desc())
        )
    ).all()

    out: dict[str, dict] = {}
    for fp, category, root_cause, feedback_id, created_at in rows:
        if fp in out:
            continue  # first row per fp is the most recent (created_at DESC)
        out[fp] = {
            "corrected_category": category,
            "corrected_root_cause": root_cause,
            "feedback_id": str(feedback_id),
            "corrected_at": created_at.isoformat() if created_at else None,
        }
    return out


def build_corrected_analysis(correction: dict) -> dict:
    """Pure: turn a stored human correction into an analysis-result dict.

    Produces the same shape the classifier would, but with the human-confirmed
    category/summary, high confidence, ``requires_human_review=False`` (already
    reviewed), and provenance marking it human-corrected so the decision-trail
    UI and downstream consumers can see WHY the AI path was bypassed.
    """
    category = correction["corrected_category"]
    root_cause = (
        correction.get("corrected_root_cause")
        or "Classification set by human review (a prior AI verdict was corrected)."
    )
    return {
        "failure_category": category,
        "root_cause_summary": root_cause,
        "confidence_score": _HUMAN_CORRECTION_CONFIDENCE,
        "is_flaky": category == FailureCategory.FLAKY.value,
        "requires_human_review": False,
        "recommended_actions": [],
        "classified_by": "human_correction",
        "human_corrected": True,
        "correction_feedback_id": correction.get("feedback_id"),
        # Mirror the analysis_router routing record so the decision trail shows
        # a "human_corrected" engine rather than an empty/derived one.
        "_routing": {
            "mode_requested": None,
            "mode_resolved": "human_corrected",
            "mode_used": "human_corrected",
            "fallback_from": None,
            "fallback_reason": None,
            "source": "human_correction",
        },
    }
