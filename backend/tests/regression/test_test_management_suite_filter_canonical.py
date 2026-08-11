"""Test Management's suite filter was the odd one out.

Third find in the F-075 class — a canonical rule with a hand-rolled copy that
drifted. ``test_management_service`` filtered with a bare
``suite_name == suite_name``, bypassing both ``normalize_suite_name`` (used by
run compare) and ``analytics_service._effective_suite_sql()``.

**Measured live**, same project, same suite, three surfaces:

===========================  =================  =================
endpoint                     ``suite_name=api`` ``suite_name=API``
===========================  =================  =================
``analytics/coverage``       5                  **5**
``runs/compare``             5                  **5**
``test-management/cases``    5                  **0**
===========================  =================  =================

Two of three surfaces answer the same question the same way for either
spelling; this one silently returned nothing. The UI populates its filter from
the data so it happens to send the exact case, but the API is public — a CLI,
SDK or MCP caller passing ``"API"`` got an empty list and no error.

The second half of the fix is the effective-suite rule that ``backend/CLAUDE.md``
states as a hard convention: for a ``live_stream`` run the SDK sends the suite
once at session-create, so it lands on ``TestRun.primary_suite_name`` while
per-event ``TestCase.suite_name`` stays NULL, and a bare per-row filter returns
nothing for those runs. The run-level arm is restricted to ``live_stream``
because a multi-``<testsuite>`` upload has an authoritative per-row value that
must win — the shape #559 gave ``run_compare_service._load_test_rows`` after it
was found returning other suites' tests.

**Stated honestly**: the live-stream half is NOT reproducible on the homelab.
No live_stream run there has both a NULL per-row suite and a
``primary_suite_name``, so the fallback has nothing to recover. It is fixed
because it is the documented rule and the query already joins ``TestRun``, not
because a live probe showed it failing. The case-sensitivity half WAS
reproduced.
"""
from __future__ import annotations

import inspect
import re

import pytest

pytest.importorskip("sqlalchemy")

from app.services import test_management_service as tms  # noqa: E402

pytestmark = pytest.mark.regression

_MANAGED = inspect.getsource(tms.list_managed_test_cases)
_AUTOMATION = inspect.getsource(tms.list_automation_test_cases)


_SOURCES = {
    "list_managed_test_cases": _MANAGED,
    "list_automation_test_cases": _AUTOMATION,
}


@pytest.mark.parametrize("name", sorted(_SOURCES))
def test_the_suite_filter_is_case_insensitive(name: str):
    """``api`` and ``API`` must select the same rows, as they do on
    analytics/coverage and runs/compare."""
    src = _SOURCES[name]
    assert "normalize_suite_name(suite_name)" in src, (
        f"{name} still compares the raw string, so suite 'API' returns 0 rows "
        f"where 'api' returns 5"
    )
    assert re.search(r"func\.lower\(func\.trim\([A-Za-z]+\.suite_name\)\)", src), (
        f"{name} lower-cases only one side of the comparison"
    )


def test_no_bare_equality_survives():
    """The exact shape that caused it, in either function."""
    for src in (_MANAGED, _AUTOMATION):
        assert not re.search(r"\.suite_name == suite_name\b", src)


def test_automation_honours_the_effective_suite_rule():
    """A live_stream run tags its suite at the RUN level; a per-row-only filter
    returns nothing for it."""
    assert "TestRun.primary_suite_name" in _AUTOMATION, (
        "the automation list ignores run-level suite tagging, so live_stream "
        "runs vanish from a suite-filtered view"
    )
    assert "or_(" in _AUTOMATION


def test_the_run_level_arm_is_restricted_to_live_stream():
    """Unrestricted, it over-matches — a multi-<testsuite> upload has an
    authoritative per-row value, and #559 fixed exactly that shape in
    run_compare after it returned other suites' tests."""
    assert 'TestRun.trigger_source == "live_stream"' in _AUTOMATION, (
        "the run-level arm applies to uploads too, so scoping to one suite "
        "will return the whole run"
    )


def test_the_per_row_arm_survives():
    """Losing it would break suite filtering for the 99.98% of rows that carry
    a per-row suite name."""
    assert re.search(r"func\.lower\(func\.trim\(TestCase\.suite_name\)\) == suite_key", _AUTOMATION)


def test_it_uses_the_shared_helper_rather_than_another_copy():
    """The whole point of this class of fix: import the canonical rule instead
    of hand-rolling a third ``.strip().lower()``."""
    module_src = inspect.getsource(tms)
    assert "from app.services.run_compare_service import normalize_suite_name" in module_src
    assert ".strip().lower()" not in _MANAGED
    assert ".strip().lower()" not in _AUTOMATION


def test_other_filters_are_untouched():
    """Search, status and project scoping are independent of this change."""
    assert "ManagedTestCase.status == status" in _MANAGED
    assert "TestRun.project_id == project_id" in _AUTOMATION
    assert "like_contains(search)" in _AUTOMATION
