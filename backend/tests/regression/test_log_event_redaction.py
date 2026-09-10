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

from app.core.logging_config import (
    _FREE_TEXT_FIELDS,
    _STRUCTURAL_LOG_FIELDS,
    _privacy_redaction,
)


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


# ── The message is operational text (code review of the H3 fix) ─────────


@pytest.mark.parametrize(
    "message",
    [
        "Task 7f3a processed 1234567890 bytes",
        "lease renewed at epoch 1757500000",
        "run_id=build-555-123-4567 finished",
        "run 123-45-6789 retried",
        "uvicorn version 0.49.0.1 started",
        "offline_egress_blocked channel=SMTP host=10.42.0.15",
        "Batch of 4111111111111111 rows",
    ],
)
def test_numbers_and_host_addresses_in_the_message_survive(message):
    """The PII heuristics match digit runs and dotted quads by shape.

    Applied to the message they rewrote byte counts, epoch seconds, build
    numbers, and the very host an operator needs from a warning.
    """
    assert _redact(event=message)["event"] == message


def test_a_structured_field_keeps_the_pii_heuristics():
    """Only the message was narrowed."""
    out = _redact(event="ok", caller="call 555-123-4567", peer="10.42.0.15")
    assert "555-123-4567" not in out["caller"]
    assert "10.42.0.15" not in out["peer"]


def test_redact_text_still_applies_every_pattern():
    """Its other callers -- agent evidence, error excerpts -- keep the full set."""
    from app.services.redaction_service import redact_text

    assert "555-123-4567" not in redact_text("call 555-123-4567")
    assert "10.42.0.15" not in redact_text("peer 10.42.0.15")
    assert "qa.lead@example.com" not in redact_text("mail qa.lead@example.com")


# ── A traceback is redacted too (QA of the H3 fix) ───────────────────────


@pytest.fixture
def json_logs(monkeypatch):
    """The real pipeline from configure_logging(), writing JSON to a buffer.

    Every other test here calls the redaction processor directly, which is why
    a traceback that reached the renderer unredacted went unseen.
    """
    import io
    import logging

    import structlog

    from app.core import logging_config
    from app.core.config import settings

    root = logging.getLogger()
    saved_handlers, saved_level = root.handlers[:], root.level
    saved_structlog = structlog.get_config()
    monkeypatch.setattr(settings, "LOG_FORMAT", "json")
    logging_config.configure_logging()
    buffer = io.StringIO()
    root.handlers[-1].setStream(buffer)
    try:
        yield buffer
    finally:
        root.handlers[:] = saved_handlers
        root.setLevel(saved_level)
        structlog.configure(**saved_structlog)


_SECRET_ERROR = "login rejected for qa.lead@example.com password=hunter2xyz via 10.42.0.15"


def test_a_stdlib_traceback_is_redacted_before_it_is_written(json_logs):
    """43 call sites pass exc_info=True. The traceback reached the JSON renderer
    as an exc_info tuple, after redaction had run, and was written out
    verbatim -- exception message and all."""
    import logging

    try:
        raise RuntimeError(_SECRET_ERROR)
    except RuntimeError:
        logging.getLogger("tests.h3").error("login failed", exc_info=True)

    written = json_logs.getvalue()
    assert "login failed" in written
    assert "RuntimeError" in written, "the traceback was dropped rather than redacted"
    assert "hunter2xyz" not in written
    assert "qa.lead@example.com" not in written
    assert "10.42.0.15" in written, "a traceback is operational text; its host must survive"


def test_a_structlog_exception_is_redacted_before_it_is_written(json_logs):
    import structlog

    try:
        raise RuntimeError(_SECRET_ERROR)
    except RuntimeError:
        structlog.get_logger("tests.h3").exception("login failed")

    written = json_logs.getvalue()
    assert "login failed" in written
    assert "RuntimeError" in written, "the traceback was dropped rather than redacted"
    assert "hunter2xyz" not in written
    assert "qa.lead@example.com" not in written


# ── error=, reason= and detail= are free text (code review of R6) ────────


def _records(buffer) -> list[dict]:
    """The JSON records the real pipeline wrote, one per line."""
    import json

    return [json.loads(line) for line in buffer.getvalue().splitlines() if line.strip()]


_REDIS_ERROR = "Error 111 connecting to 10.42.0.7:6379. Connection refused."


@pytest.mark.parametrize("field", ["error", "reason", "detail"])
@pytest.mark.parametrize(
    "text",
    [
        _REDIS_ERROR,
        "timed out after 1234567890 ms",
        "lease expired at epoch 1757500000",
    ],
)
def test_an_error_field_keeps_its_host_and_numbers(json_logs, field, text):
    """Some 250 calls log ``error=str(exc)``. Under the shape-based patterns a
    Redis error lost the host and port it is read for, and a timeout its
    milliseconds: the class R6 fixed for the message, one field over."""
    import structlog

    structlog.get_logger("tests.r6").warning("redis_unavailable", **{field: text})

    assert _records(json_logs)[-1][field] == text


@pytest.mark.parametrize(
    "text, secret",
    [
        ("login rejected: password=hunter2xyz", "hunter2xyz"),
        (
            "upstream said Authorization: Bearer abcdefghijklmnopqrstuvwxyz",
            "abcdefghijklmnopqrstuvwxyz",
        ),
        ("cannot reach postgresql://svc:dsnpass99@10.42.0.7:5432/app", "dsnpass99"),
        ("could not notify qa.lead@example.com", "qa.lead@example.com"),
    ],
)
def test_a_credential_or_an_email_in_an_error_field_is_still_redacted(
    json_logs, text, secret
):
    """Free text keeps every pattern that recognises a secret by a marker."""
    import structlog

    structlog.get_logger("tests.r6").warning("upstream_failed", error=text)

    assert secret not in json_logs.getvalue()
    assert "REDACTED" in _records(json_logs)[-1]["error"]


def test_an_error_field_loses_the_dsn_password_but_keeps_the_host(json_logs):
    import structlog

    structlog.get_logger("tests.r6").warning(
        "upstream_failed", error="cannot reach postgresql://svc:dsnpass99@10.42.0.7:5432/app"
    )

    assert _records(json_logs)[-1]["error"] == (
        "cannot reach postgresql://svc:[REDACTED]@10.42.0.7:5432/app"
    )


def test_other_structured_fields_keep_the_pii_heuristics(json_logs):
    """Only the free-text fields were narrowed."""
    import structlog

    structlog.get_logger("tests.r6").warning(
        "upstream_failed", error=_REDIS_ERROR, note="call 555-123-4567", peer="10.42.0.7"
    )

    record = _records(json_logs)[-1]
    assert record["error"] == _REDIS_ERROR
    assert "555-123-4567" not in record["note"]
    assert "10.42.0.7" not in record["peer"]


def test_an_exception_passed_as_a_field_is_redacted_as_its_repr(json_logs):
    """``error=exc`` (3 call sites) hands over the object, not its text.

    The processor skipped it as a non-string, and the JSON renderer then wrote
    its repr -- after redaction had run, secrets and all.
    """
    import structlog

    exc = ConnectionError(f"{_REDIS_ERROR} auth password=hunter2xyz")
    structlog.get_logger("tests.r6").warning("chromadb_unavailable", error=exc)

    assert "hunter2xyz" not in json_logs.getvalue()
    error = _records(json_logs)[-1]["error"]
    assert error.startswith("ConnectionError("), "the line should read as the renderer wrote it"
    assert "10.42.0.7:6379" in error, "an exception's text is free text too"


def test_an_exception_whose_repr_raises_does_not_break_the_log_call(json_logs):
    """A processor runs in the caller's frame; logging must not raise there."""
    import structlog

    class Unprintable(Exception):
        def __repr__(self):
            raise RuntimeError("no repr")

    structlog.get_logger("tests.r6").warning("odd_failure", error=Unprintable())

    assert "Unprintable object at" in _records(json_logs)[-1]["error"]


def test_the_free_text_fields_are_pinned():
    """A free-text field loses the phone, SSN, card and IPv4 patterns.

    Widening this set is a decision about what may reach the log unredacted,
    so it must be made here, deliberately.
    """
    assert _FREE_TEXT_FIELDS == {"event", "exception", "stack", "error", "reason", "detail"}
