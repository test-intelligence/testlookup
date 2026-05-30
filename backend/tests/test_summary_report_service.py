"""Unit tests for services.summary_report_service.

Covers both aggregation modes (``window`` + ``latest``), the empty-project
short-circuit, and the per-suite + top-failing-tests fan-out. DB access
is fully mocked — the service is the unit under test, not SQLAlchemy.

The mocking shape matters: ``build_summary_report`` issues multiple
``db.execute`` calls in a fixed order:

  1. ``_resolve_project_name``    → ``scalar_one_or_none()`` returning str|None
  2. ``_window_totals`` / ``_latest_totals`` → ``one()`` returning a row
     with fields ``runs, total, passed, failed, skipped, broken,
     avg_duration_ms, latest``
  3. ``_per_suite_breakdown_*`` → ``all()`` returning suite-row tuples
  4. ``_count_flaky_tests`` → ``scalar()`` returning int  (called by the
     metrics_service helper we re-use)
  5. ``_top_failing_tests`` → ``all()`` returning failing-test rows

We side_effect ``db.execute`` with the matching mock results in this
order; if the implementation re-orders the queries the test will catch
it.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest


def _scalar(value):
    res = MagicMock()
    res.scalar = MagicMock(return_value=value)
    res.scalar_one_or_none = MagicMock(return_value=value)
    return res


def _one(value):
    res = MagicMock()
    res.one = MagicMock(return_value=value)
    return res


def _all(rows):
    res = MagicMock()
    res.all = MagicMock(return_value=rows)
    return res


def _totals_row(*, runs=0, total=0, passed=0, failed=0, skipped=0, broken=0,
                avg=0, latest=None):
    """Row shape returned by ``_window_totals`` *run-aggregate* / ``_latest_totals``.

    Carries both the legacy ``total/passed/...`` names (consumed by
    ``_latest_totals``) and the ``agg_*`` aliases (consumed by
    ``_window_totals`` to derive a run-aggregate fallback). The window
    path also issues a second ``db.execute`` for the unique-fingerprint
    count — see ``_uniq_row``.
    """
    return SimpleNamespace(
        runs=runs,
        total=total,
        passed=passed,
        failed=failed,
        skipped=skipped,
        broken=broken,
        avg_duration_ms=avg,
        latest=latest,
        agg_total=total,
        agg_passed=passed,
        agg_failed=failed,
        agg_skipped=skipped,
        agg_broken=broken,
    )


def _uniq_row(*, total=0, passed=0, failed=0, skipped=0, broken=0):
    """Row shape returned by the unique-fingerprint count query in
    ``_window_totals``. When ``total > 0`` the service surfaces these
    values as the headline; otherwise it falls back to the run-aggregate
    sums on the ``_totals_row``."""
    return SimpleNamespace(
        total=total,
        passed=passed,
        failed=failed,
        skipped=skipped,
        broken=broken,
    )


def _suite_row(*, suite_name, total, passed=0, failed=0, skipped=0,
               broken=0, last_run_at=None):
    return SimpleNamespace(
        suite_name=suite_name,
        total=total,
        passed=passed,
        failed=failed,
        skipped=skipped,
        broken=broken,
        last_run_at=last_run_at,
    )


def _top_row(*, suite_name, class_name, test_name, failures):
    return SimpleNamespace(
        suite_name=suite_name,
        class_name=class_name,
        test_name=test_name,
        failures=failures,
    )


@pytest.mark.asyncio
async def test_returns_empty_envelope_when_project_id_missing():
    """Caller without a project must NOT see cross-tenant aggregates."""
    from app.services import summary_report_service as svc

    db = AsyncMock()
    db.execute = AsyncMock()  # should not be called

    result = await svc.build_summary_report(db, project_id=None, days=7)

    assert result["project_id"] is None
    assert result["project_name"] is None
    assert result["totals"]["total_test_cases"] == 0
    assert result["suites"] == []
    assert result["top_failing_tests"] == []
    assert result["run_count"] == 0
    db.execute.assert_not_called()


@pytest.mark.asyncio
async def test_rejects_invalid_mode():
    from app.services import summary_report_service as svc

    db = AsyncMock()
    db.execute = AsyncMock(return_value=_scalar("P1"))
    with pytest.raises(ValueError):
        await svc.build_summary_report(
            db, project_id=uuid.uuid4(), days=7, mode="weekly",  # type: ignore[arg-type]
        )


@pytest.mark.asyncio
async def test_window_mode_aggregates_runs_and_emits_per_suite_breakdown():
    """Happy path for ``window`` mode: totals + suites + top failing."""
    from app.services import summary_report_service as svc

    project_id = uuid.uuid4()
    now = datetime.now(timezone.utc)
    db = AsyncMock()
    # The flaky-count helper (metrics_service._count_flaky_tests) calls
    # db.execute internally too; queue its scalar result in the right slot.
    db.execute = AsyncMock(side_effect=[
        _scalar("GoogleProject"),                       # _resolve_project_name
        _one(_totals_row(                                # _window_totals run-aggregate
            runs=4, total=200, passed=180, failed=15, skipped=3, broken=2,
            avg=12_345, latest=now,
        )),
        # _window_totals unique-fingerprint pass. Same numbers here so the
        # headline matches the per-run sum (no live-stream-only suites).
        _one(_uniq_row(total=200, passed=180, failed=15, skipped=3, broken=2)),
        _all([                                           # _per_suite_breakdown_window
            _suite_row(suite_name="auth-api", total=80,  passed=78, failed=2,  last_run_at=now),
            _suite_row(suite_name="checkout-api", total=120, passed=102, failed=13, broken=2, skipped=3, last_run_at=now),
        ]),
        _scalar(3),                                      # _count_flaky_tests
        _all([                                           # _top_failing_tests
            _top_row(suite_name="checkout-api", class_name="CheckoutTests", test_name="test_pay", failures=8),
            _top_row(suite_name="auth-api",     class_name="AuthTests",     test_name="test_login", failures=2),
        ]),
    ])

    result = await svc.build_summary_report(db, project_id=project_id, days=7, mode="window")

    assert result["mode"] == "window"
    assert result["project_name"] == "GoogleProject"
    assert result["run_count"] == 4
    assert result["runs_per_day"] == round(4 / 7, 2)
    assert result["totals"]["total_test_cases"] == 200
    assert result["totals"]["passed"] == 180
    assert result["totals"]["failed"] == 15
    assert result["totals"]["pass_rate_pct"] == pytest.approx(90.0, abs=0.1)
    # Skipped is excluded from "evaluated" → 180 / (180+15+2) ≈ 91.4 %
    assert result["totals"]["weighted_pass_rate_pct"] == pytest.approx(91.4, abs=0.1)
    assert result["flaky_test_count"] == 3
    # Per-suite breakdown preserves order from the service.
    assert [s["suite_name"] for s in result["suites"]] == ["auth-api", "checkout-api"]
    assert result["suites"][1]["pass_rate_pct"] == pytest.approx(85.0, abs=0.1)
    # Top failing tests carry the failure count straight through.
    assert result["top_failing_tests"][0]["failures"] == 8


@pytest.mark.asyncio
async def test_latest_mode_uses_per_suite_snapshot_and_sets_no_runs_per_day():
    """``latest`` mode: no avg-runs-per-day, latest-per-suite branch fires."""
    from app.services import summary_report_service as svc

    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[
        _scalar("P"),                                                       # name
        _one(_totals_row(runs=2, total=50, passed=45, failed=5, latest=None)),  # _latest_totals
        _all([_suite_row(suite_name="smoke", total=50, passed=45, failed=5)]), # _per_suite_breakdown_latest
        _scalar(0),                                                          # flaky
        _all([]),                                                            # top failing
    ])

    result = await svc.build_summary_report(
        db, project_id=uuid.uuid4(), days=7, mode="latest",
    )

    assert result["mode"] == "latest"
    assert result["runs_per_day"] is None  # latest mode has no per-day rate
    assert result["totals"]["pass_rate_pct"] == pytest.approx(90.0, abs=0.1)
    assert result["suites"][0]["suite_name"] == "smoke"
    assert result["top_failing_tests"] == []


@pytest.mark.asyncio
async def test_zero_totals_produce_safe_pct_math():
    """No runs in window → percentages are 0.0, not NaN/ZeroDivisionError."""
    from app.services import summary_report_service as svc

    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[
        _scalar("P"),
        _one(_totals_row()),       # _window_totals run-aggregate — all zeros
        _one(_uniq_row()),         # _window_totals unique-fingerprint — all zeros
        _all([]),                  # no suites
        _scalar(0),                # no flaky
        _all([]),                  # no top failing
    ])

    result = await svc.build_summary_report(
        db, project_id=uuid.uuid4(), days=1, mode="window",
    )

    t = result["totals"]
    assert t["total_test_cases"] == 0
    assert t["pass_rate_pct"] == 0.0
    assert t["weighted_pass_rate_pct"] == 0.0
    assert t["fail_rate_pct"] == 0.0
    assert result["flaky_rate_pct"] == 0.0


@pytest.mark.asyncio
async def test_suite_row_helper_computes_evaluated_pass_rate():
    """``_suite_row_to_dict`` is the source of truth for per-suite math —
    pin both ``pass_rate_pct`` (passed / total) and ``weighted_pass_rate_pct``
    (passed / (passed + failed + broken))."""
    from app.services.summary_report_service import _suite_row_to_dict

    row = _suite_row(suite_name="x", total=100, passed=70, failed=20, skipped=10)
    out = _suite_row_to_dict(row)
    assert out["pass_rate_pct"] == 70.0
    # Skipped excluded → 70 / 90 ≈ 77.8 %
    assert out["weighted_pass_rate_pct"] == pytest.approx(77.8, abs=0.1)
