"""Owner report 2026-10-10: "/reports/summary shows no records in the last 24 hours".

The report was right -- every demo project's newest run was 39 h old, and only
the E2E project had run that day -- but an empty window gave no hint of why, so
it read as a broken page. The payload now carries the newest in-scope run
before the window when the window is empty (``last_run_before_window_at``), and
the page names it and offers the smallest window that holds it.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.dialects import postgresql

pytestmark = pytest.mark.regression

P = uuid.UUID("11111111-1111-4111-8111-111111111111")


def _totals():
    from app.services.summary_report_service import _Totals

    return _Totals(total=0, passed=0, failed=0, skipped=0, broken=0)


async def _build(run_count: int, last_before):
    from app.services import summary_report_service as svc

    last = AsyncMock(return_value=last_before)
    with patch.object(svc, "_resolve_project_name", AsyncMock(return_value="E-Commerce Platform")), \
            patch.object(svc, "_latest_totals", AsyncMock(return_value=(_totals(), run_count, 0, None))), \
            patch.object(svc, "_per_suite_breakdown_latest", AsyncMock(return_value=[])), \
            patch.object(svc, "_count_flaky_tests", AsyncMock(return_value=0)), \
            patch.object(svc, "_top_failing_tests", AsyncMock(return_value=[])), \
            patch.object(svc, "_enrich_failure_steps", AsyncMock()), \
            patch.object(svc, "_last_run_before", last), \
            patch.object(svc, "_per_suite_step_success", AsyncMock(return_value={})):
        try:
            payload = await svc.build_summary_report(MagicMock(), P, days=1, mode="latest")
        except Exception as exc:  # pragma: no cover - surfaced with context
            raise AssertionError(f"build failed: {exc!r}") from exc
    return payload, last


@pytest.mark.asyncio
async def test_an_empty_window_names_the_newest_run_before_it():
    before = datetime.now(timezone.utc) - timedelta(hours=39)
    payload, last = await _build(0, before)
    assert payload["last_run_before_window_at"] == before.isoformat()
    last.assert_awaited_once()


@pytest.mark.asyncio
async def test_a_window_with_runs_does_not_look_further_back():
    payload, last = await _build(3, None)
    assert payload["last_run_before_window_at"] is None
    last.assert_not_awaited()


@pytest.mark.asyncio
async def test_the_lookup_is_the_projects_runs_older_than_the_window():
    from app.services.summary_report_service import _last_run_before

    db = MagicMock()
    db.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=None)))
    start = datetime.now(timezone.utc) - timedelta(days=1)
    await _last_run_before(db, P, start)
    sql = str(db.execute.await_args.args[0].compile(dialect=postgresql.dialect()))
    assert "max(test_runs.created_at)" in sql
    assert "test_runs.project_id =" in sql and "test_runs.created_at <" in sql


def test_the_response_model_carries_it():
    from app.models.schemas import SummaryReportResponse
    from app.services.summary_report_service import _empty_envelope

    assert "last_run_before_window_at" in SummaryReportResponse.model_fields
    assert SummaryReportResponse(**_empty_envelope(days=1, mode="latest")).last_run_before_window_at is None
