"""MCP tools for AI-classification feedback (PMF US-14.1 / US-2.4).

``correct_classification`` closes the triage loop: when the AI mislabels
a failure (e.g. INFRASTRUCTURE when it's really a PRODUCT_BUG), an agent
can record the correction. The backend applies it immediately (the
analysis row is updated and the stale verdict is evicted from the
semantic cache) and stores it as a training signal for the next
fine-tune cycle — the same path the UI's "correct classification"
dialog uses.

Thin wrapper over two existing endpoints:
  * ``GET  /api/v1/projects/{id}/analyses/lookup?fingerprint=`` — bridge
    from a test fingerprint to the latest ``analysis_id``.
  * ``POST /api/v1/feedback/{analysis_id}`` — the correction itself.

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
