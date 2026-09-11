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

import app.services.redaction_service as redaction
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


# ── An Authorization header, whatever the scheme (QA of the H3 fix) ──────

_BASIC = "dXNlcjpwYXNzd29yZA=="  # base64 of "user:password"


@pytest.mark.parametrize(
    "header, credential, redacted",
    [
        (f"Authorization: Basic {_BASIC}", _BASIC, "Authorization: Basic [REDACTED]"),
        (
            'Authorization: Digest username="qa", realm="api", nonce="dcd98b7102dd2f0e", '
            'response="6629fae49393a05397450978507c4ef1"',
            "6629fae49393a05397450978507c4ef1",
            "Authorization: Digest [REDACTED]",
        ),
        (
            "Authorization: Negotiate YIIFzQYGKwYBBQUCoIIFwTCCBb2g",
            "YIIFzQYGKwYBBQUCoIIFwTCCBb2g",
            "Authorization: Negotiate [REDACTED]",
        ),
        (
            "Authorization: Token 9944b09199c62bcf9418ad846dd0e4bb",
            "9944b09199c62bcf9418ad846dd0e4bb",
            "Authorization: Token [REDACTED]",
        ),
        (
            "Authorization: NTLM TlRMTVNTUAABAAAAB4IIogAAAAAAAAAA",
            "TlRMTVNTUAABAAAAB4IIogAAAAAAAAAA",
            "Authorization: NTLM [REDACTED]",
        ),
        ("Authorization: Bearer abc123short", "abc123short", "Authorization: Bearer [REDACTED]"),
        ("Authorization: k-4f9a2c", "k-4f9a2c", "Authorization: [REDACTED]"),
        ("Authorization: SSWS 00QCjAl4MlV", "00QCjAl4MlV", "Authorization: [REDACTED]"),
        (
            "Proxy-Authorization: Basic cHJveHk6c2VjcmV0",
            "cHJveHk6c2VjcmV0",
            "Proxy-Authorization: Basic [REDACTED]",
        ),
    ],
)
@pytest.mark.parametrize(
    "redact", [redaction.redact_log_message, redaction.redact_text], ids=["message", "field"]
)
def test_an_authorization_header_loses_its_credential_whatever_the_scheme(
    redact, header, credential, redacted
):
    """The old pattern wanted 10+ characters in the first token after the
    colon, which is the scheme's name: Basic, Digest, Negotiate, Token and
    NTLM passed intact (Bearer had a pattern of its own, for long tokens)."""
    out = redact(f"request rejected, sent {header}")

    assert credential not in out
    assert out == f"request rejected, sent {redacted}", "a known scheme stays readable"


@pytest.mark.parametrize(
    "text, expected",
    [
        (
            f"headers={{'Accept': '*/*', 'Authorization': 'Basic {_BASIC}', 'Host': 'api'}}",
            "headers={'Accept': '*/*', 'Authorization': 'Basic [REDACTED]', 'Host': 'api'}",
        ),
        (
            f'{{"Authorization": "Basic {_BASIC}", "Accept": "*/*"}}',
            '{"Authorization": "Basic [REDACTED]", "Accept": "*/*"}',
        ),
        (
            f"environ={{'HTTP_AUTHORIZATION': 'Basic {_BASIC}', 'REMOTE_ADDR': '10.42.0.7'}}",
            "environ={'HTTP_AUTHORIZATION': 'Basic [REDACTED]', 'REMOTE_ADDR': '10.42.0.7'}",
        ),
        (
            "{'Authorization': 'Digest username=\"qa\", response=\"6629fae4\"', 'Accept': '*/*'}",
            "{'Authorization': 'Digest [REDACTED]', 'Accept': '*/*'}",
        ),
    ],
)
def test_a_quoted_header_value_is_redacted_up_to_its_closing_quote(text, expected):
    """A requests header dict's repr, a JSON body, a WSGI environ: the value
    ends at its quote, and what follows it is kept."""
    assert redaction.redact_log_message(text) == expected


def test_only_the_header_line_of_a_raw_request_is_redacted():
    request = (
        "GET /api HTTP/1.1\r\nHost: 10.42.0.7:8000\r\n"
        f"Authorization: Basic {_BASIC}\r\nAccept: */*\r\n"
    )

    assert redaction.redact_log_message(request) == (
        "GET /api HTTP/1.1\r\nHost: 10.42.0.7:8000\r\n"
        "Authorization: Basic [REDACTED]\r\nAccept: */*\r\n"
    )


@pytest.mark.parametrize(
    "text",
    [
        "authorization failed for project 42",
        "Authorization header missing on 10.42.0.7",
        "authorization_url: https://github.com/login/oauth/authorize",
        "X-Authorization-Status: ok",
        '    headers["Authorization"] = token',
    ],
)
def test_prose_about_authorization_is_left_alone(text):
    assert redaction.redact_log_message(text) == text


def test_redacting_twice_changes_nothing():
    once = redaction.redact_text(f"Authorization: Basic {_BASIC}")
    assert redaction.redact_text(once) == once


def test_a_basic_credential_in_the_message_is_redacted(json_logs):
    import logging

    logging.getLogger("tests.m").warning(
        "upstream rejected the call, sent %s", f"Authorization: Basic {_BASIC}"
    )

    assert _BASIC not in json_logs.getvalue()
    assert _records(json_logs)[-1]["event"] == (
        "upstream rejected the call, sent Authorization: Basic [REDACTED]"
    )


def test_a_basic_credential_in_a_traceback_is_redacted(json_logs):
    import logging

    try:
        raise PermissionError(f"401 from api: sent Authorization: Basic {_BASIC}")
    except PermissionError:
        logging.getLogger("tests.m").error("call failed", exc_info=True)

    assert _BASIC not in json_logs.getvalue()
    exception = _records(json_logs)[-1]["exception"]
    assert "PermissionError" in exception
    assert "Authorization: Basic [REDACTED]" in exception


def test_a_basic_credential_in_a_structured_field_is_redacted(json_logs):
    import structlog

    structlog.get_logger("tests.m").warning(
        "upstream_rejected",
        request_headers=f"{{'Authorization': 'Basic {_BASIC}', 'Accept': '*/*'}}",
    )

    assert _BASIC not in json_logs.getvalue()
    assert _records(json_logs)[-1]["request_headers"] == (
        "{'Authorization': 'Basic [REDACTED]', 'Accept': '*/*'}"
    )


# ── A field NAMED like a secret is redacted by its name (QA of the H3 fix) ──

_SECRET_NAMES = [
    "password", "passwd", "secret", "client_secret", "api_key", "apikey",
    "x_api_key", "token", "access_token", "refresh_token", "id_token",
    "session_token", "authorization", "cookie", "set_cookie", "private_key",
    "webhook_secret", "smtp_password",
]


@pytest.mark.parametrize("name", _SECRET_NAMES)
def test_a_field_named_like_a_secret_is_redacted_by_its_name(json_logs, name):
    """``log.info("x", password="pw-plain")`` wrote ``"password": "pw-plain"``:
    the value carries no marker, and the processor read values only."""
    import structlog

    structlog.get_logger("tests.n").info("configured", **{name: "pw-plain"})

    assert "pw-plain" not in json_logs.getvalue()
    assert _records(json_logs)[-1][name] == "[REDACTED]"


@pytest.mark.parametrize(
    "name", ["Password", "X-API-Key", "Set-Cookie", "CLIENT_SECRET", "Refresh-Token"]
)
def test_the_name_is_matched_ignoring_case_and_hyphens(json_logs, name):
    import structlog

    structlog.get_logger("tests.n").info("configured", **{name: "pw-plain"})

    assert "pw-plain" not in json_logs.getvalue()
    assert _records(json_logs)[-1][name] == "[REDACTED]"


def test_a_secret_named_key_inside_a_nested_field_is_redacted(json_logs):
    import structlog

    structlog.get_logger("tests.n").info(
        "upstream_call",
        request={
            "headers": {
                "Authorization": "Basic bmVzdGVkLWNyZWQ=",
                "X-API-Key": "k-nested-123",
                "Accept": "*/*",
            },
            "attempts": [{"token": "t-nested-one"}, {"token": "t-nested-two", "status": 401}],
        },
    )

    written = json_logs.getvalue()
    for secret in ("bmVzdGVkLWNyZWQ=", "k-nested-123", "t-nested-one", "t-nested-two"):
        assert secret not in written
    request = _records(json_logs)[-1]["request"]
    assert request["headers"] == {
        "Authorization": "[REDACTED]",
        "X-API-Key": "[REDACTED]",
        "Accept": "*/*",
    }
    assert request["attempts"] == [
        {"token": "[REDACTED]"},
        {"token": "[REDACTED]", "status": 401},
    ]


def test_metrics_and_flags_that_merely_contain_a_secret_word_are_kept(json_logs):
    """The names match EXACTLY. A token count, a rotation policy or the id of
    an API key is what an operator reads the line for."""
    import structlog

    fields = {
        "tokens": 5,
        "token_count": 120,
        "prompt_tokens": 12,
        "completion_tokens": 30,
        "max_tokens": 4096,
        "password_changed": True,
        "secret_rotation_days": 90,
        "api_key_id": "7c9e6679-7425-40de-944b-e07fc1f90ae7",
        "api_key_name": "ci-runner",
        # Look-alike names logged in app/ today.
        "token_action": "rotate",
        "custom_password": False,
        "input_tokens": 10,
        "output_tokens": 20,
        "api_keys": 3,
    }
    structlog.get_logger("tests.n").info("usage", usage=dict(fields), **fields)

    record = _records(json_logs)[-1]
    for name, value in fields.items():
        assert record[name] == value, f"{name} was redacted"
    assert record["usage"] == fields, "the same names were redacted when nested"


def test_none_and_a_bool_under_a_secret_name_are_kept(json_logs):
    """Neither can carry a secret, and "no token" or "password set" is worth
    keeping. Any other value is replaced, a number included: an OTP is one."""
    import structlog

    structlog.get_logger("tests.n").info(
        "auth_state", token=None, password=True, secret=False, session_token=123456
    )

    record = _records(json_logs)[-1]
    assert record["token"] is None
    assert record["password"] is True
    assert record["secret"] is False
    assert record["session_token"] == "[REDACTED]"


def test_a_pii_name_is_not_a_secret_name(json_logs):
    """A log's ``address=`` is as often a host as a street; PII values keep
    their own patterns instead of losing the field by name."""
    import structlog

    structlog.get_logger("tests.n").info(
        "connected", address="10.42.0.7:6379", email="qa.lead@example.com"
    )

    record = _records(json_logs)[-1]
    assert record["address"] == "[REDACTED_IP]:6379"
    assert record["email"] == "[REDACTED_EMAIL]"


def test_the_callers_objects_are_not_edited(json_logs):
    """A dict passed to a log call is the caller's own: it may be persisted,
    returned or sent after the call."""
    import copy

    import structlog

    payload = {"password": "pw-plain", "note": "call 555-123-4567", "tags": ["a@b.io"]}
    before = copy.deepcopy(payload)
    structlog.get_logger("tests.n").info("saved", payload=payload)

    assert payload == before
    logged = _records(json_logs)[-1]["payload"]
    assert logged["password"] == "[REDACTED]"
    assert "555-123-4567" not in logged["note"]
    assert logged["tags"] == ["[REDACTED_EMAIL]"]


def test_strings_inside_a_nested_field_get_that_fields_patterns(json_logs):
    import structlog

    dsn = "postgresql://svc:dsnpass99@db:5432/app"
    structlog.get_logger("tests.n").warning(
        "upstream_failed",
        config={"dsn": dsn, "peer": "10.42.0.7"},
        error={"message": _REDIS_ERROR, "dsn": dsn},
    )

    record = _records(json_logs)[-1]
    assert "dsnpass99" not in json_logs.getvalue()
    assert record["config"]["peer"] == "[REDACTED_IP]", "a structured field keeps the full set"
    assert record["error"]["message"] == _REDIS_ERROR, "free text stays free text when nested"


def test_a_subtree_past_the_depth_cap_is_replaced_not_passed_through(json_logs):
    import structlog

    deep: dict = {"note": "call 555-123-4567"}
    for _ in range(12):
        deep = {"nested": deep}
    structlog.get_logger("tests.n").info("deep", payload=deep)

    written = json_logs.getvalue()
    assert "555-123-4567" not in written
    assert "[REDACTED]" in written


def test_a_self_referencing_field_does_not_break_the_log_call(json_logs):
    import structlog

    loop: list = ["call 555-123-4567"]
    loop.append(loop)
    structlog.get_logger("tests.n").info("cyclic", payload=loop)

    assert _records(json_logs)[-1]["event"] == "cyclic"
    assert "555-123-4567" not in json_logs.getvalue()


def test_a_secret_bound_into_the_context_is_redacted(json_logs):
    import structlog

    structlog.contextvars.bind_contextvars(session_token="ctx-secret-value")
    try:
        structlog.get_logger("tests.n").info("request_done")
    finally:
        structlog.contextvars.unbind_contextvars("session_token")

    assert "ctx-secret-value" not in json_logs.getvalue()
    assert _records(json_logs)[-1]["session_token"] == "[REDACTED]"
