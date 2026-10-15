"""EXEC-01: the live pytest plugin sends each test's phase split and start, not one opaque duration.

pytest reports every test three times (setup, call, teardown). The plugin used to forward only the deciding
phase's duration, so a fixture-heavy test looked fast and nothing said when it began. The result event now carries
``setup_ms`` / ``call_ms`` (and ``teardown_ms`` on the separate teardown-failure entry), ``started_at_ms`` from the
setup report's own start where pytest stamps one, and the xdist worker. ``timestamp_ms`` stays the finish.
"""
from __future__ import annotations

import asyncio

import pytest
import testlookup_reporter as tr
from testlookup_reporter import _TestLookupPytestPlugin

SETUP_START = 1_791_972_001.25  # epoch seconds, as pytest stamps ``report.start``


class _FakeLive:
    def __init__(self) -> None:
        self.session_id = "fake-session"
        self.records: list[dict] = []

    async def record(self, test_name, status, duration_ms=0, **kw):
        self.records.append({"test_name": test_name, "status": status, "duration_ms": duration_ms, **kw})


class _Report:
    def __init__(self, when, *, passed=False, failed=False, skipped=False, longrepr=None, duration=0.0,
                 start=None, nodeid="tests/test_shop.py::test_checkout"):
        self.when = when
        self.nodeid = nodeid
        self.passed = passed
        self.failed = failed
        self.skipped = skipped
        self.longrepr = longrepr
        self.duration = duration
        if start is not None:  # older pytest has no ``start`` attribute at all
            self.start = start


@pytest.fixture()
def plugin(monkeypatch):
    monkeypatch.delenv("PYTEST_XDIST_WORKER", raising=False)
    p = _TestLookupPytestPlugin(base_url="http://localhost:8000", token="t", project_id="p")
    p._live = _FakeLive()
    p._loop = asyncio.new_event_loop()
    yield p
    p._loop.close()


def test_a_passing_test_sends_its_setup_call_and_start(plugin):
    plugin.pytest_runtest_logreport(_Report("setup", passed=True, duration=0.12, start=SETUP_START))
    plugin.pytest_runtest_logreport(_Report("call", passed=True, duration=1.6, start=SETUP_START + 0.12))
    plugin.pytest_runtest_logreport(_Report("teardown", passed=True, duration=0.08, start=SETUP_START + 1.72))

    (rec,) = plugin._live.records
    assert rec["status"] == "PASSED"
    assert rec["duration_ms"] == 1600  # unchanged: the call phase
    assert rec["setup_ms"] == 120
    assert rec["call_ms"] == 1600
    assert rec["started_at_ms"] == 1_791_972_001_250  # the SETUP start: the test begins with its fixtures
    assert "teardown_ms" not in rec  # the result is streamed before teardown runs
    assert "worker_id" not in rec


def test_without_a_report_start_no_start_is_sent(plugin):
    plugin.pytest_runtest_logreport(_Report("setup", passed=True, duration=0.01))
    plugin.pytest_runtest_logreport(_Report("call", passed=True, duration=0.5))

    (rec,) = plugin._live.records
    assert "started_at_ms" not in rec
    assert (rec["setup_ms"], rec["call_ms"]) == (10, 500)


def test_a_fixture_error_sends_the_setup_time_and_no_call(plugin):
    plugin.pytest_runtest_logreport(
        _Report("setup", failed=True, duration=0.75, start=SETUP_START, longrepr="RuntimeError: no DB")
    )
    plugin.pytest_runtest_logreport(_Report("teardown", passed=True, duration=0.0))

    (rec,) = plugin._live.records
    assert rec["status"] == "BROKEN"
    assert rec["setup_ms"] == 750
    assert rec["started_at_ms"] == 1_791_972_001_250
    assert "call_ms" not in rec


def test_a_teardown_failure_entry_carries_the_teardown_time(plugin):
    plugin.pytest_runtest_logreport(_Report("setup", passed=True, duration=0.1, start=SETUP_START))
    plugin.pytest_runtest_logreport(_Report("call", passed=True, duration=1.0))
    plugin.pytest_runtest_logreport(
        _Report("teardown", failed=True, duration=0.3, start=SETUP_START + 1.1, longrepr="OSError: pool")
    )

    passed, broken = plugin._live.records
    assert passed["call_ms"] == 1000 and "teardown_ms" not in passed
    assert broken["test_name"] == "test_checkout (teardown)"
    assert broken["teardown_ms"] == 300
    assert broken["started_at_ms"] == 1_791_972_002_350
    assert "setup_ms" not in broken and "call_ms" not in broken


def test_the_setup_of_one_test_never_leaks_into_another(plugin):
    plugin.pytest_runtest_logreport(
        _Report("setup", passed=True, duration=0.2, start=SETUP_START, nodeid="tests/a.py::test_a")
    )
    plugin.pytest_runtest_logreport(_Report("call", passed=True, duration=0.1, nodeid="tests/a.py::test_a"))
    plugin.pytest_runtest_logreport(_Report("teardown", passed=True, nodeid="tests/a.py::test_a"))
    plugin.pytest_runtest_logreport(_Report("call", passed=True, duration=0.4, nodeid="tests/b.py::test_b"))

    a, b = plugin._live.records
    assert a["setup_ms"] == 200
    assert "setup_ms" not in b and "started_at_ms" not in b
    assert plugin._setup == {}  # a finished test's setup is not kept


def test_an_xdist_worker_is_sent_as_the_worker(plugin, monkeypatch):
    monkeypatch.setenv("PYTEST_XDIST_WORKER", "gw3")
    plugin.pytest_runtest_logreport(_Report("call", passed=True, duration=0.1))
    assert plugin._live.records[0]["worker_id"] == "gw3"


def test_the_start_is_read_from_a_real_pytest_report():
    """The attribute name is pytest's own: build a real TestReport rather than trusting the fake."""
    report = pytest.TestReport(
        nodeid="tests/test_x.py::test_y", location=("tests/test_x.py", 1, "test_y"), keywords={},
        outcome="passed", longrepr=None, when="setup", duration=0.01, start=SETUP_START, stop=SETUP_START + 0.01,
    )
    assert tr._report_start_ms(report) == 1_791_972_001_250


# ── the wire: record() puts the keys on the event only when given ────────────


def _bare_session() -> tr.LiveSession:
    session = object.__new__(tr.LiveSession)
    session._queue = asyncio.Queue()
    session._batch_size = 1_000
    session._default_suite_name = None
    return session


def _bare_stream() -> tr.LiveStream:
    stream = object.__new__(tr.LiveStream)
    stream._queue = asyncio.Queue()
    stream._batch_size = 1_000
    stream._default_suite_name = None
    stream._last_real_enqueue_ms = 0.0
    return stream


@pytest.mark.parametrize("make", [_bare_session, _bare_stream])
def test_record_sends_the_timing_keys_it_is_given(make):
    target = make()
    asyncio.run(target.record(
        "test_checkout", "passed", 1600,
        started_at_ms=1_791_972_001_250, setup_ms=120, call_ms=1600, teardown_ms=80, worker_id="gw1",
    ))
    event = target._queue.get_nowait()
    assert event["started_at_ms"] == 1_791_972_001_250
    assert (event["setup_ms"], event["call_ms"], event["teardown_ms"]) == (120, 1600, 80)
    assert event["worker_id"] == "gw1"
    assert isinstance(event["timestamp_ms"], int)  # still the finish


@pytest.mark.parametrize("make", [_bare_session, _bare_stream])
def test_record_without_timing_sends_the_event_it_always_did(make):
    target = make()
    asyncio.run(target.record("test_checkout", "passed", 5))
    event = target._queue.get_nowait()
    assert set(event) == {"event_type", "test_name", "status", "duration_ms", "timestamp_ms"}
