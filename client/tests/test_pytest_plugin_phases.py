"""The pytest plugin must report a result for every test, whichever phase decided it.

``_TestLookupPytestPlugin.pytest_runtest_logreport`` began with::

    if report.when != "call" or ...:
        return

pytest emits three reports per test -- ``setup``, ``call``, ``teardown`` -- and
a test that errors in a fixture produces a failed **setup** report and no
``call`` report at all. So a conftest fixture raising (DB unreachable, missing
env var) made pytest exit 1 with "50 errors" while TestLookup received a
session containing **zero** results: the run showed 0 tests and no failures, so
the pass rate and the release-risk signal read "nothing wrong" rather than
"everything broke". That is this repo's own "absence is not health" class,
arriving through the SDK.

``@pytest.mark.skip`` also resolves during setup, so skip counts were always 0
and the ``elif report.skipped`` branch was reachable only for an imperative
``pytest.skip()`` called inside a test body.

These tests drive the hook with the report shapes pytest really emits.
"""
from __future__ import annotations

import asyncio

import pytest

from testlookup_reporter import _TestLookupPytestPlugin


class _FakeLive:
    """Stands in for LiveSession, capturing what the plugin streams."""

    def __init__(self) -> None:
        self.session_id = "fake-session"
        self.records: list[dict] = []

    async def record(self, test_name, status, duration_ms=0, **kw):
        self.records.append(
            {
                "test_name": test_name,
                "status": status,
                "duration_ms": duration_ms,
                "suite_name": kw.get("suite_name"),
                "error": kw.get("error"),
                "stack_trace": kw.get("stack_trace"),
            }
        )


class _Report:
    """The subset of a pytest TestReport the hook actually reads."""

    def __init__(
        self,
        when,
        nodeid="tests/test_thing.py::test_widget",
        passed=False,
        failed=False,
        skipped=False,
        longrepr=None,
        duration=0.25,
    ):
        self.when = when
        self.nodeid = nodeid
        self.passed = passed
        self.failed = failed
        self.skipped = skipped
        self.longrepr = longrepr
        self.duration = duration


@pytest.fixture()
def plugin():
    p = _TestLookupPytestPlugin(
        base_url="http://localhost:8000", token="t", project_id="p"
    )
    p._live = _FakeLive()
    p._loop = asyncio.new_event_loop()
    yield p
    p._loop.close()


def _passing_setup(nodeid="tests/test_thing.py::test_widget"):
    return _Report("setup", nodeid=nodeid, passed=True, duration=0.0)


# ── The regression: a fixture error must not vanish ─────────────────────────

def test_a_fixture_error_is_reported_as_broken(plugin):
    """No call report is ever emitted for this test -- setup is all there is."""
    plugin.pytest_runtest_logreport(
        _Report(
            "setup",
            failed=True,
            longrepr="conftest.py:12: in db\n    raise RuntimeError\nRuntimeError: no DB",
        )
    )
    # pytest then goes straight to teardown; no call phase happens.
    plugin.pytest_runtest_logreport(_Report("teardown", passed=True))

    assert len(plugin._live.records) == 1, (
        "a fixture error produced no result at all -- the run would read as "
        "'0 tests, nothing wrong'"
    )
    rec = plugin._live.records[0]
    assert rec["status"] == "BROKEN"
    assert rec["test_name"] == "test_widget"
    assert rec["suite_name"] == "tests/test_thing.py"
    assert rec["error"] == "RuntimeError: no DB"
    assert "conftest.py" in rec["stack_trace"]


def test_every_test_in_a_broken_session_is_reported(plugin):
    """The measured shape: 50 collected tests, all erroring in one fixture."""
    for i in range(50):
        plugin.pytest_runtest_logreport(
            _Report("setup", nodeid=f"tests/test_m.py::test_{i}", failed=True,
                    longrepr="RuntimeError: no DB")
        )
    assert len(plugin._live.records) == 50
    assert {r["status"] for r in plugin._live.records} == {"BROKEN"}


def test_a_marked_skip_is_reported(plugin):
    """``@pytest.mark.skip`` resolves in setup, so it never reached the hook."""
    plugin.pytest_runtest_logreport(
        _Report(
            "setup",
            skipped=True,
            longrepr=("tests/test_thing.py", 4, "Skipped: needs a live Redis"),
        )
    )
    assert len(plugin._live.records) == 1
    rec = plugin._live.records[0]
    assert rec["status"] == "SKIPPED"
    assert rec["error"] == "Skipped: needs a live Redis"


# ── The call phase keeps working exactly as before ──────────────────────────

def test_a_passing_test_is_recorded_once(plugin):
    plugin.pytest_runtest_logreport(_passing_setup())
    plugin.pytest_runtest_logreport(_Report("call", passed=True))
    plugin.pytest_runtest_logreport(_Report("teardown", passed=True))

    assert len(plugin._live.records) == 1, "a passing setup must not add a record"
    assert plugin._live.records[0]["status"] == "PASSED"
    assert plugin._live.records[0]["duration_ms"] == 250


def test_a_failing_test_keeps_its_error_and_stack(plugin):
    plugin.pytest_runtest_logreport(_passing_setup())
    plugin.pytest_runtest_logreport(
        _Report("call", failed=True, longrepr="tests/x.py:9: in test\nAssertionError: nope")
    )
    rec = plugin._live.records[0]
    assert rec["status"] == "FAILED"
    assert rec["error"] == "AssertionError: nope"
    assert "tests/x.py" in rec["stack_trace"]


def test_an_imperative_skip_still_works(plugin):
    """``pytest.skip()`` mid-test produces a skipped *call* report."""
    plugin.pytest_runtest_logreport(_passing_setup())
    plugin.pytest_runtest_logreport(_Report("call", skipped=True))
    assert plugin._live.records[0]["status"] == "SKIPPED"


# ── Teardown failures are reported without clobbering the result ────────────

def test_a_teardown_failure_is_reported_alongside_the_pass(plugin):
    """pytest calls this "1 passed, 1 error"; both facts are true."""
    plugin.pytest_runtest_logreport(_passing_setup())
    plugin.pytest_runtest_logreport(_Report("call", passed=True))
    plugin.pytest_runtest_logreport(
        _Report("teardown", failed=True, longrepr="OSError: could not close pool")
    )

    assert len(plugin._live.records) == 2
    passed, broken = plugin._live.records
    assert passed["status"] == "PASSED"
    assert passed["test_name"] == "test_widget"
    assert broken["status"] == "BROKEN"
    assert broken["test_name"] == "test_widget (teardown)", (
        "the cleanup failure must not overwrite the test's own verdict"
    )


def test_a_clean_teardown_adds_nothing(plugin):
    plugin.pytest_runtest_logreport(_passing_setup())
    plugin.pytest_runtest_logreport(_Report("call", passed=True))
    plugin.pytest_runtest_logreport(_Report("teardown", passed=True))
    assert len(plugin._live.records) == 1


# ── Guards on the hook itself ───────────────────────────────────────────────

def test_the_same_test_is_never_recorded_twice(plugin):
    plugin.pytest_runtest_logreport(_passing_setup())
    plugin.pytest_runtest_logreport(_Report("call", passed=True))
    plugin.pytest_runtest_logreport(_Report("call", passed=True))
    assert len(plugin._live.records) == 1


def test_the_hook_is_inert_without_a_session():
    """Never raise inside a pytest hook -- it would break the user's run."""
    p = _TestLookupPytestPlugin(base_url="http://x", token="t", project_id="p")
    p.pytest_runtest_logreport(_Report("setup", failed=True))
    p.pytest_runtest_logreport(_Report("call", passed=True))  # no exception


def test_distinct_tests_are_recorded_separately(plugin):
    for name in ("test_a", "test_b"):
        nodeid = f"tests/test_m.py::{name}"
        plugin.pytest_runtest_logreport(_passing_setup(nodeid))
        plugin.pytest_runtest_logreport(_Report("call", nodeid=nodeid, passed=True))
    assert [r["test_name"] for r in plugin._live.records] == ["test_a", "test_b"]
