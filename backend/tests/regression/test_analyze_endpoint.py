"""Regression: ``GET /api/v1/analyze/{test_case_id}`` 500'd when the
stored ``failure_category`` was a string (not a FailureCategory enum).

Bug pinned: ``AIAnalysis.failure_category`` is declared as
``Mapped[Optional[FailureCategory]]`` but the column is
``String(30)``. SQLAlchemy returns a plain str, so ``.value`` on it
raised ``AttributeError`` — the endpoint 500'd on every legacy row.

Fix: ``get_existing_analysis`` coerces the value via
``getattr(raw_category, "value", None)`` and falls back to
``FailureCategory.UNKNOWN`` for any unrecognised string so the strict
Pydantic check on the response model doesn't 500 a second time.

What this file pins:

  * 422 on a non-UUID ``test_case_id`` (validation contract).
  * 404 when no AIAnalysis row exists (caller distinguishes
    "no analysis yet" vs "analysis failed").
  * Legacy string category survives — endpoint coerces to UNKNOWN
    rather than 500.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest


def _scalar_result(value):
    res = MagicMock()
    res.scalar_one_or_none = MagicMock(return_value=value)
    return res


def _authorized_admin():
    """An ADMIN user passes ``resolve_authorized_test_case`` without a
    ProjectMember lookup (see ``app/core/deps.py``)."""
    from app.models.postgres import UserRole

    return SimpleNamespace(id=uuid.uuid4(), role=UserRole.ADMIN, api_key_project_id=None)


def _ownership_result(project_id=None):
    """Result for the ``select(TestCase, TestRun)`` ownership join that
    ``resolve_authorized_test_case`` issues before the analysis lookup."""
    run_id = uuid.uuid4()
    res = MagicMock()
    res.one_or_none = MagicMock(
        return_value=(
            SimpleNamespace(id=uuid.uuid4(), test_run_id=run_id),
            SimpleNamespace(id=run_id, project_id=project_id or uuid.uuid4()),
        )
    )
    return res


@pytest.mark.asyncio
async def test_get_analysis_422s_on_invalid_uuid():
    from app.routers.analyze import get_existing_analysis
    from fastapi import HTTPException

    db = AsyncMock()
    with pytest.raises(HTTPException) as exc:
        await get_existing_analysis(test_case_id="not-a-uuid", db=db)
    assert exc.value.status_code == 422


@pytest.mark.asyncio
async def test_get_analysis_404s_when_no_row_exists():
    from app.routers.analyze import get_existing_analysis
    from fastapi import HTTPException

    db = AsyncMock()
    # 1st execute: the ownership join; 2nd: the AIAnalysis lookup that finds nothing.
    db.execute = AsyncMock(side_effect=[_ownership_result(), _scalar_result(None)])
    with pytest.raises(HTTPException) as exc:
        await get_existing_analysis(
            test_case_id=str(uuid.uuid4()), db=db, current_user=_authorized_admin()
        )
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_get_analysis_coerces_legacy_string_category_without_500():
    """The bug: ``failure_category`` came back as a str, ``.value`` raised
    AttributeError, endpoint 500'd. Fix coerces unknown strings to UNKNOWN."""
    from app.routers.analyze import get_existing_analysis
    from app.models.postgres import FailureCategory

    row = SimpleNamespace(
        test_case_id=uuid.uuid4(),
        root_cause_summary="rcs",
        failure_category="legacy_lowercase_value",  # NOT a FailureCategory enum
        backend_error_found=False,
        pod_issue_found=False,
        is_flaky=False,
        confidence_score=80,
        recommended_actions=[],
        evidence_references=[],
        tools_used=[],
        role_actions={},
        llm_provider="ollama",
        llm_model="qwen2.5:7b",
        requires_human_review=False,
    )
    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[_ownership_result(), _scalar_result(row)])

    result = await get_existing_analysis(
        test_case_id=str(row.test_case_id), db=db, current_user=_authorized_admin()
    )
    assert result.failure_category == FailureCategory.UNKNOWN
