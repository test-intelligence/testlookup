"""The ONE server rule for a captured HTTP exchange (W23d AGT-14, contract §1.2 / §1.4).

A captured exchange is attacker-controlled input from an API key. This module cuts
it to the caps of ``http_exchange_vocab`` and redacts it; ``schemas.HttpExchangeIn``
calls :func:`redact_exchange` from its after-validator, so every validated copy —
the object-storage batch, the Celery payload, the Mongo document, the GET answer,
the export member — holds the redacted form only. The store re-validates (a
second pass changes nothing: every step is idempotent, tested on every vector),
and the Python SDK ports the same rules (``client/http_capture.py``).

The rules (``sdk-vectors/http_exchange.json`` is the cross-language specification;
its ``conventions`` say the same in prose):

* **text pass** (header values, query values, JSON string leaves, non-JSON
  bodies): every ``run_context_vocab.TOKEN_SHAPES`` run → ``[REDACTED]``, then
  ``redaction_service.redact_text`` (credentials, JWTs, e-mails, phone / card /
  IPv4 shapes), then the token shapes again — W23c's ``untrusted_input.scrub``
  order, without its prompt-only steps.
* **headers**: at most ``HEADERS_MAX`` per side (sorted by name); a name in
  ``HEADER_DENYLIST`` or ENDING in a ``HEADER_SECRET_SUFFIXES`` entry (compared
  lower-case, ``_`` read as ``-``) keeps its name and loses its value; every other
  value gets the text pass, then the ``HEADER_VALUE_MAX`` cut.
* **url**: the userinfo password (or a bare userinfo, a token's usual place) →
  ``[REDACTED]``; path segments get the token shapes; a ``QUERY_DENYLIST`` name's
  value → ``[REDACTED]``, every other query value the text pass; the fragment is
  dropped (never sent over HTTP); ``URL_MAX`` cut.
* **bodies**: cut to ``BODY_WIRE_MAX_BYTES`` first (state ``too_large``); JSON →
  a walk (a key named like a secret loses its value; every string leaf gets the
  text pass; lists walked IN FULL — not ``redact_dict``'s 100-item cut; a
  container nested deeper than 10 levels → ``[REDACTED]``) re-serialised compact;
  otherwise the text pass; then ``BODY_STORED_MAX_BYTES`` with ``TRUNCATION_MARKER``.

Every cut is SETTLED: ``x = cut(redact(x))`` is repeated until it stops changing,
so a cut can never leave a fragment a second pass would rewrite. Lengths: header
and URL caps count characters, body caps count UTF-8 bytes.

PURE: no I/O, no session, no logging of content.
"""
from __future__ import annotations

import json
import math
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Optional
from urllib.parse import quote, unquote_plus

from app.services import http_exchange_vocab as vocab
from app.services.redaction_service import REDACTED, SENSITIVE_KEYS, is_log_secret_name, redact_text
from app.services.run_context_vocab import TOKEN_SHAPES

if TYPE_CHECKING:  # pragma: no cover - typing only (schemas imports this module lazily)
    from app.models.schemas import HttpExchangeIn

#: The response-schema entry that counts the top-level keys NOT stored as a name
#: (outside ``FIELD_NAME_RE``, beyond ``SCHEMA_KEYS_MAX``, or this name itself).
UNNAMED_KEYS = "_unnamed_keys"
#: JSON nesting kept: the top-level value is depth 1; an object / array at depth
#: ``JSON_DEPTH_MAX + 1`` or deeper is replaced by ``[REDACTED]`` (fail closed).
JSON_DEPTH_MAX = 10
#: The JSON type names a response schema records.
JSON_TYPES = ("string", "number", "boolean", "object", "array", "null")
#: ``HttpExchangeIn.error`` (the transport error's class name) is kept to this many characters.
ERROR_MAX = 200

_TOKEN_SHAPE_RES = tuple(re.compile(shape) for shape in TOKEN_SHAPES)
_METHOD = re.compile(vocab.METHOD_RE)
_FIELD_NAME = re.compile(vocab.FIELD_NAME_RE)
_ID_SEGMENTS = tuple(re.compile(pattern) for pattern in vocab.ENDPOINT_ID_SEGMENT_RES)
_HEADER_DENY = frozenset(vocab.HEADER_DENYLIST)
_QUERY_DENY = frozenset(vocab.QUERY_DENYLIST)
_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_UNUSABLE_CHARS = re.compile("[\x00\ud800-\udfff]")      # NUL and lone surrogates (Mongo / UTF-8 cannot hold them)
_MARKER_HEAD, _MARKER_TAIL = vocab.TRUNCATION_MARKER.split("{omitted}")
_MARKER_AT_END = re.compile(re.escape(_MARKER_HEAD) + r"(\d{1,15})" + re.escape(_MARKER_TAIL) + r"\Z")
_SETTLE_ROUNDS = 6
#: ``"key": "value"`` / ``"key": 123`` inside text (an unterminated string runs to the end of the text).
JSON_PAIR_RE = r'"((?:[^"\\\r\n]|\\.){1,128})"(\s*:\s*)(?:"(?:[^"\\]|\\.)*(?:"|$(?![\s\S]))|-?[0-9][0-9.eE+-]*)'
#: ``key=value`` inside text (form bodies, log lines, a url quoted in text).
FORM_PAIR_RE = r"(?<![A-Za-z0-9_.-])([A-Za-z][A-Za-z0-9_.-]{0,127})=([^&\s\"',;]+)"
_JSON_PAIR = re.compile(JSON_PAIR_RE)
_FORM_PAIR = re.compile(FORM_PAIR_RE)
#: RFC 3986 appendix B: scheme, authority, path, query (the fragment is matched and dropped).
URL_PARTS_RE = r"\A(?:([A-Za-z][A-Za-z0-9+.-]*):)?(?://([^/?#]*))?([^?#]*)(?:\?([^#]*))?(?:#[\s\S]*)?\Z"
_URL_PARTS = re.compile(URL_PARTS_RE)


# ── small pure helpers ───────────────────────────────────────────────────────

def _clean(text: str) -> str:
    """Text a UTF-8 store accepts: NUL dropped, a lone surrogate → U+FFFD."""
    if not _UNUSABLE_CHARS.search(text):
        return text
    return _UNUSABLE_CHARS.sub(lambda m: "" if m.group(0) == "\x00" else "�", text)


def _token_pass(text: str) -> str:
    for shape in _TOKEN_SHAPE_RES:
        text = shape.sub(REDACTED, text)
    return text


def _path_token_pass(path: str) -> str:
    """The token shapes per path SEGMENT: the generic 40+ run includes ``/``, so on a whole path it would
    swallow every ordinary path of 40 characters (``/api/products/123/reviews/…``)."""
    return "/".join(_token_pass(segment) for segment in path.split("/"))


def _key_is_secret(key: str) -> bool:
    """A JSON / form key named like a secret: ``redaction_service.SENSITIVE_KEYS`` or its log rule
    (``is_log_secret_name``: exact names + the ``_token`` / ``_secret`` … suffixes), on the key
    lower-cased with ``-`` read as ``_``, and on its camelCase split (``apiKey`` → ``api_key``)."""
    plain = key.lower().replace("-", "_")
    snake = _CAMEL_BOUNDARY.sub("_", key).lower().replace("-", "_")
    return any(name in SENSITIVE_KEYS or is_log_secret_name(name) for name in (plain, snake))


def _redact_json_pair(match: re.Match[str]) -> str:
    if not _key_is_secret(match.group(1)):
        return match.group(0)
    return f'"{match.group(1)}"{match.group(2)}"{REDACTED}"'


def _redact_form_pair(match: re.Match[str]) -> str:
    if not _key_is_secret(match.group(1)):
        return match.group(0)
    return f"{match.group(1)}={REDACTED}"


def _pair_pass(text: str) -> str:
    """Secret-named pairs inside TEXT: ``"password": "…"`` (a JSON fragment — a body that does not
    parse, or the store-cut tail of one that did) and ``client_secret=…`` (form / log style) — the
    values ``redact_text``'s own rules miss."""
    return _FORM_PAIR.sub(_redact_form_pair, _JSON_PAIR.sub(_redact_json_pair, text))


def text_pass(text: str) -> str:
    """Secret-named pairs → token shapes → ``redact_text`` → token shapes. Idempotent."""
    if not text:
        return text
    return _token_pass(redact_text(_token_pass(_pair_pass(text))))


def _utf8_len(text: str) -> int:
    return len(text.encode("utf-8"))


def _cut_bytes(text: str, limit: int) -> str:
    """At most ``limit`` UTF-8 bytes, never half a character."""
    raw = text.encode("utf-8")
    if len(raw) <= limit:
        return text
    return raw[:limit].decode("utf-8", "ignore")


def _settle(text: str, step: Callable[[str], str], cut: Callable[[str], str]) -> str:
    """``x = cut(step(x))`` until ``x`` stops changing — the cut-then-redact fixed point."""
    for _ in range(_SETTLE_ROUNDS):
        nxt = cut(step(text))
        if nxt == text:
            return nxt
        text = nxt
    return text


def _markers(text: Optional[str]) -> int:
    return text.count("[REDACTED") if text else 0


# ── headers ──────────────────────────────────────────────────────────────────

def _header_name_is_secret(name: str) -> bool:
    lowered = name.strip().lower().replace("_", "-")
    return lowered in _HEADER_DENY or lowered.endswith(vocab.HEADER_SECRET_SUFFIXES)


def _header_text(value: Any) -> Optional[str]:
    """A header value as text: a string as is, a number as its decimal text; anything else is dropped."""
    if isinstance(value, str):
        return _clean(value)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    return None


def redact_headers(headers: Mapping[str, Any] | None) -> tuple[dict[str, str], int]:
    """At most ``HEADERS_MAX`` headers (sorted by name); denylisted / secret-suffixed names keep the
    name and lose the value; every other value gets the text pass and the ``HEADER_VALUE_MAX`` cut.
    Returns ``(headers, n)`` where ``n`` counts the ``[REDACTED`` markers in the values."""
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
    for name, value in entries[: vocab.HEADERS_MAX]:
        if _header_name_is_secret(name):
            stored = REDACTED if value else value
        else:
            stored = _settle(value, text_pass, lambda text: text[: vocab.HEADER_VALUE_MAX])
        out[name[: vocab.HEADER_NAME_MAX]] = stored
    return out, sum(_markers(value) for value in out.values())


# ── url and endpoint ─────────────────────────────────────────────────────────

def _redact_netloc(netloc: str) -> str:
    if "@" not in netloc:
        return netloc
    userinfo, host = netloc.rsplit("@", 1)
    if ":" in userinfo:
        user = userinfo.split(":", 1)[0]
        return f"{user}:{REDACTED}@{host}"
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


def _split_url(url: str) -> tuple[Optional[str], Optional[str], str, Optional[str]]:
    """``(scheme, netloc, path, query)`` by RFC 3986 appendix B — it never fails (``urlsplit`` raises on
    ``user:[REDACTED]@host``, which would send a stored url down a different path on re-validation)."""
    match = _URL_PARTS.match(url)
    if match is None:  # pragma: no cover - the pattern matches every string
        return None, None, url, None
    scheme, netloc, path, query = match.group(1, 2, 3, 4)
    return scheme, netloc, path or "", query


def url_host(url: str) -> Optional[str]:
    """The lower-cased host of a (redacted) url, without userinfo or port; ``None`` when there is none."""
    _scheme, netloc, _path, _query = _split_url(url if isinstance(url, str) else "")
    if not netloc:
        return None
    host = netloc.rsplit("@", 1)[-1]
    if host.startswith("["):
        host = host[: host.find("]") + 1] if "]" in host else host
    else:
        host = host.split(":", 1)[0]
    return host.lower() or None


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
    """The userinfo, the query values of ``QUERY_DENYLIST`` names (every other value: the text pass),
    token-shaped path runs → ``[REDACTED]``; the fragment dropped; cut to ``URL_MAX`` characters.
    Returns ``(url, n)`` where ``n`` counts the ``[REDACTED`` markers in the url."""
    text = _clean(url).strip() if isinstance(url, str) else ""
    out = _settle(text, _redact_url_once, lambda value: value[: vocab.URL_MAX])
    return out, _markers(out)


def _path_of(text: str) -> str:
    """The path of an absolute url (``scheme://host/path?query``) or of a bare path / template."""
    path = text.split("#", 1)[0].split("?", 1)[0]
    if "://" in path:
        path = path.split("://", 1)[1]
        path = path[path.find("/"):] if "/" in path else ""
    return path or "/"


def _finish_endpoint(path: str) -> str:
    if not path.startswith("/"):
        path = "/" + path
    return _settle(path, _path_token_pass, lambda value: value[: vocab.ENDPOINT_MAX])


def derive_endpoint(url: str) -> str:
    """The drift key when the SDK sent no template: the url's path, each segment matching an
    ``ENDPOINT_ID_SEGMENT_RES`` pattern → ``{id}``, token shapes → ``[REDACTED]``, ≤ ``ENDPOINT_MAX``."""
    path = _path_of(_clean(url).strip() if isinstance(url, str) else "")
    segments = [
        "{id}" if segment and any(pattern.fullmatch(segment) for pattern in _ID_SEGMENTS) else segment
        for segment in path.split("/")
    ]
    return _finish_endpoint("/".join(segments))


def normalise_endpoint(text: str) -> str:
    """An SDK-sent template (``/api/products/{id}``): scheme, host, query and fragment stripped,
    token shapes → ``[REDACTED]``, ≤ ``ENDPOINT_MAX``. Its segments are kept as sent."""
    return _finish_endpoint(_path_of(_clean(text).strip() if isinstance(text, str) else ""))


# ── bodies ───────────────────────────────────────────────────────────────────

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
    """The parsed body, or ``_NotJson`` (Python's NaN / Infinity / 1e999 extensions refused: other
    languages' parsers reject them, and a re-serialised ``Infinity`` would not parse again)."""
    try:
        return json.loads(text, parse_constant=_refuse_constant, parse_float=_finite_float)
    except (ValueError, RecursionError) as exc:
        raise _NotJson(str(exc)) from None


def _walk(value: Any, depth: int) -> Any:
    """A parsed JSON value, redacted. ``depth`` of the top-level value is 1."""
    if isinstance(value, (dict, list)) and depth > JSON_DEPTH_MAX:
        return REDACTED
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for raw_key, item in value.items():
            name = _clean(raw_key)
            key = text_pass(name)  # a key can carry an address or a token too
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
    """Top-level key → JSON type of a parsed, redacted body (``{}`` for a non-object body)."""
    if not isinstance(value, dict):
        return {}
    schema: dict[str, str] = {}
    unnamed = 0
    for key, item in value.items():
        if key != UNNAMED_KEYS and len(schema) < vocab.SCHEMA_KEYS_MAX and _FIELD_NAME.fullmatch(key):
            schema[key] = _json_type(item)
        else:
            unnamed += 1
    if unnamed:
        schema[UNNAMED_KEYS] = str(unnamed)
    return schema


def _sanitised_schema(schema: Any) -> Optional[dict[str, str]]:
    """A schema carried by an already-redacted body (a re-validation): only safe names and types."""
    if not isinstance(schema, Mapping):
        return None
    out: dict[str, str] = {}
    for key, kind in list(schema.items())[: vocab.SCHEMA_KEYS_MAX + 1]:
        if key == UNNAMED_KEYS and isinstance(kind, str) and kind.isdigit():
            out[key] = kind
        elif isinstance(key, str) and _FIELD_NAME.fullmatch(key) and kind in JSON_TYPES:
            out[key] = kind
    return out


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _store_cut(text: str) -> str:
    return _cut_bytes(text, vocab.BODY_STORED_MAX_BYTES)


def _marker(omitted: int) -> str:
    return vocab.TRUNCATION_MARKER.format(omitted=omitted)


@dataclass(frozen=True)
class BodyResult:
    """One redacted body: what is stored, and what the exchange records about it."""

    text: Optional[str]
    redactions: int                     # "[REDACTED" markers in the stored text
    schema: Optional[dict[str, str]]    # top-level key → JSON type (state "parsed" only)
    state: str                          # SCHEMA_STATES
    truncated: bool                     # incomplete: cut by the SDK, at the wire cap or at the store cap
    body_bytes: int                     # UTF-8 size before the store cut (see redact_message_body)


def _already_stored(text: str, truncated: bool) -> Optional[tuple[str, int]]:
    """``(kept, omitted)`` when ``text`` is this module's own store-cut output (a re-validation)."""
    if not truncated:
        return None
    match = _MARKER_AT_END.search(text)
    if match is None:
        return None
    kept = text[: match.start()]
    if _utf8_len(kept) > vocab.BODY_STORED_MAX_BYTES:
        return None
    return kept, int(match.group(1))


def redact_message_body(
    text: Any,
    *,
    truncated: bool = False,
    body_bytes: int = 0,
    schema: Any = None,
    state: Any = None,
) -> BodyResult:
    """The body rule (module docstring). ``truncated`` / ``body_bytes`` / ``schema`` / ``state`` are what
    the message already says (the SDK's own cut, or a stored copy being re-validated):

    * a stored copy (``truncated`` and a trailing ``TRUNCATION_MARKER``, kept part ≤ the store cap)
      keeps its marker, its schema (names / types re-checked) and its state — its JSON can no longer
      be parsed — and its kept text is settled again (a no-op for this module's own output);
    * otherwise the body is fresh: ``body_bytes`` = the largest of the size received, the size after
      redaction and the size the SDK reported for a body it cut.
    """
    if not isinstance(text, str):
        return BodyResult(None, 0, None, "empty", bool(truncated), 0)
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
        return BodyResult(kept + _marker(omitted), _markers(kept), carried_schema, carried, True, size)
    received = _utf8_len(text)
    if not text.strip():
        return BodyResult(text, 0, None, "empty", bool(truncated), max(received, _usable_size(body_bytes)))
    wire_cut = received > vocab.BODY_WIRE_MAX_BYTES
    if wire_cut:
        text = _cut_bytes(text, vocab.BODY_WIRE_MAX_BYTES)
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
            result_state, full = "parsed", _dump(walked)
            result_schema = _schema_of(walked)
    full_bytes = _utf8_len(full)
    size = max(received, full_bytes, _usable_size(body_bytes))
    if full_bytes <= vocab.BODY_STORED_MAX_BYTES:
        return BodyResult(full, _markers(full), result_schema, result_state, incomplete, size)
    kept = _settle(full, text_pass, _store_cut)
    omitted = max(received, full_bytes) - _utf8_len(kept)
    return BodyResult(kept + _marker(omitted), _markers(kept), result_schema, result_state, True, size)


def _usable_size(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return 0
    return value


def redact_body(text: str | None, *, truncated: bool = False) -> tuple[str | None, int, dict[str, str] | None, str]:
    """``(text, n, schema, state)`` of one fresh body — the contract's §1.4 signature over
    :func:`redact_message_body`."""
    result = redact_message_body(text, truncated=truncated)
    return result.text, result.redactions, result.schema, result.state


# ── the exchange ─────────────────────────────────────────────────────────────

def normalise_method(value: Any) -> Optional[str]:
    """The method upper-cased, or ``None`` when it fails ``METHOD_RE`` (the exchange is then dropped)."""
    if not isinstance(value, str):
        return None
    method = value.strip().upper()
    return method if _METHOD.fullmatch(method) else None


def _usable_item(item: Any) -> Optional[dict[str, Any]]:
    if not isinstance(item, Mapping) and hasattr(item, "model_dump"):
        item = item.model_dump()
    if not isinstance(item, Mapping):
        return None
    url = item.get("url")
    if not isinstance(url, str) or not url.strip() or normalise_method(item.get("method")) is None:
        return None
    return dict(item)


def prefilter_exchanges(
    raw: Any, *, validate: Optional[Callable[[dict[str, Any]], Any]] = None,
) -> tuple[list[Any], int]:
    """``(kept ≤ EXCHANGES_PER_TEST_MAX, dropped)``. Non-list → ``([], 0)``. An item that is not an
    object, has no non-blank string ``url`` or a method failing ``METHOD_RE`` after upper-casing is
    dropped; usable items beyond the per-test cap are dropped too (counted, never a 422).

    ``validate`` (the server passes ``HttpExchangeIn.model_validate``) turns each usable item into its
    validated form; an item it refuses (a ``ValueError`` — pydantic's ``ValidationError`` is one, e.g.
    a status of 700) is dropped and counted the same way, so one bad exchange never costs the batch."""
    if not isinstance(raw, list):
        return [], 0
    kept: list[Any] = []
    dropped = 0
    for item in raw:
        usable: Any = _usable_item(item)
        if usable is not None and validate is not None and len(kept) < vocab.EXCHANGES_PER_TEST_MAX:
            try:
                usable = validate(usable)
            except ValueError:
                usable = None
        if usable is None or len(kept) >= vocab.EXCHANGES_PER_TEST_MAX:
            dropped += 1
            continue
        kept.append(usable)
    return kept, dropped


def redact_exchange(ex: "HttpExchangeIn") -> None:
    """The after-validator body (§1.2): url → endpoint → headers → bodies → the response schema and
    the redaction counts, all recomputed (a client-sent schema is only carried by a stored copy whose
    body can no longer be parsed, and only its safe names). Mutates ``ex``; idempotent."""
    original_url = ex.url
    ex.url, query_count = redact_url(original_url)
    endpoint = ex.endpoint.strip() if isinstance(ex.endpoint, str) else ""
    ex.endpoint = normalise_endpoint(endpoint) if endpoint else derive_endpoint(original_url)
    header_count = 0
    for message in (ex.request, ex.response):
        message.headers, count = redact_headers(message.headers)
        header_count += count
    request = redact_message_body(
        ex.request.body, truncated=ex.request.body_truncated, body_bytes=ex.request.body_bytes,
    )
    response = redact_message_body(
        ex.response.body, truncated=ex.response.body_truncated, body_bytes=ex.response.body_bytes,
        schema=ex.response_schema, state=ex.response_schema_state,
    )
    for message, result in ((ex.request, request), (ex.response, response)):
        message.body = result.text
        message.body_truncated = result.truncated
        message.body_bytes = result.body_bytes
    ex.response_schema = response.schema
    ex.response_schema_state = response.state
    if isinstance(ex.error, str):
        # A transport error's class name ("ConnectTimeout"); a client may put a url or a token in it.
        ex.error = _settle(_clean(ex.error), text_pass, lambda value: value[:ERROR_MAX]) or None
    ex.redaction_counts = {
        "headers": header_count,
        "query": query_count,
        "body": request.redactions + response.redactions,
    }


__all__ = [
    "BodyResult",
    "ERROR_MAX",
    "FORM_PAIR_RE",
    "JSON_DEPTH_MAX",
    "JSON_PAIR_RE",
    "JSON_TYPES",
    "UNNAMED_KEYS",
    "URL_PARTS_RE",
    "derive_endpoint",
    "normalise_endpoint",
    "normalise_method",
    "prefilter_exchanges",
    "redact_body",
    "redact_exchange",
    "redact_headers",
    "redact_message_body",
    "redact_url",
    "text_pass",
    "url_host",
]
