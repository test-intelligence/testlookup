"""A TestLookup server that is down or refusing must not stop a pytest run.

The plugin reports results; it is not part of the tests. So:

* when the live session cannot be created at session start (connection
  refused, 401, 5xx), the plugin logs ONE warning, switches live reporting off
  for the run, and every later hook is a no-op. pytest must not see an
  exception: an exception in ``pytest_sessionstart`` is an INTERNALERROR and
  no test runs;
* during the run, a server answering 429 or 503 with a long ``Retry-After``
  may hold the test process for at most ``PYTEST_MAX_RETRY_TOTAL_SECONDS`` per
  batch (the general SDK budget is ``MAX_RETRY_TOTAL_SECONDS``, five minutes).
  After that the batch counts as failed and the tests go on.
"""
from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import httpx
import pytest

import testlookup_reporter as tr

BASE_URL = "http://testlookup.example.test"
SESSION = {
    "session_id": "session-1",
    "session_token": "ci-session-token-test-only",
    "run_id": "run-1",
}


def _plugin(handler) -> tr._TestLookupPytestPlugin:
    plugin = tr._TestLookupPytestPlugin(
        base_url=BASE_URL, api_key="qai_test_only_not_a_real_key", project_id="p"
    )
    plugin._reporter._http = httpx.AsyncClient(
        base_url=BASE_URL, transport=httpx.MockTransport(handler)
    )
    return plugin


def _refuse_connection(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("connection refused", request=request)


def _answer(status: int):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json={"detail": "x"})

    return handler


class _Report:
    when = "call"
    nodeid = "tests/test_thing.py::test_widget"
    passed = True
    failed = False
    skipped = False
    longrepr = None
    duration = 0.01


@pytest.mark.parametrize(
    "handler",
    [_refuse_connection, _answer(401), _answer(503), _answer(500)],
    ids=["connection refused", "401", "503", "500"],
)
def test_a_session_that_cannot_be_created_switches_reporting_off(handler):
    plugin = _plugin(handler)
    recorder = MagicMock()

    with patch.object(tr, "logger", recorder):
        plugin.pytest_sessionstart(session=None)  # must not raise

        assert plugin._live is None
        # The rest of the run goes on without the plugin.
        plugin.pytest_runtest_setup(item=MagicMock(nodeid=_Report.nodeid))
        plugin.pytest_runtest_logreport(_Report())
        plugin.pytest_sessionfinish(session=None, exitstatus=0)

    assert recorder.warning.call_count == 1, recorder.warning.call_args_list
    message = recorder.warning.call_args.args[0] % recorder.warning.call_args.args[1:]
    assert "live reporting is off" in message
    assert "tests run as normal" in message
    assert recorder.error.call_count == 0


def test_the_warning_does_not_carry_the_credential():
    plugin = _plugin(_answer(401))
    recorder = MagicMock()

    with patch.object(tr, "logger", recorder):
        plugin.pytest_sessionstart(session=None)

    message = recorder.warning.call_args.args[0] % recorder.warning.call_args.args[1:]
    assert "qai_test_only_not_a_real_key" not in message


def test_a_working_server_still_gets_a_live_session():
    """Positive case: the session is created as before, with the pytest retry budget."""
    posts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        posts.append(request.url.path)
        return httpx.Response(200, json=SESSION)

    plugin = _plugin(handler)
    recorder = MagicMock()

    with patch.object(tr, "logger", recorder):
        plugin.pytest_sessionstart(session=None)

    try:
        assert plugin._live is not None
        assert plugin._live.session_id == "session-1"
        assert posts == ["/api/v1/stream/sessions"]
        assert recorder.warning.call_count == 0
        assert plugin._live.retry_cap_seconds == tr.PYTEST_MAX_RETRY_TOTAL_SECONDS
    finally:
        plugin._loop.run_until_complete(plugin._live._shutdown())
        plugin._loop.run_until_complete(plugin._reporter.aclose())
        plugin._loop.close()


# ── the retry budget on the pytest path ──────────────────────────────────

EVENTS = [{"event_type": "test_result", "test_name": "test_a", "status": "PASSED"}]


def _live_session(cap: float | None, posts: list, sleeps: list, monkeypatch) -> tr.LiveSession:
    def handler(request: httpx.Request) -> httpx.Response:
        posts.append(request)
        return httpx.Response(503, json={"detail": "busy"}, headers={"Retry-After": "120"})

    async def _sleep(seconds):
        sleeps.append(seconds)

    monkeypatch.setattr(tr.asyncio, "sleep", _sleep)
    live = object.__new__(tr.LiveSession)
    live.session_id = "session-1"
    live.session_token = "ci-session-token-test-only"
    live.run_id = "run-1"
    live._http = httpx.AsyncClient(base_url=BASE_URL, transport=httpx.MockTransport(handler))
    live._stats = {"sent": 0, "failed": 0}
    if cap is not None:
        live.retry_cap_seconds = cap
    return live


def test_the_pytest_budget_is_far_below_the_general_one():
    assert tr.PYTEST_MAX_RETRY_TOTAL_SECONDS <= 30.0
    assert tr.PYTEST_MAX_RETRY_TOTAL_SECONDS < tr.MAX_RETRY_TOTAL_SECONDS


def test_a_long_retry_after_is_not_waited_out_inside_a_pytest_run(monkeypatch):
    posts: list = []
    sleeps: list = []
    live = _live_session(tr.PYTEST_MAX_RETRY_TOTAL_SECONDS, posts, sleeps, monkeypatch)

    asyncio.run(live._post_batch(list(EVENTS)))

    assert sleeps == [], "a 120 s Retry-After must not block the test process"
    assert len(posts) == 1
    assert live._stats == {"sent": 0, "failed": 1}


def test_outside_pytest_the_general_budget_still_honours_retry_after(monkeypatch):
    """Positive case: a session without the pytest cap waits as the server asks."""
    posts: list = []
    sleeps: list = []
    live = _live_session(None, posts, sleeps, monkeypatch)

    asyncio.run(live._post_batch(list(EVENTS)))

    assert sleeps and sleeps[0] == 120.0
    assert live.retry_cap_seconds == tr.MAX_RETRY_TOTAL_SECONDS
