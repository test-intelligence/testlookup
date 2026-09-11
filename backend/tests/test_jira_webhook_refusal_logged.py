"""R-B45-R3-4: a Jira delivery refused for its timestamp leaves a trace.

It is answered 200 ``applied: false`` (a 4xx would make Jira retry forever),
so without a log line a sender whose clock is fast, or whose bodies lack the
field, fails silently on every delivery.
"""
from __future__ import annotations

import hashlib
import json
import time
from types import SimpleNamespace

import pytest
import structlog

from app.routers import feedback as fb


class _Request:
    def __init__(self, body: bytes):
        self._body = body
        self.headers = {}

    async def body(self) -> bytes:
        return self._body


@pytest.mark.asyncio
@pytest.mark.parametrize("stamp,kind", [
    ("missing", "missing"),
    (None, "missing"),
    ("1800000000000", "invalid"),
    ("nan", "invalid"),
    (int((time.time() - fb.JIRA_REPLAY_WINDOW_SECONDS - 60) * 1000), "outside_window"),
    (int((time.time() + 3600) * 1000), "outside_window"),
])
async def test_a_refused_delivery_is_logged_without_its_body(monkeypatch, stamp, kind):
    monkeypatch.setattr(fb, "_verify_jira_signature", lambda body, header: None)
    secret_marker = "SECRET-ISSUE-CONTENT"
    issue = {"key": secret_marker}
    if stamp == "missing":
        body = json.dumps({"issue": issue}).encode()
    elif stamp == "nan":
        body = b'{"timestamp": NaN, "issue": ' + json.dumps(issue).encode() + b"}"
    else:
        body = json.dumps({"timestamp": stamp, "issue": issue}).encode()

    with structlog.testing.capture_logs() as logs:
        result = await fb.jira_resolution_webhook(_Request(body), SimpleNamespace())

    assert result["applied"] is False
    refused = [e for e in logs if e.get("event") == "jira_webhook_delivery_refused"]
    assert len(refused) == 1, logs
    entry = refused[0]
    assert entry["log_level"] == "warning"
    assert entry["reason"] == kind
    assert entry["delivery"] == hashlib.sha256(body).hexdigest()[:12]
    assert secret_marker not in json.dumps(entry, default=str)


@pytest.mark.asyncio
async def test_an_accepted_delivery_logs_no_refusal(monkeypatch):
    monkeypatch.setattr(fb, "_verify_jira_signature", lambda body, header: None)

    async def claim(raw):
        return False, "k"  # already received: stops before touching the DB

    monkeypatch.setattr(fb, "_claim_delivery", claim)
    body = json.dumps({"timestamp": int(time.time() * 1000), "issue": {"key": "X-1"}}).encode()
    with structlog.testing.capture_logs() as logs:
        await fb.jira_resolution_webhook(_Request(body), SimpleNamespace())
    assert not [e for e in logs if e.get("event") == "jira_webhook_delivery_refused"]
