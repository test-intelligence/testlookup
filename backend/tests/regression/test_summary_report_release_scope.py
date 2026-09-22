"""Regression: the summary report's release scope reaches every section.

Found by the VIZ-201 scope work, fixed in VIZ-202: ``build_summary_report``
passed ``release_id`` to the top-failing list ONLY. The totals, run count,
per-suite rows and step success were computed project-wide, under a report
header (and the PDF built from the same payload) that named the release. On
the seeded characterisation project, release ``1.0.0-rc1`` over 90 days read
43 runs / 25 unique tests / 84.0% -- the whole project's figures -- where the
release itself holds 17 runs / 22 unique tests / 77.3%.

Every section helper already took ``release_id``; the builder simply never
handed it over. This pins the hand-over for both modes, and that an unscoped
report still passes nothing (the golden-backed unscoped SQL is unchanged).
"""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import pytest

pytest.importorskip("sqlalchemy")

from app.services import summary_report_service as svc  # noqa: E402

_SECTIONS = {
    "window": ("_window_totals", "_per_suite_breakdown_window"),
    "latest": ("_latest_totals", "_per_suite_breakdown_latest"),
}


def _patched(mode: str):
    totals_name, suites_name = _SECTIONS[mode]
    mocks = {
        totals_name: AsyncMock(return_value=(svc._Totals(10, 8, 1, 1, 0), 3, 100, None)),
        suites_name: AsyncMock(return_value=[]),
        "_per_suite_step_success": AsyncMock(return_value={}),
        "_top_failing_tests": AsyncMock(return_value=[]),
        "_enrich_failure_steps": AsyncMock(return_value=None),
        "_resolve_project_name": AsyncMock(return_value="Checkout"),
        "_count_flaky_tests": AsyncMock(return_value=0),
    }
    return mocks


def _release_of(mock: AsyncMock):
    call = mock.await_args
    assert call is not None, "the section was never computed"
    return call.kwargs.get("release_id")


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["window", "latest"])
async def test_every_section_receives_the_release(mode):
    release = str(uuid.uuid4())
    mocks = _patched(mode)
    with patch.multiple(svc, **mocks):
        await svc.build_summary_report(AsyncMock(), uuid.uuid4(), 30, mode, release_id=release)
    totals_name, suites_name = _SECTIONS[mode]
    for name in (totals_name, suites_name, "_per_suite_step_success", "_top_failing_tests"):
        assert _release_of(mocks[name]) == release, f"{name} ignored the report's release"


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["window", "latest"])
async def test_every_section_receives_the_suites(mode):
    mocks = _patched(mode)
    with patch.multiple(svc, **mocks):
        await svc.build_summary_report(
            AsyncMock(), uuid.uuid4(), 30, mode, suite_name=("payments", "cart"),
        )
    totals_name, suites_name = _SECTIONS[mode]
    for name in (totals_name, suites_name, "_per_suite_step_success", "_top_failing_tests"):
        assert mocks[name].await_args.kwargs.get("suite_name") == ("payments", "cart"), name


@pytest.mark.asyncio
async def test_an_unscoped_report_passes_no_release_or_suite():
    mocks = _patched("window")
    with patch.multiple(svc, **mocks):
        await svc.build_summary_report(AsyncMock(), uuid.uuid4(), 30, "window")
    for name in ("_window_totals", "_per_suite_breakdown_window", "_top_failing_tests"):
        kwargs = mocks[name].await_args.kwargs
        assert kwargs.get("release_id") is None and kwargs.get("suite_name") is None, name


# ── fields that do NOT follow the body's scope, declared (VIZ-202 review) ──
#
# ``meta.ignored_filters`` is per dimension, and these bodies ARE release-
# scoped, so a field that is not cannot be declared there. It is declared on
# the field (OpenAPI description) instead, and pinned here: the declaration
# and the behaviour must move together.


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["window", "latest"])
async def test_the_report_flaky_count_takes_the_suite_but_not_the_release(mode):
    mocks = _patched(mode)
    release = str(uuid.uuid4())
    with patch.multiple(svc, **mocks):
        await svc.build_summary_report(
            AsyncMock(), uuid.uuid4(), 30, mode, release_id=release, suite_name=("payments",),
        )
    args = mocks["_count_flaky_tests"].await_args.args
    # (db, project_id, suite): the suite is applied, no release is passed.
    assert args[2] == ("payments",), "flaky_test_count ignored the report's suite filter"
    assert release not in map(str, args) and not mocks["_count_flaky_tests"].await_args.kwargs


def test_the_report_flaky_count_declares_its_release_exception():
    from app.models.schemas import SummaryReportResponse

    description = SummaryReportResponse.model_fields["flaky_test_count"].description or ""
    assert description == svc.FLAKY_COUNT_SCOPE_NOTE
    assert "Not scoped by release_id" in description and "Scoped by suite_name" in description


def test_the_dashboard_declares_its_release_unscoped_fields():
    """``active_defects`` / ``flaky_test_count`` / ``new_failures_24h`` are the
    readiness verdict's hard-cap inputs and stay project-wide under a release
    filter (pre-existing; the golden's ``metrics_summary/release`` case pins
    their values). Declared on the route's OpenAPI description and the
    ``DashboardSummary`` model."""
    import inspect

    from app.models.schemas import DashboardSummary
    from app.routers import metrics
    from app.services import metrics_service

    assert metrics.DASHBOARD_RELEASE_UNSCOPED == (
        "active_defects", "flaky_test_count", "new_failures_24h",
    )
    doc = inspect.getdoc(metrics.dashboard_summary) or ""
    for field in metrics.DASHBOARD_RELEASE_UNSCOPED:
        assert field in doc, field
        assert "Not scoped by release_id" in (DashboardSummary.model_fields[field].description or "")
    # The behaviour the declaration describes: the flaky count is called
    # with the suite and no release (``_count_flaky_tests(db, project, suite)``).
    source = inspect.getsource(metrics_service.get_dashboard_summary)
    assert "_count_flaky_tests(db, project_id, suite_filter)" in source
