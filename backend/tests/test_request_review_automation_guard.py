"""Unit test for the request_test_case_review automation-id guard.

User-reported bug:
    POST /api/v1/test-management/cases/{id}/request-review returned 404
    with body ``{"detail":"Test case not found"}`` when the id was a
    per-run TestCase row (carried into the merged Test Management list
    via ``include_automation=true``). The frontend now hides the CTA on
    automation rows, but the backend also returns a 400 with a clearer
    detail message so direct API/CLI callers get an actionable error
    instead of a misleading "not found".
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

pytest.importorskip("sqlalchemy")

from fastapi import HTTPException  # noqa: E402


class _ExecResult:
    def __init__(self, value):
        self._v = value
    def scalar_one_or_none(self):
        return self._v

    def first(self):
        return self._v


@pytest.mark.asyncio
async def test_request_review_managed_case_not_found_returns_404():
    """A truly unknown id (not in managed_test_cases AND not in
    test_cases) still produces the original 404."""
    from app.services.test_management_service import request_test_case_review

    unknown_id = uuid.uuid4()
    db = SimpleNamespace(
        get=AsyncMock(return_value=None),
        execute=AsyncMock(return_value=_ExecResult(None)),
    )

    with pytest.raises(HTTPException) as exc:
        await request_test_case_review(db, unknown_id, current_user=SimpleNamespace(id=uuid.uuid4()))

    assert exc.value.status_code == 404
    assert "not found" in exc.value.detail.lower()


@pytest.mark.asyncio
async def test_request_review_automation_id_returns_400_with_promote_hint():
    """An id that exists in test_cases but not in managed_test_cases —
    the automation-row case — returns 400 with a hint that the row
    must be promoted before it can be reviewed. This pins the user-
    visible 'fix the 404 spam' contract."""
    from app.services.test_management_service import request_test_case_review

    automation_id = uuid.uuid4()
    db = SimpleNamespace(
        get=AsyncMock(return_value=None),                       # not a ManagedTestCase
        execute=AsyncMock(return_value=_ExecResult(automation_id)),  # is a TestCase
    )

    with pytest.raises(HTTPException) as exc:
        await request_test_case_review(db, automation_id, current_user=SimpleNamespace(id=uuid.uuid4()))

    assert exc.value.status_code == 400
    msg = exc.value.detail.lower()
    assert "automation" in msg
    assert "promoted" in msg or "managed" in msg


@pytest.mark.asyncio
async def test_request_review_managed_wrong_status_returns_lifecycle_conflict():
    """A real managed test case whose status doesn't allow a review
    transition returns the governed 409 contract. This path is distinct from
    the automation-id guidance."""
    from app.services.test_management_service import request_test_case_review

    managed_id = uuid.uuid4()
    managed = SimpleNamespace(
        id=managed_id,
        project_id=uuid.uuid4(),
        status="approved",
        author_id=uuid.uuid4(),
        reviewer_id=None,
    )
    db = SimpleNamespace(
        get=AsyncMock(return_value=managed),
        execute=AsyncMock(return_value=_ExecResult(managed)),
    )

    with pytest.raises(HTTPException) as exc:
        await request_test_case_review(
            db,
            managed_id,
            current_user=SimpleNamespace(id=uuid.uuid4(), role="QA_ENGINEER"),
        )

    assert exc.value.status_code == 409
    assert exc.value.detail["current_state"] == "approved"
    assert exc.value.detail["attempted_action"] == "request_review"


@pytest.mark.asyncio
async def test_automation_review_target_dependency_checks_run_project_access():
    from app.routers import test_management_shared

    automation_id = uuid.uuid4()
    automation = SimpleNamespace(id=automation_id)
    project_id = uuid.uuid4()
    actor = SimpleNamespace(id=uuid.uuid4(), role="QA_ENGINEER")
    db = SimpleNamespace(
        get=AsyncMock(return_value=None),
        execute=AsyncMock(return_value=_ExecResult((automation, project_id))),
    )

    with patch.object(
        test_management_shared,
        "resolve_project_scope",
        AsyncMock(side_effect=HTTPException(status_code=403, detail="Forbidden")),
    ) as resolve_scope:
        with pytest.raises(HTTPException) as exc:
            await test_management_shared.require_case_access_for_review_target(
                automation_id, db, actor
            )

    assert exc.value.status_code == 403
    resolve_scope.assert_awaited_once_with(db, actor, str(project_id))
