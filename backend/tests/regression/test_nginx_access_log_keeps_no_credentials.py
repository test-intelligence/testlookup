"""The frontend image's nginx access log, and exported spans, keep no credential.

Security review 2026-11-20, TL-04. The access log used to record the whole
request line and the Referer header. A report share link carries its bearer
token in the path (``/api/v1/shared/reports/<token>``), one-time tickets and
the OIDC callback carry theirs in the query string, and a Referer repeats the
URL of the page a request came from. Whoever could read the proxy log could
open the shared report.

The rule for ``frontend/nginx.main.conf``:

* the log format uses none of the variables that hold the raw request line,
  the query string or the Referer;
* the path it logs comes from a ``map`` that drops the query and replaces a
  share link's path whole.

The two ``map`` blocks are also replayed here against request lines, with the
regular expressions read from the file, so a change to either one is tested.

The same share path is replaced in span attributes (``app/core/tracing.py``).
"""
from __future__ import annotations

import base64
import re
from pathlib import Path
from urllib.parse import unquote

import pytest

from app.core import tracing

pytestmark = pytest.mark.regression

ROOT = Path(__file__).resolve().parents[3]
MAIN_CONF = ROOT / "frontend" / "nginx.main.conf"
# A made-up share token with the shape of a real one, built here so that no
# token-looking literal sits in the file.
TOKEN = base64.urlsafe_b64encode(b"foobar-share-token-synthetic").decode().rstrip("=")

#: Variables that carry a raw request line, a query string or a referring URL.
FORBIDDEN_IN_LOG_FORMAT = ("$request ", '$request"', "$request_uri", "$args", "$query_string", "$http_referer", "$uri")


def _directives() -> str:
    """The file without its comments."""
    lines = MAIN_CONF.read_text(encoding="utf-8").splitlines()
    return "\n".join(line for line in lines if not line.lstrip().startswith("#"))


def _log_format() -> str:
    match = re.search(r"log_format\s+main\s+(.*?);", _directives(), re.DOTALL)
    assert match, "nginx.main.conf no longer defines log_format main"
    return "".join(re.findall(r"'([^']*)'", match.group(1)))


def _map_patterns(source: str, target: str) -> list[tuple[str, str]]:
    """``(pattern, value)`` rows of ``map <source> <target> { ... }``, in file order."""
    block = re.search(
        r"map\s+" + re.escape(source) + r"\s+" + re.escape(target) + r"\s*\{(.*?)\}", _directives(), re.DOTALL
    )
    assert block, f"nginx.main.conf has no map {source} {target}"
    rows = []
    for line in block.group(1).splitlines():
        parts = line.strip().rstrip(";").split(None, 1)
        if len(parts) == 2:
            rows.append((parts[0].strip('"'), parts[1].strip().strip('"')))
    return rows


def _apply_map(rows: list[tuple[str, str]], value: str) -> str:
    """What nginx's map gives for ``value``: the first matching regex, else the default."""
    default = "-"
    for pattern, result in rows:
        if pattern == "default":
            default = result
            continue
        assert pattern.startswith("~"), f"only regex rows are expected, got {pattern!r}"
        flags = re.IGNORECASE if pattern.startswith("~*") else 0
        regex = pattern[2:] if pattern.startswith("~*") else pattern[1:]
        regex = re.sub(r"\(\?<", "(?P<", regex)
        match = re.search(regex, value, flags)
        if match:
            return re.sub(r"\$(\w+)", lambda m: match.groupdict().get(m.group(1), m.group(0)), result)
    return default


def _logged_path(request_uri: str) -> str:
    # nginx's $uri: no query, percent-decoded, repeated slashes merged.
    uri = re.sub(r"/+", "/", unquote(request_uri.split("?", 1)[0]))
    is_share = _apply_map(_map_patterns("$uri", "$log_is_share"), uri)
    return _apply_map(_map_patterns('"$log_is_share:$request_uri"', "$log_path"), f"{is_share}:{request_uri}")


def test_the_error_log_is_too_quiet_to_record_request_lines():
    """An error-level nginx message contains the raw request line and the Referer.

    Seen with the image: an upstream that refused a share-link request wrote
    ``request: "GET /api/v1/shared/reports/<token> ..."`` at level ``error``.
    The message text is fixed, so the level is what keeps it out; the access
    log carries the upstream status in its place.
    """
    levels = re.findall(r"(?m)^\s*error_log\s+\S+\s+(\w+)\s*;", _directives())

    assert levels and set(levels) <= {"crit", "alert", "emerg"}, levels
    assert "$upstream_status" in _log_format()


def test_the_log_format_uses_no_variable_that_can_hold_a_credential():
    log_format = _log_format() + " "
    for variable in FORBIDDEN_IN_LOG_FORMAT:
        assert variable not in log_format, f"log_format main records {variable.strip()}"
    assert "$request_method $log_path $server_protocol" in log_format
    assert "$status" in log_format


@pytest.mark.parametrize(
    "request_uri",
    [
        f"/api/v1/shared/reports/{TOKEN}",
        f"/api/v1/shared/reports/{TOKEN}/pdf",
        f"/api/v1/shared/reports/{TOKEN}?download=1",
        f"/api/v1//shared/reports/{TOKEN}",
        f"/api/v1/shared%2Freports/{TOKEN}",
        f"/API/v1/Shared/reports/{TOKEN}",
        f"/api/v1/invocations/abc/events?ticket={TOKEN}",
        f"/api/v1/auth/oidc/callback?code={TOKEN}&state={TOKEN}",
        f"/api/v1/stream/sse/p1?token={TOKEN}",
        f"/api/v1/project-data-downloads/123?ticket={TOKEN}",
    ],
)
def test_a_logged_request_line_never_contains_the_token(request_uri):
    logged = _logged_path(request_uri)

    assert TOKEN not in logged
    assert "?" not in logged
    assert logged.startswith("/")


@pytest.mark.parametrize(
    ("request_uri", "expected"),
    [
        ("/api/v1/runs?page=2", "/api/v1/runs"),
        ("/assets/index-abc.js", "/assets/index-abc.js"),
        ("/", "/"),
        (f"/api/v1/shared/reports/{TOKEN}/pdf", "/api/v1/shared/reports/[REDACTED]"),
    ],
)
def test_the_log_still_says_which_path_was_requested(request_uri, expected):
    assert _logged_path(request_uri) == expected


@pytest.mark.parametrize(
    ("key", "value", "expected"),
    [
        ("http.target", f"/api/v1/shared/reports/{TOKEN}/pdf", "/api/v1/shared/reports/[REDACTED]/pdf"),
        ("url.path", f"/api/v1/shared/reports/{TOKEN}", "/api/v1/shared/reports/[REDACTED]"),
        ("http.url", f"http://api.test/api/v1/shared/reports/{TOKEN}?x=1", "http://api.test/api/v1/shared/reports/[REDACTED]?[REDACTED]"),
        ("url.full", f"https://api.test/api/v1//shared/reports/{TOKEN}", "https://api.test/api/v1//shared/reports/[REDACTED]"),
    ],
)
def test_a_span_attribute_never_keeps_a_share_token(key, value, expected):
    redacted = tracing.redact_span_attributes({key: value, "http.method": "GET"})

    assert redacted is not None
    assert redacted[key] == expected
    assert TOKEN not in repr(redacted)


def test_an_ordinary_path_is_left_as_it_is():
    assert tracing.redact_span_attributes({"url.path": "/api/v1/runs/42", "http.target": "/api/v1/runs/42"}) is None
