"""The log message itself must be redacted, not just its structured fields.

Re-audit finding H3. ``_privacy_redaction`` scrubbed every string value in a
log record **except** ``event`` — which is the field that holds the message.
Every ``logger.warning("could not authenticate with %s", token)`` renders its
arguments there, as does every f-string message. So the one field guaranteed to
contain free-form, unreviewed text was the one field guaranteed not to be
scrubbed, while the structured key/value pairs a developer chose deliberately
were being cleaned.

What stays exempt is the record's own structure — timestamp, level, logger,
service, version, env, trace and span ids. None of those can hold caller data,
and redacting them would corrupt the record: an ``@`` in a logger name would be
rewritten as an email address.
"""
from __future__ import annotations

import pytest

from app.core.logging_config import _STRUCTURAL_LOG_FIELDS, _privacy_redaction


def _redact(**fields):
    return _privacy_redaction(None, "info", dict(fields))


# ── The defect ───────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "message, must_not_contain",
    [
        ("auth failed: token=sk-live-abcdef123456", "sk-live-abcdef123456"),
        ("upstream said Authorization: Bearer abcdefghijklmnopqrstuvwxyz", "abcdefghijklmnopqrstuvwxyz"),
        ("connecting to postgres://app:hunter2xyz@db:5432/x", "hunter2xyz"),
        ("notifying qa.lead@example.com about the failure", "qa.lead@example.com"),
        ("api_key=AKIAIOSFODNN7EXAMPLE rejected", "AKIAIOSFODNN7EXAMPLE"),
    ],
)
def test_a_secret_in_the_message_is_redacted(message, must_not_contain):
    out = _redact(event=message)
    assert must_not_contain not in out["event"], (
        "the message field is not redacted, so anything interpolated into a "
        "log line — which is where %s arguments and f-strings land — is "
        "written to the log verbatim"
    )
    assert "REDACTED" in out["event"]


def test_structured_fields_are_still_redacted():
    """The behaviour that already worked must keep working."""
    out = _redact(event="auth failed", detail="password=hunter2xyz")
    assert "hunter2xyz" not in out["detail"]


# ── What must NOT be touched ─────────────────────────────────────────────


def test_the_records_own_structure_is_left_alone():
    """These are set by the logging stack, not by a caller.

    Redacting them would corrupt the record — a logger name containing an ``@``
    would be rewritten as an email address.
    """
    fields = {
        "timestamp": "2026-09-10T10:00:00Z",
        "level": "warning",
        "logger": "services.auth@v2",
        "service": "TestLookup",
        "version": "0.0.1",
        "env": "production",
        "trace_id": "0af7651916cd43dd8448eb211c80319c",
        "span_id": "b7ad6b7169203331",
    }
    out = _redact(event="ok", **fields)
    for key, value in fields.items():
        assert out[key] == value, f"{key} was rewritten by redaction"


def test_event_is_not_in_the_structural_exemptions():
    """The defect, pinned as a property rather than only as behaviour."""
    assert "event" not in _STRUCTURAL_LOG_FIELDS, (
        "'event' is exempt from redaction again — that is the field every "
        "log message and every interpolated argument lands in"
    )


def test_the_exemption_list_covers_only_structure():
    """A new exemption must be a field the caller cannot influence."""
    assert _STRUCTURAL_LOG_FIELDS == {
        "timestamp",
        "level",
        "logger",
        "service",
        "version",
        "env",
        "trace_id",
        "span_id",
    }, (
        "the redaction exemption list changed. Every entry must be a field set "
        "by the logging stack itself; anything a caller can put text into has "
        "to be redacted."
    )


# ── Ordinary messages must survive intact ────────────────────────────────


@pytest.mark.parametrize(
    "message",
    [
        "ingestion complete: 412 tests, 3 failed",
        "run 9f8e7d6c-5b4a-4938-8271-0a1b2c3d4e5f finalised",
        "retrying in 5s (attempt 2 of 3)",
        "queue depth ingestion.shard.4 = 17",
    ],
)
def test_an_ordinary_message_is_unchanged(message):
    """Redaction that mangled normal logs would be traded for the leak."""
    assert _redact(event=message)["event"] == message


def test_a_non_string_value_is_left_alone():
    out = _redact(event="done", count=412, ok=True, ratio=0.97)
    assert out["count"] == 412
    assert out["ok"] is True
    assert out["ratio"] == 0.97
