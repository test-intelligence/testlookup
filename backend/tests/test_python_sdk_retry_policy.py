"""Tests for the Python SDK's retry-policy alignment (Phase 4.6).

The SDK must:

  1. Retry on HTTP 429 + 503 (server-side back-pressure), in addition to
     transport errors.
  2. Honour ``Retry-After`` exactly when the server sends one — no
     doubling, no halving. Both integer (delta-seconds) and HTTP-date
     forms count.
  3. Cap cumulative retry time at ``MAX_RETRY_TOTAL_SECONDS`` (5 min)
     so a misconfigured project surfaces a hard failure instead of
     silently buffering forever.

We test ``_compute_next_retry_delay`` and ``_parse_retry_after``
directly because they encode the full decision and don't need an HTTP
stack to verify. End-to-end behaviour is exercised by patching
``self._http.post`` in the ``LiveStream._post_batch`` integration tests.

The SDK lives at ``client/testlookup_reporter.py``; we add its directory
to ``sys.path`` so the test runs without a separate install step.
"""
from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest


_CLIENT_DIR = Path(__file__).resolve().parents[2] / "client"
if str(_CLIENT_DIR) not in sys.path:
    sys.path.insert(0, str(_CLIENT_DIR))


# ── Pure helpers ──────────────────────────────────────────────────────────────


def test_parse_retry_after_integer_seconds():
    import testlookup_reporter as tr
    assert tr._parse_retry_after("30") == 30.0
    assert tr._parse_retry_after("0") == 0.0
    assert tr._parse_retry_after("  5  ") == 5.0


def test_parse_retry_after_handles_missing_and_blank():
    import testlookup_reporter as tr
    assert tr._parse_retry_after(None) is None
    assert tr._parse_retry_after("") is None
    assert tr._parse_retry_after("   ") is None


def test_parse_retry_after_http_date_form():
    """RFC 9110 allows an HTTP-date in Retry-After — we accept it so a
    server using ``Retry-After: <date>`` is still honoured."""
    import testlookup_reporter as tr
    future = datetime.now(timezone.utc) + timedelta(seconds=10)
    header = format_datetime(future, usegmt=True)
    parsed = tr._parse_retry_after(header)
    assert parsed is not None
    # Allow some slack for parsing and clock skew (under 1s).
    assert 9.0 <= parsed <= 11.0


def test_parse_retry_after_unparseable_returns_none():
    import testlookup_reporter as tr
    assert tr._parse_retry_after("not-a-date") is None


def test_compute_next_delay_honours_retry_after_exactly():
    """Retry-After: 30 → exactly 30s. No doubling, no halving."""
    import testlookup_reporter as tr
    delay = tr._compute_next_retry_delay(
        retry_after_header="30",
        attempt=1,
        total_elapsed_s=0.0,
    )
    assert delay == 30.0


def test_compute_next_delay_falls_back_to_exponential_without_header():
    import testlookup_reporter as tr
    base = tr.RETRY_BASE_DELAY
    assert tr._compute_next_retry_delay(
        retry_after_header=None, attempt=1, total_elapsed_s=0.0
    ) == base * 1
    assert tr._compute_next_retry_delay(
        retry_after_header=None, attempt=2, total_elapsed_s=0.0
    ) == base * 2
    assert tr._compute_next_retry_delay(
        retry_after_header=None, attempt=3, total_elapsed_s=0.0
    ) == base * 4


def test_compute_next_delay_returns_none_when_cap_exceeded():
    """If we've already burned the 5-min budget, no more retries."""
    import testlookup_reporter as tr
    assert tr._compute_next_retry_delay(
        retry_after_header="30",
        attempt=2,
        total_elapsed_s=tr.MAX_RETRY_TOTAL_SECONDS + 0.1,
    ) is None


def test_compute_next_delay_returns_none_when_delay_would_overflow_cap():
    """Retry-After: 600 with 0s elapsed against a 300s cap → don't wait,
    surface the hard failure. The server is asking for longer than we'll
    tolerate."""
    import testlookup_reporter as tr
    assert tr._compute_next_retry_delay(
        retry_after_header="600",
        attempt=1,
        total_elapsed_s=0.0,
    ) is None


# ── LiveStream._post_batch integration ─────────────────────────────────────────


def _build_stream():
    import testlookup_reporter as tr
    stream = tr.LiveStream(
        api_key="tlk_test",
        run_id="run-test",
        base_url="http://test.invalid",
    )
    return stream, tr


def _fake_response(status_code: int, *, retry_after: str | None = None, body: dict | None = None) -> MagicMock:
    """Build a minimal stand-in for an httpx.Response. We only touch the
    attributes the SDK reads (status_code, headers.get, raise_for_status,
    json, text), so a real Response isn't necessary."""
    resp = MagicMock()
    resp.status_code = status_code
    headers = {}
    if retry_after is not None:
        headers["Retry-After"] = retry_after
    resp.headers = headers
    resp.text = ""
    resp.json = MagicMock(return_value=body or {"accepted": 1})
    if 200 <= status_code < 300:
        resp.raise_for_status = MagicMock()
    else:
        resp.raise_for_status = MagicMock(
            side_effect=httpx.HTTPStatusError("err", request=MagicMock(), response=resp)
        )
    return resp


@pytest.mark.asyncio
async def test_livestream_retries_on_503_then_succeeds(monkeypatch):
    """A 503 with Retry-After=0 retries and then a 200 on the second
    attempt is counted as success — not as a 503-induced loss."""
    stream, tr = _build_stream()
    monkeypatch.setattr(asyncio, "sleep", AsyncMock())  # don't really sleep

    calls = []

    async def fake_post(path, json):
        calls.append(path)
        if len(calls) == 1:
            return _fake_response(503, retry_after="0")
        return _fake_response(200, body={"accepted": 3, "session_id": "sess-x"})

    stream._http.post = fake_post  # type: ignore[assignment]
    try:
        await stream._post_batch([{"event_type": "test_result"}] * 3)
        assert stream._stats["sent"] == 3
        assert stream._stats["failed"] == 0
        assert len(calls) == 2
    finally:
        await stream._http.aclose()


@pytest.mark.asyncio
async def test_livestream_retries_on_429_then_succeeds(monkeypatch):
    """A 429 with Retry-After=0 retries and then a 200 on the second
    attempt is counted as success."""
    stream, tr = _build_stream()
    monkeypatch.setattr(asyncio, "sleep", AsyncMock())

    calls = []

    async def fake_post(path, json):
        calls.append(path)
        if len(calls) == 1:
            return _fake_response(429, retry_after="0")
        return _fake_response(200, body={"accepted": 2})

    stream._http.post = fake_post  # type: ignore[assignment]
    try:
        await stream._post_batch([{"event_type": "test_result"}] * 2)
        assert stream._stats["sent"] == 2
        assert stream._stats["failed"] == 0
        assert len(calls) == 2
    finally:
        await stream._http.aclose()


@pytest.mark.asyncio
async def test_livestream_honours_retry_after_exactly(monkeypatch):
    """Retry-After: 7 → next ``asyncio.sleep`` is called with exactly 7,
    not the exponential 0.5 / 1.0 / 2.0 schedule."""
    stream, tr = _build_stream()
    sleep_mock = AsyncMock()
    monkeypatch.setattr(asyncio, "sleep", sleep_mock)

    responses = [
        _fake_response(429, retry_after="7"),
        _fake_response(200),
    ]

    async def fake_post(path, json):
        return responses.pop(0)

    stream._http.post = fake_post  # type: ignore[assignment]
    try:
        await stream._post_batch([{"event_type": "test_result"}])
        # First sleep argument must be the exact header value (7.0s) —
        # not the default exponential 0.5s.
        assert sleep_mock.await_args_list[0].args[0] == 7.0
    finally:
        await stream._http.aclose()


@pytest.mark.asyncio
async def test_livestream_gives_up_when_retry_after_overflows_cap(monkeypatch):
    """Retry-After: 9999 with 0s elapsed > 300s cap → don't sleep, mark
    the batch as failed and surface immediately."""
    stream, tr = _build_stream()
    sleep_mock = AsyncMock()
    monkeypatch.setattr(asyncio, "sleep", sleep_mock)

    async def fake_post(path, json):
        return _fake_response(503, retry_after="9999")

    stream._http.post = fake_post  # type: ignore[assignment]
    try:
        await stream._post_batch([{"event_type": "test_result"}] * 5)
        # Hard failure — 5 events marked failed, no sleeps queued.
        assert stream._stats["sent"] == 0
        assert stream._stats["failed"] == 5
        assert sleep_mock.await_count == 0
    finally:
        await stream._http.aclose()
