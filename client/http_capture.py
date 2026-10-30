"""Opt-in HTTP exchange capture for the TestLookup Python SDK (W23d AGT-14).

When a run opts in -- ``pytest --testlookup-capture-http``, ``TESTLOOKUP_CAPTURE_HTTP=1``
or ``testlookup.capture_http=true`` / ``reporting.capture_http: true`` in the config
file -- the reporter records the HTTP requests each test makes with ``requests``
(``Session.send``) or ``httpx`` (``Client.send`` / ``AsyncClient.send``) and sends
them beside the test's result, so a failing test's page shows what it sent and what
came back, and the deep investigation can compare response fields across runs.

OFF by default: nothing is wrapped unless capture is enabled. The SDK's OWN client
(the reporter's ``httpx.AsyncClient``) is excluded by identity, so TestLookup's
traffic is never captured. A streamed response is never read (its body is recorded
as not captured), so capture never consumes a test's stream. The wrapper calls the
original first and records after, swallowing every recorder error: capture can
never change what a test sees.

Secrets never leave the CI machine: every exchange is redacted HERE, before it is
buffered, with the SAME rules the server applies again
(``backend/app/services/http_exchange_redaction.py``; the cross-language
specification is ``sdk-vectors/http_exchange.json`` and
``client/tests/test_http_capture.py`` runs every one of its cases through this
port). Header values of ``HEADER_DENYLIST`` names (and names ending in a secret
suffix) are replaced by ``[REDACTED]``; query values of ``QUERY_DENYLIST`` names
and URL userinfo too; every other value, and every JSON string leaf, passes the
text rules (secret-named pairs, provider token shapes, credentials, JWTs, e-mail
addresses, phone / card / IPv4 shapes). Bodies are cut at ``BODY_WIRE_MAX_BYTES``
first, at most ``EXCHANGES_PER_TEST_MAX`` exchanges are kept per test.

Stdlib only (``requests`` / ``httpx`` are wrapped only when importable).
"""

from __future__ import annotations

import json
import logging
import math
import re
import threading
import time
from datetime import datetime, timezone
from typing import Any, Callable, Iterable, Mapping, Optional
from urllib.parse import quote, unquote_plus

__all__ = [
    "HttpCapture",
    "capture_enabled",
    "derive_endpoint",
    "redact_body",
    "redact_exchange",
    "redact_headers",
    "redact_url",
    "text_pass",
]

logger = logging.getLogger("testlookup_reporter.http_capture")

# ── The server's vocabulary (http_exchange_vocab.py; pinned by sdk-vectors/http_exchange.json) ──
EXCHANGES_PER_TEST_MAX = 20
BODY_WIRE_MAX_BYTES = 65_536
BODY_STORED_MAX_BYTES = 16_384
TRUNCATION_MARKER = "…[truncated: {omitted} bytes not stored]"
HEADERS_MAX = 50
HEADER_NAME_MAX, HEADER_VALUE_MAX = 128, 2_048
URL_MAX, ENDPOINT_MAX = 2_048, 300
METHOD_RE = r"^[A-Z]{1,16}$"
SCHEMA_KEYS_MAX = 200
FIELD_NAME_RE = r"^[A-Za-z_$@][A-Za-z0-9_.$@:-]{0,119}$"
HEADER_DENYLIST = (
    "authorization", "proxy-authorization", "cookie", "set-cookie", "x-api-key", "api-key", "x-auth-token",
    "x-access-token", "x-csrf-token", "x-xsrf-token", "x-amz-security-token", "x-goog-api-key",
    "x-session-token", "private-token", "x-gitlab-token", "x-hub-signature", "x-hub-signature-256",
)
HEADER_SECRET_SUFFIXES = ("token", "secret", "password", "api-key", "apikey", "signature", "credential")
QUERY_DENYLIST = (
    "token", "access_token", "id_token", "refresh_token", "api_key", "apikey", "key", "code", "password",
    "secret", "client_secret", "sig", "signature", "x-amz-signature", "x-amz-credential", "x-amz-security-token", "auth",
)
ENDPOINT_ID_SEGMENT_RES = (
    r"^\d+$", r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$",
    r"^[0-9a-fA-F]{24}$", r"^[A-Za-z0-9_-]{16,}$",
)
REDACTED = "[REDACTED]"
# run_context_vocab.TOKEN_SHAPES (W23b) -- the same list client/run_context.py carries.
TOKEN_SHAPES = (
    r"gh[pousr]_[A-Za-z0-9]{20,}", r"github_pat_[A-Za-z0-9_]{20,}", r"glpat-[A-Za-z0-9_-]{16,}",
    r"xox[abprs]-[A-Za-z0-9-]{10,}", r"sk-[A-Za-z0-9_-]{20,}", r"AKIA[A-Z0-9]{16}",
    r"[A-Za-z0-9+/=_-]{40,}",
)
UNNAMED_KEYS = "_unnamed_keys"
JSON_DEPTH_MAX = 10
JSON_TYPES = ("string", "number", "boolean", "object", "array", "null")
SCHEMA_STATES = ("parsed", "not_json", "empty", "too_large")
ERROR_MAX = 200

# ── redaction_service (the key names and redact_text's patterns, in its order) ──
_SECRET_KEY_NAMES = frozenset({
    "password", "passwd", "secret", "token", "api_key", "apikey",
    "api_token", "authorization", "auth_token", "access_token",
    "refresh_token", "private_key", "secret_key", "credential",
    "credentials", "cookie", "hashed_password", "jwt", "bearer",
    "smtp_password", "minio_secret_key", "webhook_secret",
    "client_secret", "signing_secret", "personal_access_token",
    "private_token", "session_token", "id_token", "encryption_key",
    "ssh_key", "deploy_key", "webhook_token",
})
_PII_KEY_NAMES = frozenset({
    "email", "email_address", "phone", "phone_number", "mobile",
    "ssn", "social_security", "social_security_number",
    "credit_card", "card_number", "cc_number",
    "dob", "date_of_birth", "address", "street_address",
    "national_id", "passport_number", "drivers_license",
})
SENSITIVE_KEYS = _SECRET_KEY_NAMES | _PII_KEY_NAMES
LOG_SECRET_KEYS = _SECRET_KEY_NAMES | frozenset({
    "x_api_key", "set_cookie", "proxy_authorization",
    "x_api_token", "x_auth_token", "x_webhook_secret",
    "access_tokens", "refresh_tokens", "webhook_url", "smtp_pass",
    "signing_key", "hmac_key", "app_secret_key_previous", "jwt_secret_key_previous",
    "passphrase", "client_secret", "session_cookie",
})
LOG_SECRET_NAME_EXCEPTIONS = frozenset({"next_page_token", "page_token", "pagination_token", "next_token"})
LOG_SECRET_SUFFIXES = (
    "_token", "_password", "_passwd", "_secret", "_api_key", "_apikey",
    "_secret_key", "_access_key", "_private_key", "_webhook_url",
    "_credential", "_credentials", "_passphrase",
)

_AUTH_SCHEMES = (
    "Basic|Bearer|Digest|Negotiate|NTLM|Kerberos|Token|ApiKey|"
    "AWS4-HMAC-SHA256|DPoP|HOBA|Mutual|OAuth|SCRAM-SHA-1|SCRAM-SHA-256|vapid"
)
_AUTHORIZATION_HEADER = re.compile(
    r"(?P<head>(?<![A-Za-z0-9])Authorization(?:\\?[\"'])?[ \t]*[:=][ \t]*"
    r"(?P<open>\\?[\"'])?)"
    r"(?P<scheme>(?:" + _AUTH_SCHEMES + r")[ \t]+)?"
    r"(?:(?!(?P=open))[^\r\n])+",
    re.IGNORECASE,
)
_TEXT_PATTERNS: tuple[tuple["re.Pattern[str]", str], ...] = (
    (re.compile(r"(Bearer\s+)[A-Za-z0-9\-_\.]{20,}", re.IGNORECASE), r"\1[REDACTED]"),
    (_AUTHORIZATION_HEADER, r"\g<head>\g<scheme>[REDACTED]"),
    (re.compile(r"(hooks\.slack\.com/(?:services|workflows|triggers)/)[^\s'\"<>]+", re.IGNORECASE), r"\1[REDACTED]"),
    (re.compile(r"(\.webhook\.office\.com/webhookb2/)[^\s'\"<>]+", re.IGNORECASE), r"\1[REDACTED]"),
    (re.compile(r"(outlook\.office(?:365)?\.com/webhook/)[^\s'\"<>]+", re.IGNORECASE), r"\1[REDACTED]"),
    (re.compile(r"(discord(?:app)?\.com/api/webhooks/)[^\s'\"<>]+", re.IGNORECASE), r"\1[REDACTED]"),
    (re.compile(r"((?:\.logic\.azure\.com|\.powerplatform\.com)[^\s'\"<>]*?[?&]sig=)[^\s&'\"<>]+", re.IGNORECASE),
     r"\1[REDACTED]"),
    (re.compile(r"((?:Set-)?Cookie:\s*)[^\r\n]+", re.IGNORECASE), r"\1[REDACTED]"),
    (re.compile(r"(api[_-]?key\s*[:=]\s*)['\"]?[^\s'\",]{4,}['\"]?", re.IGNORECASE), r"\1[REDACTED]"),
    (re.compile(r"(token\s*[:=]\s*)['\"]?[^\s'\",]{4,}['\"]?", re.IGNORECASE), r"\1[REDACTED]"),
    (re.compile(r"(password\s*[:=]\s*)['\"]?[^\s'\",]{4,}['\"]?", re.IGNORECASE), r"\1[REDACTED]"),
    (re.compile(r"(secret[_-]?key\s*[:=]\s*)['\"]?[A-Za-z0-9\-_]{10,}['\"]?", re.IGNORECASE), r"\1[REDACTED]"),
    (re.compile(r"(AKIA[A-Z0-9]{16})"), "[REDACTED_AWS_KEY]"),
    (re.compile(r"eyJ[A-Za-z0-9_-]{20,}\.eyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}"), "[REDACTED_JWT]"),
    (re.compile(r"(://[^:]+:)[^@]{4,}(@)"), r"\1[REDACTED]\2"),
    (re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}"), "[REDACTED_EMAIL]"),
    (re.compile(r"\b(\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b"), "[REDACTED_PHONE]"),
    (re.compile(r"\b\d{3}[-\s]\d{2}[-\s]\d{4}\b"), "[REDACTED_SSN]"),
    (re.compile(r"\b\d{4}[-\s]?\d{4}[-\s]?\d{4}[-\s]?\d{4}\b(?!-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b)"), "[REDACTED_CC]"),
    (re.compile(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b"), "[REDACTED_IP]"),
)

# ── The port of http_exchange_redaction.py (same names, same order) ───────────
_TOKEN_SHAPE_RES = tuple(re.compile(shape) for shape in TOKEN_SHAPES)
_METHOD = re.compile(METHOD_RE)
_FIELD_NAME = re.compile(FIELD_NAME_RE)
_ID_SEGMENTS = tuple(re.compile(pattern) for pattern in ENDPOINT_ID_SEGMENT_RES)
_HEADER_DENY = frozenset(HEADER_DENYLIST)
_QUERY_DENY = frozenset(QUERY_DENYLIST)
_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_UNUSABLE_CHARS = re.compile("[\x00\ud800-\udfff]")
_MARKER_HEAD, _MARKER_TAIL = TRUNCATION_MARKER.split("{omitted}")
_MARKER_AT_END = re.compile(re.escape(_MARKER_HEAD) + r"(\d{1,15})" + re.escape(_MARKER_TAIL) + r"\Z")
_SETTLE_ROUNDS = 6
_JSON_PAIR = re.compile(r'"((?:[^"\\\r\n]|\\.){1,128})"(\s*:\s*)(?:"(?:[^"\\]|\\.)*(?:"|$(?![\s\S]))|-?[0-9][0-9.eE+-]*)')
_FORM_PAIR = re.compile(r"(?<![A-Za-z0-9_.-])([A-Za-z][A-Za-z0-9_.-]{0,127})=([^&\s\"',;]+)")
#: RFC 3986 appendix B: scheme, authority, path, query (the fragment is matched and dropped).
URL_PARTS_RE = r"\A(?:([A-Za-z][A-Za-z0-9+.-]*):)?(?://([^/?#]*))?([^?#]*)(?:\?([^#]*))?(?:#[\s\S]*)?\Z"
_URL_PARTS = re.compile(URL_PARTS_RE)


def _clean(text: str) -> str:
    if not _UNUSABLE_CHARS.search(text):
        return text
    return _UNUSABLE_CHARS.sub(lambda m: "" if m.group(0) == "\x00" else "�", text)


def _token_pass(text: str) -> str:
    for shape in _TOKEN_SHAPE_RES:
        text = shape.sub(REDACTED, text)
    return text


def _redact_text(text: str) -> str:
    if not text:
        return text
    for pattern, replacement in _TEXT_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def _is_log_secret_name(name: str) -> bool:
    name = name.lower().replace("-", "_")
    if name in LOG_SECRET_NAME_EXCEPTIONS:
        return False
    return name in LOG_SECRET_KEYS or name.endswith(LOG_SECRET_SUFFIXES)


def _key_is_secret(key: str) -> bool:
    plain = key.lower().replace("-", "_")
    snake = _CAMEL_BOUNDARY.sub("_", key).lower().replace("-", "_")
    return any(name in SENSITIVE_KEYS or _is_log_secret_name(name) for name in (plain, snake))


def _redact_json_pair(match: "re.Match[str]") -> str:
    if not _key_is_secret(match.group(1)):
        return match.group(0)
    return f'"{match.group(1)}"{match.group(2)}"{REDACTED}"'


def _redact_form_pair(match: "re.Match[str]") -> str:
    if not _key_is_secret(match.group(1)):
        return match.group(0)
    return f"{match.group(1)}={REDACTED}"


def text_pass(text: str) -> str:
    """Secret-named pairs → token shapes → the credential / PII patterns → token shapes. Idempotent."""
    if not text:
        return text
    paired = _FORM_PAIR.sub(_redact_form_pair, _JSON_PAIR.sub(_redact_json_pair, text))
    return _token_pass(_redact_text(_token_pass(paired)))


def _utf8_len(text: str) -> int:
    return len(text.encode("utf-8"))


def _cut_bytes(text: str, limit: int) -> str:
    raw = text.encode("utf-8")
    if len(raw) <= limit:
        return text
    return raw[:limit].decode("utf-8", "ignore")


def _settle(text: str, step: Callable[[str], str], cut: Callable[[str], str]) -> str:
    for _ in range(_SETTLE_ROUNDS):
        nxt = cut(step(text))
        if nxt == text:
            return nxt
        text = nxt
    return text


def _markers(text: Optional[str]) -> int:
    return text.count("[REDACTED") if text else 0


def _header_text(value: Any) -> Optional[str]:
    if isinstance(value, str):
        return _clean(value)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    return None


def redact_headers(headers: Optional[Mapping[str, Any]]) -> tuple[dict[str, str], int]:
    """At most ``HEADERS_MAX`` headers (sorted by name); a denylisted / secret-suffixed name keeps its name
    and loses its value; every other value gets the text pass and the ``HEADER_VALUE_MAX`` cut."""
    if not isinstance(headers, Mapping):
        return {}, 0
    entries: list[tuple[str, str]] = []
    for raw_name, raw_value in headers.items():
        if not isinstance(raw_name, str):
            continue
        name = _clean(raw_name).strip()
        value = _header_text(raw_value)
        if not name or value is None:
            continue
        entries.append((name, value))
    entries.sort(key=lambda entry: entry[0])
    out: dict[str, str] = {}
    for name, value in entries[:HEADERS_MAX]:
        lowered = name.strip().lower().replace("_", "-")
        if lowered in _HEADER_DENY or lowered.endswith(HEADER_SECRET_SUFFIXES):
            stored = REDACTED if value else value
        else:
            stored = _settle(value, text_pass, lambda text: text[:HEADER_VALUE_MAX])
        out[name[:HEADER_NAME_MAX]] = stored
    return out, sum(_markers(value) for value in out.values())


def _redact_netloc(netloc: str) -> str:
    if "@" not in netloc:
        return netloc
    userinfo, host = netloc.rsplit("@", 1)
    if ":" in userinfo:
        return f"{userinfo.split(':', 1)[0]}:{REDACTED}@{host}"
    return f"{REDACTED}@{host}"


def _redact_query(query: str) -> str:
    if not query:
        return query
    parts: list[str] = []
    for part in query.split("&"):
        name, sep, value = part.partition("=")
        name = _token_pass(name)
        if value and unquote_plus(name).strip().lower() in _QUERY_DENY:
            parts.append(f"{name}={REDACTED}")
            continue
        if value:
            decoded = unquote_plus(value)
            redacted = text_pass(decoded)
            if redacted != decoded:
                value = quote(redacted, safe="[]")
        parts.append(f"{name}{sep}{value}")
    return "&".join(parts)


def _path_token_pass(path: str) -> str:
    """The token shapes per path SEGMENT (the generic 40+ run includes ``/``)."""
    return "/".join(_token_pass(segment) for segment in path.split("/"))


def _split_url(url: str) -> tuple[Optional[str], Optional[str], str, Optional[str]]:
    """``(scheme, netloc, path, query)`` by RFC 3986 appendix B -- never fails (``urlsplit`` raises on
    ``user:[REDACTED]@host``)."""
    match = _URL_PARTS.match(url)
    if match is None:  # pragma: no cover - the pattern matches every string
        return None, None, url, None
    scheme, netloc, path, query = match.group(1, 2, 3, 4)
    return scheme, netloc, path or "", query


def _redact_url_once(url: str) -> str:
    scheme, netloc, path, query = _split_url(url)
    out = f"{scheme}:" if scheme is not None else ""
    if netloc is not None:
        out += "//" + _redact_netloc(netloc)
    out += _path_token_pass(path)
    if query is not None:
        out += "?" + _redact_query(query)
    return out  # the fragment is dropped: never sent over HTTP


def redact_url(url: str) -> tuple[str, int]:
    """Userinfo, ``QUERY_DENYLIST`` values (other values: the text pass), token-shaped path runs →
    ``[REDACTED]``; the fragment dropped; at most ``URL_MAX`` characters."""
    text = _clean(url).strip() if isinstance(url, str) else ""
    out = _settle(text, _redact_url_once, lambda value: value[:URL_MAX])
    return out, _markers(out)


def _path_of(text: str) -> str:
    path = text.split("#", 1)[0].split("?", 1)[0]
    if "://" in path:
        path = path.split("://", 1)[1]
        path = path[path.find("/"):] if "/" in path else ""
    return path or "/"


def _finish_endpoint(path: str) -> str:
    if not path.startswith("/"):
        path = "/" + path
    return _settle(path, _path_token_pass, lambda value: value[:ENDPOINT_MAX])


def derive_endpoint(url: str) -> str:
    """The url's path with id-like segments as ``{id}`` (the server's drift key when no template is sent)."""
    path = _path_of(_clean(url).strip() if isinstance(url, str) else "")
    segments = ["{id}" if seg and any(p.fullmatch(seg) for p in _ID_SEGMENTS) else seg for seg in path.split("/")]
    return _finish_endpoint("/".join(segments))


def normalise_endpoint(text: str) -> str:
    return _finish_endpoint(_path_of(_clean(text).strip() if isinstance(text, str) else ""))


class _NotJson(ValueError):
    pass


def _refuse_constant(_text: str) -> Any:
    raise _NotJson("NaN / Infinity are not JSON")


def _finite_float(text: str) -> float:
    number = float(text)
    if not math.isfinite(number):
        raise _NotJson("a number outside the float range")
    return number


def _parse_json(text: str) -> Any:
    try:
        return json.loads(text, parse_constant=_refuse_constant, parse_float=_finite_float)
    except (ValueError, RecursionError) as exc:
        raise _NotJson(str(exc)) from None


def _walk(value: Any, depth: int) -> Any:
    if isinstance(value, (dict, list)) and depth > JSON_DEPTH_MAX:
        return REDACTED
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for raw_key, item in value.items():
            name = _clean(raw_key)
            key = text_pass(name)
            if _key_is_secret(name) and item is not None and not isinstance(item, bool):
                out[key] = REDACTED
            else:
                out[key] = _walk(item, depth + 1)
        return out
    if isinstance(value, list):
        return [_walk(item, depth + 1) for item in value]
    if isinstance(value, str):
        return text_pass(_clean(value))
    return value


def _json_type(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "string"
    return "array" if isinstance(value, list) else "object"


def _schema_of(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    schema: dict[str, str] = {}
    unnamed = 0
    for key, item in value.items():
        if key != UNNAMED_KEYS and len(schema) < SCHEMA_KEYS_MAX and _FIELD_NAME.fullmatch(key):
            schema[key] = _json_type(item)
        else:
            unnamed += 1
    if unnamed:
        schema[UNNAMED_KEYS] = str(unnamed)
    return schema


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _store_cut(text: str) -> str:
    return _cut_bytes(text, BODY_STORED_MAX_BYTES)


def _usable_size(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return 0
    return value


def _sanitised_schema(schema: Any) -> Optional[dict[str, str]]:
    if not isinstance(schema, Mapping):
        return None
    out: dict[str, str] = {}
    for key, kind in list(schema.items())[: SCHEMA_KEYS_MAX + 1]:
        if key == UNNAMED_KEYS and isinstance(kind, str) and kind.isdigit():
            out[key] = kind
        elif isinstance(key, str) and _FIELD_NAME.fullmatch(key) and kind in JSON_TYPES:
            out[key] = kind
    return out


def _already_stored(text: str, truncated: bool) -> Optional[tuple[str, int]]:
    if not truncated:
        return None
    match = _MARKER_AT_END.search(text)
    if match is None:
        return None
    kept = text[: match.start()]
    if _utf8_len(kept) > BODY_STORED_MAX_BYTES:
        return None
    return kept, int(match.group(1))


def _body(text: Optional[str], redactions: int, schema: Optional[dict], state: str, truncated: bool,
          size: int) -> dict[str, Any]:
    return {"text": text, "redactions": redactions, "schema": schema, "state": state, "truncated": truncated,
            "body_bytes": size}


def redact_message_body(text: Any, *, truncated: bool = False, body_bytes: Any = 0, schema: Any = None,
                        state: Any = None) -> dict[str, Any]:
    """The server's ``redact_message_body``: ``{text, redactions, schema, state, truncated, body_bytes}``. A stored
    copy (``truncated`` and a trailing marker) keeps its marker, schema and state; otherwise the body is fresh."""
    if not isinstance(text, str):
        return _body(None, 0, None, "empty", bool(truncated), 0)
    text = _clean(text)
    stored = _already_stored(text, bool(truncated))
    if stored is not None:
        kept, omitted = stored
        kept = _settle(kept, text_pass, _store_cut)
        carried = state if state in ("parsed", "not_json", "too_large") else "too_large"
        carried_schema = _sanitised_schema(schema) if carried == "parsed" else None
        if carried == "parsed" and carried_schema is None:
            carried = "too_large"
        size = max(_utf8_len(kept) + omitted, _usable_size(body_bytes))
        return _body(kept + TRUNCATION_MARKER.format(omitted=omitted), _markers(kept), carried_schema, carried,
                     True, size)
    received = _utf8_len(text)
    if not text.strip():
        return _body(text, 0, None, "empty", bool(truncated), max(received, _usable_size(body_bytes)))
    wire_cut = received > BODY_WIRE_MAX_BYTES
    if wire_cut:
        text = _cut_bytes(text, BODY_WIRE_MAX_BYTES)
    incomplete = wire_cut or bool(truncated)
    result_schema: Optional[dict[str, str]] = None
    if incomplete:
        result_state, full = "too_large", text_pass(text)
    else:
        try:
            walked = _walk(_parse_json(text), 1)
        except _NotJson:
            result_state, full = "not_json", text_pass(text)
        else:
            result_state, full, result_schema = "parsed", _dump(walked), _schema_of(walked)
    full_bytes = _utf8_len(full)
    size = max(received, full_bytes, _usable_size(body_bytes))
    if full_bytes <= BODY_STORED_MAX_BYTES:
        return _body(full, _markers(full), result_schema, result_state, incomplete, size)
    kept = _settle(full, text_pass, _store_cut)
    omitted = max(received, full_bytes) - _utf8_len(kept)
    return _body(kept + TRUNCATION_MARKER.format(omitted=omitted), _markers(kept), result_schema, result_state,
                 True, size)


def redact_body(text: Optional[str], *, truncated: bool = False) -> tuple[Optional[str], int, Optional[dict], str]:
    """``(text, n, schema, state)`` of one body (the server's §1.4 signature)."""
    result = redact_message_body(text, truncated=truncated)
    return result["text"], result["redactions"], result["schema"], result["state"]


def normalise_method(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    method = value.strip().upper()
    return method if _METHOD.fullmatch(method) else None


def _in_range(value: Any, low: int, high: int) -> bool:
    """``None`` or an integer in ``[low, high]`` (what the server's model accepts)."""
    return value is None or (isinstance(value, int) and not isinstance(value, bool) and low <= value <= high)


def redact_error(value: Any) -> Optional[str]:
    """A transport error's class name ("ConnectTimeout") through the text pass, at most ``ERROR_MAX`` characters."""
    if not isinstance(value, str):
        return None
    return _settle(_clean(value), text_pass, lambda text: text[:ERROR_MAX]) or None


def redact_exchange(exchange: Mapping[str, Any]) -> Optional[dict[str, Any]]:
    """One wire exchange, redacted exactly as the server's model would store it (``None`` = the server would
    drop it: no url or a method failing ``METHOD_RE``). Keys: method, url, endpoint, status, duration_ms,
    started_at, error, request / response ``{headers, body, body_truncated, body_bytes}``, response_schema,
    response_schema_state, redaction_counts."""
    method = normalise_method(exchange.get("method"))
    original_url = exchange.get("url")
    if method is None or not isinstance(original_url, str) or not original_url.strip():
        return None
    if not _in_range(exchange.get("status"), 100, 599) or not _in_range(exchange.get("duration_ms"), 0, 86_400_000):
        return None  # the server's model refuses it, so its pre-filter drops it
    url, query_count = redact_url(original_url)
    endpoint_in = exchange.get("endpoint")
    endpoint = normalise_endpoint(endpoint_in) if isinstance(endpoint_in, str) and endpoint_in.strip() \
        else derive_endpoint(original_url)
    out: dict[str, Any] = {
        "method": method, "url": url, "endpoint": endpoint,
        "status": exchange.get("status"), "duration_ms": exchange.get("duration_ms"),
        "started_at": exchange.get("started_at"), "error": redact_error(exchange.get("error")),
    }
    header_count = body_count = 0
    for side in ("request", "response"):
        message = exchange.get(side) if isinstance(exchange.get(side), Mapping) else {}
        headers, count = redact_headers(message.get("headers"))
        header_count += count
        carried = {"schema": exchange.get("response_schema"), "state": exchange.get("response_schema_state")} \
            if side == "response" else {}
        body = redact_message_body(message.get("body"), truncated=message.get("body_truncated") is True,
                                   body_bytes=message.get("body_bytes", 0), **carried)
        body_count += body["redactions"]
        out[side] = {"headers": headers, "body": body["text"], "body_truncated": body["truncated"],
                     "body_bytes": body["body_bytes"]}
        if side == "response":
            out["response_schema"], out["response_schema_state"] = body["schema"], body["state"]
    out["redaction_counts"] = {"headers": header_count, "query": query_count, "body": body_count}
    return out


# ── What the SDK sends: redacted, cut at the WIRE cap only ─────────────────────
#
# The server stores at most BODY_STORED_MAX_BYTES and computes the response schema from the full (≤ 64 KiB)
# body, so the SDK applies every redaction rule but NOT the store cut: a 20 KiB JSON answer still reaches the
# server parseable. Every rule is idempotent, so the server's own pass changes nothing the SDK already did.

_FALSEY = frozenset({"0", "false", "no", "off"})
_TRUTHY = frozenset({"1", "true", "yes", "on"})
ENV_CAPTURE = "TESTLOOKUP_CAPTURE_HTTP"
_TEXT_TYPES = ("json", "text/", "xml", "x-www-form-urlencoded", "javascript", "graphql", "yaml", "csv", "html")


def _wire_body(text: Optional[str], truncated: bool) -> tuple[Optional[str], bool]:
    if text is None:
        return None, truncated
    text = _clean(text)
    if _utf8_len(text) > BODY_WIRE_MAX_BYTES:
        text, truncated = _cut_bytes(text, BODY_WIRE_MAX_BYTES), True
    if not text.strip():
        return text, truncated
    if truncated:
        return text_pass(text), True
    try:
        walked = _walk(_parse_json(text), 1)
    except _NotJson:
        return text_pass(text), False
    return _dump(walked), False


def wire_exchange(raw: Mapping[str, Any]) -> Optional[dict[str, Any]]:
    """A captured exchange as the SDK sends it: method, redacted url, status, timing, error and both sides'
    redacted headers and bodies (``body_bytes`` = the size captured). ``None`` = unusable (the server would drop
    it)."""
    method = normalise_method(raw.get("method"))
    url = raw.get("url")
    if method is None or not isinstance(url, str) or not url.strip():
        return None
    redacted_url, _ = redact_url(url)
    # The drift key is derived from the url BEFORE redaction (as the server derives it from a raw url): a
    # token-shaped id segment reads "{id}" here, where the redacted url would only offer "[REDACTED]".
    template = raw.get("endpoint")
    endpoint = normalise_endpoint(template) if isinstance(template, str) and template.strip() else derive_endpoint(url)
    out: dict[str, Any] = {"method": method, "url": redacted_url, "endpoint": endpoint}
    for key in ("status", "duration_ms", "started_at"):
        if raw.get(key) is not None:
            out[key] = raw[key]
    error = redact_error(raw.get("error"))
    if error is not None:
        out["error"] = error
    for side in ("request", "response"):
        message = raw.get(side) if isinstance(raw.get(side), Mapping) else {}
        headers, _ = redact_headers(message.get("headers"))
        raw_body = message.get("body") if isinstance(message.get("body"), str) else None
        body, truncated = _wire_body(raw_body, message.get("body_truncated") is True)
        # The size BEFORE the SDK touched it (the server stores max(this, what it receives) as body_bytes).
        size = max(_usable_size(message.get("body_bytes")), _utf8_len(_clean(raw_body)) if raw_body else 0)
        out[side] = {"headers": headers, "body": body, "body_truncated": truncated, "body_bytes": size}
    return out


def capture_enabled(flag: Any = False, *, env: Optional[Mapping[str, str]] = None, config_value: Any = None) -> bool:
    """Capture is OFF unless asked: the pytest flag > ``TESTLOOKUP_CAPTURE_HTTP`` > the config value."""
    if flag is True:
        return True
    raw_env = (env or {}).get(ENV_CAPTURE)
    if isinstance(raw_env, str) and raw_env.strip():
        lowered = raw_env.strip().lower()
        if lowered in _TRUTHY:
            return True
        if lowered in _FALSEY:
            return False
    if isinstance(config_value, bool):
        return config_value
    return isinstance(config_value, str) and config_value.strip().lower() in _TRUTHY


def _textual(content_type: Optional[str]) -> bool:
    if not content_type:
        return True
    lowered = content_type.lower()
    return any(kind in lowered for kind in _TEXT_TYPES)


def _body_of(raw: Any, content_type: Optional[str]) -> tuple[Optional[str], bool, int]:
    """(text, cut, byte size): bytes decoded UTF-8 with ``errors="replace"``, cut at the wire cap first; a
    non-text content type, a stream or an unknown object → ``(None, False, 0)``."""
    if raw is None or not _textual(content_type):
        return None, False, 0
    if isinstance(raw, str):
        raw = raw.encode("utf-8", "replace")
    if not isinstance(raw, (bytes, bytearray)):
        return None, False, 0
    size = len(raw)
    cut = size > BODY_WIRE_MAX_BYTES
    return bytes(raw[:BODY_WIRE_MAX_BYTES]).decode("utf-8", "replace"), cut, size


def _header(headers: Mapping[str, Any], name: str) -> Optional[str]:
    for key, value in headers.items():
        if isinstance(key, str) and key.lower() == name:
            return str(value)
    return None


def _message(headers: Any, raw_body: Any) -> dict[str, Any]:
    plain = {str(k): str(v) for k, v in dict(headers or {}).items()}
    body, cut, size = _body_of(raw_body, _header(plain, "content-type"))
    return {"headers": plain, "body": body, "body_truncated": cut, "body_bytes": size}


def _httpx_request_body(request: Any) -> Any:
    try:
        return request.content          # a streamed request body raises httpx.RequestNotRead
    except Exception:  # noqa: BLE001
        return None


class HttpCapture:
    """Wraps ``requests.Session.send``, ``httpx.Client.send`` and ``httpx.AsyncClient.send`` (those importable)
    while installed, and records -- REDACTED -- the exchanges made between ``start(key)`` and ``stop()``.

    ``exclude`` lists client objects never captured (the SDK's own ``httpx.AsyncClient``), compared by
    identity. At most ``EXCHANGES_PER_TEST_MAX`` are kept per test; the rest are counted in ``dropped``."""

    def __init__(self, *, exclude: Iterable[object] = ()) -> None:
        self._exclude = list(exclude)
        self._lock = threading.Lock()
        self._active: Optional[str] = None
        self._buffer: list[dict[str, Any]] = []
        self.dropped = 0
        self._patched: list[tuple[type, str, Any]] = []

    @property
    def installed(self) -> bool:
        return bool(self._patched)

    def exclude(self, client: object) -> None:
        self._exclude.append(client)

    # ── install / uninstall ──
    def install(self) -> None:
        if self._patched:
            return
        try:
            import requests  # type: ignore[import-untyped]
        except ImportError:
            requests = None
        if requests is not None:
            self._patch(requests.Session, "send", self._sync_wrapper, "requests")
        try:
            import httpx
        except ImportError:
            httpx = None
        if httpx is not None:
            self._patch(httpx.Client, "send", self._sync_wrapper, "httpx")
            self._patch(httpx.AsyncClient, "send", self._async_wrapper, "httpx")

    def uninstall(self) -> None:
        for owner, name, original in reversed(self._patched):
            current = owner.__dict__.get(name)
            if getattr(current, "_testlookup_capture", None) is self:
                setattr(owner, name, original)
        self._patched.clear()

    def _patch(self, cls: type, name: str, factory: Callable[..., Any], flavour: str) -> None:
        # The class that DEFINES the method (a subclass someone installed in its place inherits it).
        owner = next((klass for klass in cls.__mro__ if name in klass.__dict__), None)
        if owner is None or any(owner is done and name == done_name for done, done_name, _ in self._patched):
            return
        original = owner.__dict__[name]
        wrapper = factory(original, flavour)
        wrapper._testlookup_capture = self
        setattr(owner, name, wrapper)
        self._patched.append((owner, name, original))

    # ── per test ──
    def start(self, key: str) -> None:
        with self._lock:
            self._active, self._buffer, self.dropped = key, [], 0

    def stop(self) -> list[dict[str, Any]]:
        with self._lock:
            kept, self._active, self._buffer = self._buffer, None, []
        return kept

    def _wanted(self, client: object) -> bool:
        return self._active is not None and not any(client is excluded for excluded in self._exclude)

    def _record(self, build: Callable[[], dict[str, Any]]) -> None:
        """Build, redact and buffer one exchange; any error is swallowed (capture never disturbs a test)."""
        try:
            exchange = wire_exchange(build())
        except Exception as exc:  # noqa: BLE001
            logger.debug("HTTP capture skipped one exchange (%s)", type(exc).__name__)
            return
        if exchange is None:
            return
        with self._lock:
            if self._active is None:
                return
            if len(self._buffer) >= EXCHANGES_PER_TEST_MAX:
                self.dropped += 1
            else:
                self._buffer.append(exchange)

    @staticmethod
    def _builder(flavour: str, request: Any, response: Any, stream: bool, started: float, t0: float,
                 error: Optional[BaseException]) -> Callable[[], dict[str, Any]]:
        def build() -> dict[str, Any]:
            if flavour == "requests":
                request_body = request.body
            else:
                request_body = _httpx_request_body(request)
            exchange: dict[str, Any] = {
                "method": str(request.method),
                "url": str(request.url),
                "duration_ms": max(0, int((time.monotonic() - t0) * 1000)),
                # ISO-8601 UTC with a Z suffix (the vector file's wire convention), millisecond precision.
                "started_at": datetime.fromtimestamp(started, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3]
                + "Z",
                "request": _message(request.headers, request_body),
                "response": {"headers": {}, "body": None, "body_truncated": False, "body_bytes": 0},
            }
            if error is not None:
                exchange["error"] = type(error).__name__[:200]
            if response is not None:
                exchange["status"] = int(response.status_code)
                # A streamed response is never read: its body is not captured (the test owns the stream).
                exchange["response"] = _message(response.headers, None if stream else response.content)
            return exchange

        return build

    def _sync_wrapper(self, original: Callable[..., Any], flavour: str) -> Callable[..., Any]:
        capture = self

        def send(client: Any, request: Any, *args: Any, **kwargs: Any) -> Any:
            if not capture._wanted(client):
                return original(client, request, *args, **kwargs)
            stream = bool(kwargs.get("stream", False))
            started, t0 = time.time(), time.monotonic()
            try:
                response = original(client, request, *args, **kwargs)
            except BaseException as exc:
                capture._record(capture._builder(flavour, request, None, stream, started, t0, exc))
                raise
            capture._record(capture._builder(flavour, request, response, stream, started, t0, None))
            return response

        return send

    def _async_wrapper(self, original: Callable[..., Any], flavour: str) -> Callable[..., Any]:
        capture = self

        async def send(client: Any, request: Any, *args: Any, **kwargs: Any) -> Any:
            if not capture._wanted(client):
                return await original(client, request, *args, **kwargs)
            stream = bool(kwargs.get("stream", False))
            started, t0 = time.time(), time.monotonic()
            try:
                response = await original(client, request, *args, **kwargs)
            except BaseException as exc:
                capture._record(capture._builder(flavour, request, None, stream, started, t0, exc))
                raise
            capture._record(capture._builder(flavour, request, response, stream, started, t0, None))
            return response

        return send
