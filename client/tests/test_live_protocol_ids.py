"""Replay-safe identity contract for the Python live SDK paths."""

from __future__ import annotations

import asyncio
import copy
import uuid
from unittest.mock import AsyncMock

import testlookup_reporter as tr


class _Response:
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code
        self.headers = {"Retry-After": "0"} if status_code == 503 else {}
        self.text = ""

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return {"accepted": 1, "session_id": "session-1"}


class _RetryingHTTP:
    def __init__(self) -> None:
        self.payloads: list[dict] = []

    async def post(self, _path: str, *, json: dict, **_kwargs) -> _Response:
        self.payloads.append(copy.deepcopy(json))
        return _Response(503 if len(self.payloads) == 1 else 200)


def test_session_retry_reuses_batch_identity_and_event_order(monkeypatch):
    http = _RetryingHTTP()
    session = object.__new__(tr.LiveSession)
    session.session_id = "session-1"
    session.session_token = "token-1"
    session.run_id = "run-1"
    session._http = http
    session._stats = {"sent": 0, "failed": 0}
    monkeypatch.setattr(tr.asyncio, "sleep", AsyncMock())

    asyncio.run(session._post_batch([{"event_type": "test_result"}]))

    assert len(http.payloads) == 2
    assert http.payloads[0] == http.payloads[1]
    uuid.UUID(http.payloads[0]["batch_id"])


def test_api_key_stream_retry_reuses_batch_identity_and_event_order(monkeypatch):
    http = _RetryingHTTP()
    stream = object.__new__(tr.LiveStream)
    stream._run_id = "ci-build-1"
    stream._meta = {}
    stream._http = http
    stream._stats = {"sent": 0, "failed": 0}
    stream._session_id = None
    monkeypatch.setattr(tr.asyncio, "sleep", AsyncMock())

    asyncio.run(stream._post_batch([{"event_type": "test_result"}]))

    assert len(http.payloads) == 2
    assert http.payloads[0] == http.payloads[1]
    uuid.UUID(http.payloads[0]["batch_id"])
