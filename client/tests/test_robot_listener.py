"""W18 DEV-03: the Robot Framework listener streams one result per test, then closes the session.

Driven with the attribute shapes Robot Framework 7 (``elapsed_time``,
``start_time``) and 5/6 (``elapsedtime``, ``starttime``) hand a v3 listener,
against the fake server behind every httpx client (``wire``). A server that is
down never raises into the Robot run; without configuration the listener is off.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace

import httpx

from testlookup_robot_listener import TestLookupListener


def _suite():
    return SimpleNamespace(name="Login", longname="Acceptance.Login", full_name="Acceptance.Login")


def _test(name, parent):
    return SimpleNamespace(name=name, parent=parent, tags=["smoke"])


def _rf7(status, ms, message=""):
    return SimpleNamespace(status=status, elapsed_time=timedelta(milliseconds=ms), start_time=datetime(2026, 10, 26, 10, 0, 0),
                           message=message, tags=["smoke"])


def _rf5(status, ms, message=""):
    return SimpleNamespace(status=status, elapsedtime=ms, starttime="20261026 10:00:01.500", message=message, tags=[])


def _run(listener):
    suite = _suite()
    listener.start_suite(suite, SimpleNamespace())
    listener.end_test(_test("Valid login", suite), _rf7("PASS", 1200))
    listener.end_test(_test("Wrong password", suite), _rf7("FAIL", 800, "Expected 'Welcome' but got 'Error'\nmore"))
    listener.end_test(_test("Locked account", suite), _rf5("SKIP", 0, "Skipped: not on CI"))
    listener.end_test(_test("Not run", suite), _rf5("NOT RUN", 0))
    listener.close()


def test_each_test_is_one_result_and_the_session_closes(wire):
    _run(TestLookupListener(url="http://testlookup.invalid", project="p", token="jwt"))
    batches = wire.bodies("POST", "/api/v1/stream/events/batch")
    results = [e for b in batches for e in b["events"] if e["event_type"] == "test_result"]
    assert [(r["test_name"], r["status"]) for r in results] == [
        ("Valid login", "PASSED"), ("Wrong password", "FAILED"), ("Locked account", "SKIPPED"), ("Not run", "SKIPPED"),
    ]
    assert results[0]["duration_ms"] == 1200 and results[0]["suite_name"] == "Acceptance.Login"
    assert results[1]["error_message"] == "Expected 'Welcome' but got 'Error'"
    assert results[0].get("started_at_ms") and results[2].get("started_at_ms")
    assert wire.first("DELETE", "/api/v1/stream/sessions/") is not None


def test_a_server_that_is_down_never_fails_the_run(monkeypatch):
    def refuse(request):
        raise httpx.ConnectError("refused", request=request)

    real = httpx.AsyncClient

    class Refusing(real):  # type: ignore[misc, valid-type]
        def __init__(self, *a, **k):
            k["transport"] = httpx.MockTransport(refuse)
            super().__init__(*a, **k)

    monkeypatch.setattr(httpx, "AsyncClient", Refusing)
    _run(TestLookupListener(url="http://testlookup.invalid", project="p", token="jwt"))


def test_without_configuration_the_listener_is_off(wire):
    listener = TestLookupListener()
    assert listener.enabled is False
    _run(listener)
    assert wire.bodies("POST", "/api/v1/stream/sessions") == []
