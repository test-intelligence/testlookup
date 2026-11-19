"""The live batch POST gives up, stops and waits exactly as documented (GAP-E06).

``test_live_protocol_ids.py`` covers "one 503, then 200, keeps the batch id".
The limits around it never ran: the retry cap, a 4xx that must not be
retried, a 401 that stops at once, a 429 (no test had one), transport errors,
and an HTTP-date ``Retry-After``. Both batch paths share the contract
(``LiveSession._post_batch`` for JWT sessions, ``LiveStream._post_batch`` for
API-key ingest), so every case runs against both, through a real
``httpx.AsyncClient`` on a ``MockTransport``, with ``asyncio.sleep`` recorded
instead of awaited.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime

import httpx
import pytest

import testlookup_reporter as tr

EVENTS = [
    {"event_type": "test_result", "test_name": f"test_{i}", "status": "PASSED"}
    for i in range(3)
]
PATHS = {"session": "/api/v1/stream/events/batch", "stream": "/api/v1/stream/ingest"}


def _make(kind: str, responses: list):
    """A LiveSession or LiveStream whose HTTP client answers ``responses`` in order.

    Each item is a status code, a ``(status, headers)`` pair, or an exception
    instance to raise as a transport error. The last item repeats.
    """
    posts: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        posts.append(request)
        item = responses[min(len(posts), len(responses)) - 1]
        if isinstance(item, Exception):
            raise item
        status, headers = item if isinstance(item, tuple) else (item, {})
        body = (
            {"accepted": len(EVENTS), "session_id": "session-1"}
            if status in (200, 202)
            else {"detail": "x"}
        )
        return httpx.Response(status, json=body, headers=headers)

    http = httpx.AsyncClient(
        base_url="http://testlookup.example.test",
        transport=httpx.MockTransport(handler),
    )
    if kind == "session":
        obj = object.__new__(tr.LiveSession)
        obj.session_id = "session-1"
        obj.session_token = "ci-session-token-test-only"
        obj.run_id = "run-1"
    else:
        obj = object.__new__(tr.LiveStream)
        obj._run_id = "ci-build-1"
        obj._meta = {}
        obj._session_id = None
    obj._http = http
    obj._stats = {"sent": 0, "failed": 0}
    return obj, posts


@pytest.fixture
def sleeps(monkeypatch):
    recorded: list[float] = []

    async def _sleep(seconds):
        recorded.append(seconds)

    monkeypatch.setattr(tr.asyncio, "sleep", _sleep)
    return recorded


def _post(obj) -> None:
    async def _go():
        try:
            await obj._post_batch(list(EVENTS))
        finally:
            await obj._http.aclose()

    asyncio.run(_go())


KINDS = pytest.mark.parametrize("kind", ["session", "stream"])


@KINDS
def test_a_sustained_503_stops_after_max_retries_and_counts_every_event_failed(
    kind, sleeps
):
    obj, posts = _make(kind, [503])

    _post(obj)

    assert tr.MAX_RETRIES == 5
    assert len(posts) == tr.MAX_RETRIES, (
        f"expected exactly {tr.MAX_RETRIES} POSTs, got {len(posts)}"
    )
    assert obj._stats == {"sent": 0, "failed": len(EVENTS)}, (
        "a batch that gave up must count every event failed"
    )
    assert sleeps == [0.5, 1.0, 2.0, 4.0], (
        "exponential backoff between attempts, none after the last"
    )
    assert all(r.url.path == PATHS[kind] for r in posts)


@KINDS
def test_every_retry_resends_the_same_batch(kind, sleeps):
    obj, posts = _make(kind, [503])

    _post(obj)

    bodies = {r.content for r in posts}
    assert len(bodies) == 1, (
        "a retry must resend the identical batch (same batch_id, same events)"
    )


@KINDS
def test_a_400_is_not_retried(kind, sleeps):
    obj, posts = _make(kind, [400])

    _post(obj)

    assert len(posts) == 1
    assert obj._stats == {"sent": 0, "failed": len(EVENTS)}
    assert sleeps == []


AUTH_LOG = {
    "session": "Session token rejected",
    "stream": "LiveStream auth failed (401)",
}


@KINDS
def test_a_401_stops_at_once_and_says_the_credential_was_refused(kind, sleeps, caplog):
    obj, posts = _make(kind, [401, 202])

    with caplog.at_level("ERROR", logger="testlookup_reporter"):
        _post(obj)

    assert len(posts) == 1, "a rejected credential must not be retried"
    assert obj._stats == {"sent": 0, "failed": len(EVENTS)}
    assert sleeps == []
    # Without the 401 branch the generic error path would drop the batch too, but
    # the operator would read "Unexpected batch POST error" instead of the cause.
    assert any(AUTH_LOG[kind] in r.getMessage() for r in caplog.records), (
        f"a 401 must be logged as a refused credential ({AUTH_LOG[kind]!r}); got "
        f"{[r.getMessage() for r in caplog.records]}"
    )


@KINDS
def test_a_429_is_retried_then_succeeds(kind, sleeps):
    obj, posts = _make(kind, [429, 202])

    _post(obj)

    assert len(posts) == 2
    assert obj._stats == {"sent": len(EVENTS), "failed": 0}
    assert sleeps == [0.5]


@KINDS
def test_retry_after_seconds_is_honoured_exactly(kind, sleeps):
    obj, posts = _make(kind, [(503, {"Retry-After": "7"}), 202])

    _post(obj)

    assert sleeps == [7.0]
    assert obj._stats["sent"] == len(EVENTS)


@KINDS
def test_an_http_date_retry_after_is_honoured(kind, sleeps):
    # RFC 9110 allows an HTTP-date. The header names a moment ~120 s ahead; the
    # SDK must wait for it, not fall back to its 0.5 s first backoff step.
    when = datetime.now(timezone.utc) + timedelta(seconds=120)
    obj, posts = _make(
        kind, [(429, {"Retry-After": format_datetime(when, usegmt=True)}), 202]
    )

    _post(obj)

    assert len(sleeps) == 1
    assert 100.0 < sleeps[0] <= 120.0, (
        f"slept {sleeps[0]!r}s, want about the 120 s the HTTP-date names"
    )
    assert obj._stats == {"sent": len(EVENTS), "failed": 0}


@KINDS
def test_a_retry_after_past_the_total_cap_gives_up_at_once(kind, sleeps):
    obj, posts = _make(
        kind, [(503, {"Retry-After": str(int(tr.MAX_RETRY_TOTAL_SECONDS) + 1)}), 202]
    )

    _post(obj)

    assert len(posts) == 1
    assert sleeps == []
    assert obj._stats == {"sent": 0, "failed": len(EVENTS)}


@KINDS
def test_transport_errors_are_retried_then_the_batch_is_sent(kind, sleeps):
    refused = httpx.ConnectError("connection refused")
    obj, posts = _make(kind, [refused, refused, 202])

    _post(obj)

    assert len(posts) == 3
    assert obj._stats == {"sent": len(EVENTS), "failed": 0}
    assert sleeps == [0.5, 1.0]


@KINDS
def test_sustained_transport_errors_stop_after_max_retries(kind, sleeps):
    obj, posts = _make(kind, [httpx.ReadTimeout("read timed out")])

    _post(obj)

    assert len(posts) == tr.MAX_RETRIES
    assert obj._stats == {"sent": 0, "failed": len(EVENTS)}
