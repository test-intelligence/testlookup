"""Smoke tests for the summary-report router.

The router is thin (one service call per endpoint). These tests pin:

  * Authorisation: a caller who is not a member of the target project
    gets 403 (when ``get_accessible_project_ids`` returns a non-None
    set that doesn't contain the project_id).
  * Happy path: returns the service's envelope as a Pydantic
    ``SummaryReportResponse``.
  * PDF endpoint hands the JSON payload to the renderer and streams the
    bytes back with ``application/pdf``.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException


def _envelope(project_id: uuid.UUID, mode: str = "window") -> dict:
    """Minimum-viable response the service is expected to return."""
    return {
        "project_id": str(project_id),
        "project_name": "GoogleProject",
        "mode": mode,
        "window_days": 7,
        "generated_at": "2026-05-16T00:00:00+00:00",
        "period_start": "2026-05-09T00:00:00+00:00",
        "period_end": "2026-05-16T00:00:00+00:00",
        "totals": {
            "total_test_cases": 100,
            "passed": 90,
            "failed": 8,
            "skipped": 1,
            "broken": 1,
            "evaluated": 99,
            "pass_rate_pct": 90.0,
            "fail_rate_pct": 8.0,
            "skip_rate_pct": 1.0,
            "broken_rate_pct": 1.0,
            "weighted_pass_rate_pct": 90.9,
        },
        "run_count": 5,
        "runs_per_day": 0.71,
        "avg_duration_ms": 1234,
        "latest_run_at": "2026-05-15T00:00:00+00:00",
        "flaky_test_count": 2,
        "flaky_rate_pct": 2.0,
        "suites": [],
        "top_failing_tests": [],
    }


# ── _enforce_project_access ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_enforce_project_access_allows_admin_seeing_all():
    """ADMIN-style callers (``get_accessible_project_ids`` returns None) skip the check."""
    from app.routers.summary_report import _enforce_project_access

    db = AsyncMock()
    user = SimpleNamespace(id=uuid.uuid4())
    with patch(
        "app.routers.summary_report.get_accessible_project_ids",
        new=AsyncMock(return_value=None),
    ):
        await _enforce_project_access(db, user, uuid.uuid4())  # does not raise


@pytest.mark.asyncio
async def test_enforce_project_access_rejects_non_member():
    from app.routers.summary_report import _enforce_project_access

    db = AsyncMock()
    user = SimpleNamespace(id=uuid.uuid4())
    target = uuid.uuid4()
    accessible = {uuid.uuid4(), uuid.uuid4()}  # target NOT in this set
    with patch(
        "app.routers.summary_report.get_accessible_project_ids",
        new=AsyncMock(return_value=accessible),
    ):
        with pytest.raises(HTTPException) as exc_info:
            await _enforce_project_access(db, user, target)
        assert exc_info.value.status_code == 403


# ── GET /api/v1/reports/summary ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_summary_report_returns_service_envelope():
    from app.routers.summary_report import get_summary_report

    project_id = uuid.uuid4()
    user = SimpleNamespace(id=uuid.uuid4())
    db = AsyncMock()

    with patch(
        "app.routers.summary_report.get_accessible_project_ids",
        new=AsyncMock(return_value=None),
    ), patch(
        "app.services.summary_report_service.build_summary_report",
        new=AsyncMock(return_value=_envelope(project_id)),
    ):
        result = await get_summary_report(
            project_id=project_id, days=7, mode="window",
            # Explicit, because this calls the handler DIRECTLY. FastAPI
            # resolves `Query(None)` to None per request; a direct call gets
            # the Query OBJECT, which the release resolver then tries to parse
            # as a UUID and rejects with a 422.
            release_id=None,
            db=db, current_user=user,
        )

    assert result.project_id == str(project_id)
    assert result.totals.passed == 90
    assert result.mode == "window"
    assert result.runs_per_day == 0.71


@pytest.mark.asyncio
async def test_get_summary_report_preserves_phase5_step_fields():
    """Regression: the Phase 5 granular step enrichment must SURVIVE the
    ``SummaryReportResponse`` round-trip the router performs.

    Pydantic v2 defaults ``extra='ignore'`` — before the response models
    declared the optional step fields, the service's per-suite
    ``step_success_rate`` / ``passed_steps`` / ``total_steps`` and the
    per-test ``failure_step`` / ``step_breakdown`` were silently STRIPPED from
    the JSON the frontend consumes (only the /pdf path, which renders the raw
    payload, was unaffected). This drives the full router path and asserts the
    fields land on the validated response model.
    """
    from app.routers.summary_report import get_summary_report

    project_id = uuid.uuid4()
    user = SimpleNamespace(id=uuid.uuid4())
    db = AsyncMock()

    payload = _envelope(project_id)
    payload["suites"] = [
        {
            "suite_name": "Checkout",
            "total": 4, "passed": 3, "failed": 1, "skipped": 0, "broken": 0,
            "pass_rate_pct": 75.0, "weighted_pass_rate_pct": 75.0,
            "last_run_at": None,
            "step_success_rate": 88.0, "passed_steps": 8, "total_steps": 9,
        },
        {
            # A suite with no captured step data keeps None (additive/optional).
            "suite_name": "Smoke",
            "total": 2, "passed": 2, "failed": 0, "skipped": 0, "broken": 0,
            "pass_rate_pct": 100.0, "weighted_pass_rate_pct": 100.0,
            "last_run_at": None,
            "step_success_rate": None, "passed_steps": None, "total_steps": None,
        },
    ]
    payload["top_failing_tests"] = [
        {
            "suite_name": "Checkout", "class_name": "CartTest",
            "test_name": "test_apply_coupon", "failures": 3,
            "failure_step": "submit invalid coupon",
            "step_breakdown": [
                {"name": "open cart", "status": "PASSED", "assertion_message": None},
                {"name": "submit invalid coupon", "status": "FAILED",
                 "assertion_message": "expected 200 got 400"},
            ],
        },
    ]

    with patch(
        "app.routers.summary_report.get_accessible_project_ids",
        new=AsyncMock(return_value=None),
    ), patch(
        "app.services.summary_report_service.build_summary_report",
        new=AsyncMock(return_value=payload),
    ):
        result = await get_summary_report(
            project_id=project_id, days=7, mode="window",
            # Explicit, because this calls the handler DIRECTLY. FastAPI
            # resolves `Query(None)` to None per request; a direct call gets
            # the Query OBJECT, which the release resolver then tries to parse
            # as a UUID and rejects with a 422.
            release_id=None,
            db=db, current_user=user,
        )

    # Per-suite step success-rate survives validation (Critical/High findings).
    checkout = next(s for s in result.suites if s.suite_name == "Checkout")
    assert checkout.step_success_rate == 88.0
    assert checkout.passed_steps == 8
    assert checkout.total_steps == 9
    smoke = next(s for s in result.suites if s.suite_name == "Smoke")
    assert smoke.step_success_rate is None
    # Per-test failure location + step breakdown survive validation.
    top = result.top_failing_tests[0]
    assert top.failure_step == "submit invalid coupon"
    assert top.step_breakdown is not None
    assert top.step_breakdown[1].status == "FAILED"
    assert top.step_breakdown[1].name == "submit invalid coupon"


@pytest.mark.asyncio
async def test_get_summary_report_works_with_no_project_id():
    """Omitted project_id must NOT 500 — service returns an empty envelope."""
    from app.routers.summary_report import get_summary_report

    user = SimpleNamespace(id=uuid.uuid4())
    db = AsyncMock()
    empty_envelope = {
        **_envelope(uuid.uuid4()),
        "project_id": None,
        "project_name": None,
        "totals": {
            "total_test_cases": 0, "passed": 0, "failed": 0, "skipped": 0,
            "broken": 0, "evaluated": 0, "pass_rate_pct": 0.0, "fail_rate_pct": 0.0,
            "skip_rate_pct": 0.0, "broken_rate_pct": 0.0, "weighted_pass_rate_pct": 0.0,
        },
        "run_count": 0, "runs_per_day": 0.0, "avg_duration_ms": 0,
        "latest_run_at": None, "flaky_test_count": 0, "flaky_rate_pct": 0.0,
    }
    with patch(
        "app.services.summary_report_service.build_summary_report",
        new=AsyncMock(return_value=empty_envelope),
    ):
        result = await get_summary_report(
            project_id=None, days=7, mode="window", release_id=None,
            db=db, current_user=user,
        )

    assert result.project_id is None
    assert result.totals.total_test_cases == 0


# ── GET /api/v1/reports/summary/pdf ────────────────────────────────────────


@pytest.mark.asyncio
async def test_pdf_endpoint_streams_pdf_bytes():
    from app.routers.summary_report import export_summary_report_pdf

    project_id = uuid.uuid4()
    user = SimpleNamespace(id=uuid.uuid4())
    db = AsyncMock()
    fake_pdf = b"%PDF-1.4 ...fake bytes..."

    with patch(
        "app.routers.summary_report.get_accessible_project_ids",
        new=AsyncMock(return_value=None),
    ), patch(
        "app.services.summary_report_service.build_summary_report",
        new=AsyncMock(return_value=_envelope(project_id)),
    ), patch(
        "app.services.summary_report_pdf.render_summary_report_pdf",
        new=MagicMock(return_value=fake_pdf),
    ):
        response = await export_summary_report_pdf(
            project_id=project_id, days=7, mode="window",
            # Explicit, because this calls the handler DIRECTLY. FastAPI
            # resolves `Query(None)` to None per request; a direct call gets
            # the Query OBJECT, which the release resolver then tries to parse
            # as a UUID and rejects with a 422.
            release_id=None,
            db=db, current_user=user,
        )

    assert response.media_type == "application/pdf"
    disposition = response.headers["content-disposition"]
    assert disposition.startswith("attachment;")
    assert "summary-googleproject-7d-window.pdf" in disposition
