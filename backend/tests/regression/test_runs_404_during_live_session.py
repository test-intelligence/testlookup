"""Regression: 404s on the runs detail panel for in-flight live sessions.

Bug pinned (2026-05-19): three endpoints all hit by the Run Detail
page returned 404 during the first ~30s of a new live session because
the ``test_runs`` row didn't exist yet:

  * ``GET /api/v1/runs/{id}`` — see also
    ``test_get_run_live_session_fallback.py`` for the service-layer
    fallback path.
  * ``GET /api/v1/runs/{id}/regression-diff`` — now returns an
    in-progress payload instead of 404 when a matching LiveSession
    exists.
  * ``GET /api/v1/test-cases/{id}/review`` — switched from 404 to
    200/null for the "no review yet" case so the browser network tab
    stays clean for the common pre-review state.

What this file pins:

  * Regression diff returns ``status="in_progress"`` + empty diff
    arrays when only a LiveSession exists.
  * Regression diff still 404s when neither row exists.
  * Test-case review GET returns ``None`` (200) when no review row
    exists, never 404.
  * Test-case review GET still 404s when the test case itself
    doesn't exist (a separate failure mode).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _scalar_result(value):
    res = MagicMock()
    res.scalar_one_or_none = MagicMock(return_value=value)
    return res


# ── regression-diff ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_regression_diff_returns_in_progress_for_live_session():
    """In-flight live session → graceful in-progress payload, not 404."""
    from app.routers.runs import get_regression_diff

    run_id = uuid.uuid4()
    live = SimpleNamespace(
        id=run_id,
        project_id=uuid.uuid4(),
        started_at=datetime.now(timezone.utc),
        build_number="b1",
        suite_name="s1",
    )
    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[
        _scalar_result(None),   # TestRun missing
        _scalar_result(live),   # LiveSession found
    ])

    payload = await get_regression_diff(run_id=run_id, db=db, _=None)
    assert payload["status"] == "in_progress"
    assert payload["diff_available"] is False
    assert payload["reason"] == "live_run_in_progress"
    assert payload["added"] == []
    assert payload["removed"] == []
    assert payload["flipped_to_failing"] == []
    assert payload["flipped_to_passing"] == []


@pytest.mark.asyncio
async def test_regression_diff_still_404s_when_neither_row_exists():
    """If neither TestRun nor LiveSession have the id, 404 is the correct
    response — that's a real not-found, not an in-progress run."""
    from app.routers.runs import get_regression_diff
    from fastapi import HTTPException

    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[
        _scalar_result(None),
        _scalar_result(None),
    ])
    with pytest.raises(HTTPException) as exc:
        await get_regression_diff(run_id=uuid.uuid4(), db=db, _=None)
    assert exc.value.status_code == 404


# ── /test-cases/{id}/review ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_test_case_review_get_returns_none_when_no_review_yet():
    """Pre-fix: 404 on every page load until a review was recorded.
    Post-fix: 200 with ``null`` so the network tab stays clean."""
    from app.routers.test_execution_reviews import get_test_case_review

    test_case_id = uuid.uuid4()
    project_id = uuid.uuid4()
    db = AsyncMock()
    user = SimpleNamespace(id=uuid.uuid4())

    with patch("app.routers.test_execution_reviews.svc.get_test_case_project",
               AsyncMock(return_value=project_id)), \
         patch("app.routers.test_execution_reviews._assert_project_access",
               AsyncMock(return_value=None)), \
         patch("app.routers.test_execution_reviews.svc.get_review",
               AsyncMock(return_value=None)):
        result = await get_test_case_review(
            test_case_id=test_case_id, db=db, current_user=user,
        )
    assert result is None, (
        "GET /test-cases/{id}/review must return None (200), not 404, "
        "when no review row exists. The browser network tab is part of "
        "the user-facing surface; 404s on it look like real errors."
    )


@pytest.mark.asyncio
async def test_test_case_review_get_404s_when_test_case_is_missing():
    """The 404 path that REMAINS — the test case itself doesn't exist."""
    from app.routers.test_execution_reviews import get_test_case_review
    from fastapi import HTTPException

    test_case_id = uuid.uuid4()
    db = AsyncMock()
    user = SimpleNamespace(id=uuid.uuid4())

    with patch("app.routers.test_execution_reviews.svc.get_test_case_project",
               AsyncMock(return_value=None)):
        with pytest.raises(HTTPException) as exc:
            await get_test_case_review(
                test_case_id=test_case_id, db=db, current_user=user,
            )
    assert exc.value.status_code == 404
    assert "Test case not found" in exc.value.detail
