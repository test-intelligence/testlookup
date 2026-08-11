"""The "New failures (24h)" KPI counted FAILED but not BROKEN.

Fourth find in the F-075 class — a canonical rule with hand-rolled copies that
drifted. ``metrics_service`` declares the rule in its own comment three lines
from the offending code::

    # a "failure" is FAILED *or* BROKEN — the canonical failed set used
    # everywhere else (see flaky_signals._FAILED_STATUSES)

and applies it in ``_evaluated`` and in the trend query — whose comment
describes *this exact bug* being fixed there: *"BROKEN was previously omitted
here, so the trend line read 83.9% for the same window the headline reported
81.0% — and a day whose only failures were BROKEN charted as a flat 100%."*
``new_failures_24h`` was the last holdout.

**Reproduced live** on a throwaway project holding one PASSED, one FAILED and
one BROKEN test (the BROKEN one ingested from a JUnit ``<error>`` element)::

    new_failures_24h : 1        <-- truth is 2
    avg_pass_rate_7d : 33.3     <-- 1/3, so BROKEN *is* counted as a non-pass

One payload, two definitions of failure. A day whose only failures were
infrastructure errors showed zero new failures on the dashboard while the pass
rate beside it fell.

The fix imports ``flaky_signals._FAILED_STATUSES`` rather than writing a fifth
copy of the constant — it already exists in four modules (``flaky_signals``,
``analysis_report_service``, ``digest_content_service``,
``agents.ingestion_agent``). They agree today; a fifth copy is how they stop.
"""
from __future__ import annotations

import inspect
import re

import pytest

pytest.importorskip("sqlalchemy")

from app.models.postgres import TestStatus  # noqa: E402
from app.services import metrics_service  # noqa: E402
from app.services.flaky_signals import _FAILED_STATUSES  # noqa: E402

pytestmark = pytest.mark.regression

_SRC = inspect.getsource(metrics_service.get_dashboard_summary)


def test_the_canonical_failed_set_is_failed_or_broken():
    """Guard the constant itself, not just this one consumer."""
    assert _FAILED_STATUSES == {TestStatus.FAILED.value, TestStatus.BROKEN.value}


def test_new_failures_counts_broken():
    assert "TestCase.status.in_(_FAILED_STATUSES)" in _SRC, (
        "the 24h failure KPI matches FAILED only, so a day whose failures were "
        "all BROKEN reports zero new failures while the pass rate drops"
    )


def test_the_bare_failed_only_comparison_is_gone():
    """The exact shape that caused it."""
    assert not re.search(r"TestCase\.status == TestStatus\.FAILED\b", _SRC)


def test_it_imports_the_constant_rather_than_redeclaring_it():
    """Four copies of this set already exist and currently agree. A fifth is
    how they stop agreeing — the whole point of this class of fix."""
    module_src = inspect.getsource(metrics_service)
    assert "from app.services.flaky_signals import _FAILED_STATUSES" in module_src
    assert not re.search(
        r"^_FAILED_STATUSES\s*=", module_src, re.M
    ), "metrics_service declared its own copy instead of importing the canonical one"


def test_the_module_still_agrees_with_itself():
    """``_evaluated`` and the trend query already counted BROKEN. The KPI now
    joins them, so every failure notion in this module matches."""
    assert metrics_service._evaluated(10, 0, 2) == 12, "BROKEN left the denominator"
    trend_src = inspect.getsource(metrics_service.get_trend_data)
    assert "SUM(tr.broken_tests)" in trend_src


def test_the_24h_window_and_scoping_still_apply():
    """The status change must not have displaced the other conditions."""
    assert "TestCase.created_at >= yesterday" in _SRC
    assert "TestRun.project_id == project_id" in _SRC
