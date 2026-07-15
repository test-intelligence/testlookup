"""MCP tools for AI-classification feedback (PMF US-14.1 / US-2.4 / AI-5).

``correct_classification`` closes the triage loop: when the AI mislabels
a failure (e.g. INFRASTRUCTURE when it's really a PRODUCT_BUG), an agent
can record the correction. The backend applies it immediately (the
analysis row is updated and the stale verdict is evicted from the
semantic cache) and stores it as a training signal for the next
fine-tune cycle — the same path the UI's "correct classification"
dialog uses.

``record_fix_outcome`` (Agentic plan AI-5) closes the *fix* loop: after a
fix informed by TestLookup's diagnosis merges (or gets reverted), the
outcome lands as an ``ai_feedback`` row with ``source="fix_outcome"`` —
which the AI-F1 label-provenance policy buckets as ``human_indirect``
(human-originated, but not an explicit rating), so it can never be
mistaken for a direct correction or an LLM pseudo-label.

Thin wrappers over three endpoints:
  * ``GET  /api/v1/projects/{id}/analyses/lookup?fingerprint=`` — bridge
    from a test fingerprint to the latest ``analysis_id``.
  * ``POST /api/v1/feedback/{analysis_id}`` — the correction itself.
  * ``POST /api/v1/projects/{id}/fix-outcomes`` — the fix-outcome signal.

Auth/RBAC is server-side: any active project member may submit feedback,
and the ``ai_feedback`` row records the MCP login identity as the
correcting user.
"""
from __future__ import annotations

from typing import Optional

import client as api  # type: ignore[import]

# Mirrors backend FailureCategory (String column; backend validates).
FAILURE_CATEGORIES = (
    "PRODUCT_BUG",
    "INFRASTRUCTURE",
    "TEST_DATA",
    "AUTOMATION_DEFECT",
    "FLAKY",
    "UNKNOWN",
)


# Closed outcome vocabulary for record_fix_outcome (mirrors the backend's
# FIX_OUTCOMES; the endpoint validates too — this pre-check just gives the
# agent a faster, clearer error).
FIX_OUTCOMES = ("fixed", "not_fixed", "reverted")


def _fix_outcome_body(
    fingerprint: str,
    outcome: str,
    reference: Optional[str] = None,
    comment: Optional[str] = None,
) -> dict:
    """Build + validate the POST body for a fix-outcome signal.

    Raises ValueError on a missing fingerprint or an unknown outcome so the
    tool can return a structured error without a round-trip.
    """
    if not fingerprint or not fingerprint.strip():
        raise ValueError("fingerprint is required")
    normalized = outcome.strip().lower()
    if normalized not in FIX_OUTCOMES:
        raise ValueError(
            f"Unknown outcome '{outcome}'. Allowed: {', '.join(FIX_OUTCOMES)}"
        )
    body: dict = {"fingerprint": fingerprint.strip(), "outcome": normalized}
    if reference:
        body["reference"] = reference
    if comment:
        body["comment"] = comment
    return body


def _correction_body(
    corrected_category: str,
    comment: Optional[str] = None,
    corrected_root_cause: Optional[str] = None,
) -> dict:
    """Build the feedback POST body for a category correction.

    ``rating`` must be "incorrect" — the backend only applies
    ``corrected_category`` to the analysis row when the rating marks the
    original verdict as wrong.
    """
    category = corrected_category.strip().upper()
    if category not in FAILURE_CATEGORIES:
        raise ValueError(
            f"Unknown category '{corrected_category}'. "
            f"Allowed: {', '.join(FAILURE_CATEGORIES)}"
        )
    body: dict = {"rating": "incorrect", "corrected_category": category}
    if comment:
        body["comment"] = comment
    if corrected_root_cause:
        body["corrected_root_cause"] = corrected_root_cause
    return body


def register(mcp) -> None:  # noqa: ANN001

    @mcp.tool()
    async def correct_classification(
        project_id: str,
        fingerprint: str,
        corrected_category: str,
        comment: Optional[str] = None,
        corrected_root_cause: Optional[str] = None,
    ) -> dict:
        """
        SIDE EFFECT: overrides the AI's failure classification for a test
        and records the correction as a training signal.

        Effects: the latest AI analysis row for the fingerprint is updated
        to the corrected category (and root cause, if given), the stale
        verdict is evicted from the analysis cache so similar failures
        aren't re-labelled wrongly, and an `ai_feedback` row is stored
        under the MCP login identity for the fine-tuning export.

        Args:
            project_id: Project UUID the test belongs to.
            fingerprint: Test fingerprint (sha256(class::test)[:16]).
            corrected_category: The right category — one of PRODUCT_BUG,
                INFRASTRUCTURE, TEST_DATA, AUTOMATION_DEFECT, FLAKY, UNKNOWN.
            comment: Optional explanation of why the AI was wrong.
            corrected_root_cause: Optional corrected root-cause summary.
        """
        try:
            body = _correction_body(corrected_category, comment, corrected_root_cause)
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}

        try:
            found = await api.get(
                f"/api/v1/projects/{project_id}/analyses/lookup",
                params={"fingerprint": fingerprint},
            )
        except Exception as exc:
            return api.error_payload(exc)

        analysis_id = (found or {}).get("analysis_id")
        if not analysis_id:
            return {
                "ok": False,
                "error": (
                    "No AI analysis recorded for this fingerprint yet — "
                    "run trigger_ai_analysis on the failing test case first, "
                    "then correct its classification."
                ),
            }

        previous_category = (found or {}).get("failure_category")
        try:
            await api.post(f"/api/v1/feedback/{analysis_id}", json_body=body)
        except Exception as exc:
            return api.error_payload(exc)
        return {
            "ok": True,
            "action": "classification_corrected",
            "analysis_id": analysis_id,
            "fingerprint": fingerprint,
            "previous_category": previous_category,
            "new_category": body["corrected_category"],
            "note": (
                "Analysis updated and correction stored as a training "
                "signal for the next fine-tune cycle."
            ),
        }

    @mcp.tool()
    async def record_fix_outcome(
        project_id: str,
        fingerprint: str,
        outcome: str,
        reference: Optional[str] = None,
        comment: Optional[str] = None,
    ) -> dict:
        """
        SIDE EFFECT: record the outcome of a fix for a diagnosed test
        failure — this is how a coding agent's merged fix teaches the
        classifier (Agentic plan AI-5).

        Call it AFTER the fix's fate is known, not when you open the PR:
          - "fixed"     → the fix merged and the test is verified green;
            confirms the diagnosis (the analysis category becomes a
            training label).
          - "not_fixed" → the attempted fix did not resolve the failure;
            marks the diagnosis suspect.
          - "reverted"  → the fix was rolled back; marks the diagnosis
            suspect.

        The signal lands as an `ai_feedback` row with source
        `fix_outcome` under YOUR login identity, bucketed by the AI-F1
        label-provenance policy as `human_indirect` — it informs training
        but never outranks an explicit human correction
        (correct_classification). 404 when the fingerprint has never been
        analysed in this project (nothing to grade).

        Args:
            project_id: Project UUID the test belongs to.
            fingerprint: Test fingerprint (sha256(class::test)[:16]).
            outcome: One of "fixed", "not_fixed", "reverted".
            reference: Optional PR/commit/ticket reference (e.g.
                "org/repo#482", "abc1234").
            comment: Optional context (what the fix changed, how it was
                verified).
        """
        try:
            body = _fix_outcome_body(fingerprint, outcome, reference, comment)
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}

        try:
            data = await api.post(
                f"/api/v1/projects/{project_id}/fix-outcomes", json_body=body,
            )
        except Exception as exc:
            return api.error_payload(exc)
        data = data or {}
        return {
            "ok": True,
            "action": "fix_outcome_recorded",
            "feedback_id": data.get("feedback_id"),
            "analysis_id": data.get("analysis_id"),
            "fingerprint": fingerprint,
            "outcome": body["outcome"],
            "rating": data.get("rating"),
            "label_provenance": data.get("label_provenance", "human_indirect"),
            "note": data.get("message", "Fix outcome recorded."),
        }
