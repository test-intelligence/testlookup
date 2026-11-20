"""Request hardening at the ASGI layer (Trivy findings on PR #26).

Two starlette advisories were reachable while ``fastapi==0.115.5`` pinned
starlette below 0.42. W33 moved to starlette 0.52.1, which fixed the Range
one, and the 2026-11-12 supply-chain PR to 1.10.0, which fixes the form one
(1.3.1). Both guards stay as defence in depth: the 64 KiB body cap is far
below starlette 1.10's urlencoded limits (1000 fields of up to 1 MB each),
and refusing byte ranges costs nothing:

* CVE-2026-54283: ``request.form()`` ignores its limits for
  ``application/x-www-form-urlencoded`` bodies, so a client can make the
  server buffer and parse an arbitrarily large form. ``POST
  /api/v1/auth/login`` takes exactly that body (OAuth2PasswordRequestForm)
  without authentication. :class:`FormBodyLimitMiddleware` refuses such a
  body over a small cap with 413 before any handler or ``request.form()``
  reads it: by ``Content-Length`` when present, and by counting the streamed
  bytes when it is not (chunked). Multipart uploads and JSON are untouched.
* CVE-2025-62727: DoS through ``Range`` handling in ``FileResponse``. The
  unauthenticated SDK download (``GET /api/v1/sdk/{lang}``) returns a
  FileResponse. Nothing in the app serves byte ranges on purpose, so
  :class:`RangeHeaderStripMiddleware` drops ``Range``/``If-Range`` from every
  request and FileResponse always sends the whole file.

Two more guards came from the 2026-11-18 security audit. Both exist because
some deployments route ``/api/`` from the load balancer straight to the
backend, past the frontend's nginx and whatever it enforces:

* :class:`RequestBodyLimitMiddleware` bounds the request body of EVERY content
  type while it is read. Ordinary routes get ``REQUEST_BODY_MAX_BYTES``; the
  routes that carry test results or an archive (:func:`is_large_body_route`)
  get ``REQUEST_BODY_UPLOAD_MAX_BYTES`` and keep their own, lower, checks.
* :class:`StrictTransportSecurityMiddleware` adds the same
  ``Strict-Transport-Security`` header the frontend's nginx sends, to
  responses of requests that arrived over https.
"""

from __future__ import annotations

import re

from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import JSONResponse

# The exact binding starlette's Request.form() uses to pick the urlencoded
# parser (None when python-multipart is absent, and then starlette parses no
# forms either).
from starlette.requests import parse_options_header
from starlette.types import ASGIApp, Message, Receive, Scope, Send

FORM_MEDIA_TYPE = b"application/x-www-form-urlencoded"
_RANGE_HEADERS = (b"range", b"if-range")


def _header(scope: Scope, name: bytes) -> bytes | None:
    for key, value in scope.get("headers", []):
        if key.lower() == name:
            return bytes(value)
    return None


def _content_length(scope: Scope) -> int | None:
    raw = _header(scope, b"content-length")
    if raw is None:
        return None
    try:
        parsed = int(raw)
    except ValueError:
        return None
    return parsed if parsed >= 0 else None


class FormBodyLimitMiddleware:
    """413 for an urlencoded form body over ``max_bytes`` (CVE-2026-54283)."""

    def __init__(self, app: ASGIApp, max_bytes: int, path_limits: dict[str, int] | None = None):
        self.app = app
        self.max_bytes = max_bytes
        # Exact-path overrides: the SAML ACS (POST binding) legitimately carries
        # a large signed SAMLResponse, bounded by its own route check.
        self.path_limits = dict(path_limits or {})

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not self._is_form(scope):
            await self.app(scope, receive, send)
            return

        limit = self.path_limits.get(scope.get("path", ""), self.max_bytes)
        declared = _content_length(scope)
        if declared is not None and declared > limit:
            await self._reject(limit, scope, receive, send)
            return

        # No (usable) Content-Length -- chunked, or lying: count what arrives
        # and stop reading at the cap. Only up to max_bytes is ever buffered.
        buffered: list[Message] = []
        total = 0
        while True:
            message = await receive()
            buffered.append(message)
            if message["type"] == "http.request":
                total += len(message.get("body", b""))
                if total > limit:
                    await self._reject(limit, scope, receive, send)
                    return
                if not message.get("more_body", False):
                    break
            elif message["type"] == "http.disconnect":
                break

        index = 0

        async def replay() -> Message:
            nonlocal index
            if index < len(buffered):
                message = buffered[index]
                index += 1
                return message
            return await receive()

        await self.app(scope, replay, send)

    @staticmethod
    def _is_form(scope: Scope) -> bool:
        content_type = _header(scope, b"content-type") or b""
        media = content_type.split(b";", 1)[0].strip().lower()
        if media == FORM_MEDIA_TYPE:
            return True
        # Also ask starlette's own classifier (R-B45-T-1). bytes.strip() only
        # removes ASCII whitespace, but parse_options_header also strips the
        # latin-1 bytes \xa0 and \x85: 'application/x-www-form-urlencoded\xa0'
        # was not a form here yet starlette parsed the whole body as one --
        # 1 MiB reached POST /api/v1/auth/login and its handler ran.
        if parse_options_header is not None:
            parsed, _ = parse_options_header(content_type.decode("latin-1"))
            return bool(parsed == FORM_MEDIA_TYPE)
        return False

    @staticmethod
    async def _reject(limit: int, scope: Scope, receive: Receive, send: Send) -> None:
        response = JSONResponse(
            status_code=413,
            content={"detail": f"Form body exceeds {limit} bytes"},
        )
        await response(scope, receive, send)


class RangeHeaderStripMiddleware:
    """Drop ``Range``/``If-Range`` so FileResponse never parses ranges (CVE-2025-62727)."""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            headers = scope.get("headers", [])
            kept = [(k, v) for k, v in headers if k.lower() not in _RANGE_HEADERS]
            if len(kept) != len(headers):
                scope = dict(scope)
                scope["headers"] = kept
        await self.app(scope, receive, send)


# ── A body limit for every content type ─────────────────────────────────────

#: Routes whose body is test output or an archive, so its size follows the
#: caller's test suite rather than a form: the JSON batch and report-file
#: ingest, the two live batch routes, and the project import (whole and in
#: parts). Each has its own lower check (50,000 results, a 50 MiB file, a
#: 32 MiB part) that answers first with its own message; the limit here is the
#: backstop for the bytes around them.
LARGE_BODY_PATHS = frozenset(
    {
        "/api/v1/ingest",
        "/api/v1/ingest/file",
        "/api/v1/stream/events/batch",
        "/api/v1/stream/ingest",
        "/api/v1/projects/import",
    }
)
_LARGE_BODY_PATTERNS = (
    re.compile(r"^/api/v1/project-imports/uploads/[^/]+/parts/[^/]+$"),
)


def is_large_body_route(path: str) -> bool:
    """True for the routes that get the upload limit instead of the default."""
    path = path.rstrip("/") or "/"
    return path in LARGE_BODY_PATHS or any(p.match(path) for p in _LARGE_BODY_PATTERNS)


def _size_text(limit: int) -> str:
    mib = limit / (1024 * 1024)
    if mib >= 1:
        return f"{limit} bytes ({mib:g} MiB)"
    return f"{limit} bytes"


def _too_large_detail(limit: int, large_route: bool) -> str:
    advice = (
        "Send the results in smaller batches or split the file."
        if large_route
        else "Send a smaller request."
    )
    return f"Request body is larger than the limit of {_size_text(limit)} for this route. {advice}"


class RequestBodyTooLarge(StarletteHTTPException):
    """Raised from ``receive`` when the body passes the limit while it is read.

    An HTTPException on purpose: FastAPI turns any other error raised while it
    reads a body into a 400, and lets this one through as the 413 it is.
    """

    def __init__(self, limit: int, large_route: bool):
        self.limit = limit
        super().__init__(status_code=413, detail=_too_large_detail(limit, large_route))


class RequestBodyLimitMiddleware:
    """413 for a request body over the route's limit, whatever its content type.

    A declared ``Content-Length`` over the limit is refused before anything is
    read. Otherwise the bytes are counted as the application reads them, so a
    chunked body (no ``Content-Length``) is stopped at the limit too. Nothing
    is buffered here: a streaming handler still sees each chunk as it arrives.

    Only ``http`` scopes are touched, and only the REQUEST body: WebSockets,
    server-sent events and streamed downloads pass through unchanged. A limit
    of ``0`` switches that limit off.
    """

    def __init__(self, app: ASGIApp, max_bytes: int, upload_max_bytes: int):
        self.app = app
        self.max_bytes = max_bytes
        self.upload_max_bytes = upload_max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        large_route = is_large_body_route(str(scope.get("path") or ""))
        limit = self.upload_max_bytes if large_route else self.max_bytes
        if limit <= 0:
            await self.app(scope, receive, send)
            return

        declared = _content_length(scope)
        if declared is not None and declared > limit:
            await self._reject(limit, large_route, scope, receive, send)
            return

        total = 0
        exceeded = False
        response_started = False
        refusal_sent = False

        async def counted_receive() -> Message:
            nonlocal total, exceeded
            message = await receive()
            if message["type"] == "http.request":
                total += len(message.get("body", b""))
                if total > limit:
                    exceeded = True
                    raise RequestBodyTooLarge(limit, large_route)
            return message

        async def guarded_send(message: Message) -> None:
            nonlocal response_started, refusal_sent
            if exceeded and not response_started:
                # Whatever the application made of the interrupted read, the
                # caller gets the one 413 that states the limit.
                if not refusal_sent:
                    refusal_sent = True
                    await self._reject(limit, large_route, scope, receive, send)
                return
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, counted_receive, guarded_send)
        except RequestBodyTooLarge:
            if response_started or refusal_sent:
                return
            await self._reject(limit, large_route, scope, receive, send)

    @staticmethod
    async def _reject(
        limit: int, large_route: bool, scope: Scope, receive: Receive, send: Send
    ) -> None:
        response = JSONResponse(
            status_code=413,
            content={"detail": _too_large_detail(limit, large_route)},
        )
        await response(scope, receive, send)


# ── HSTS on responses the frontend's nginx never sees ───────────────────────

#: The value frontend/nginx.conf.template sends. One year, no
#: includeSubDomains: the hosts below an operator's hostname are not ours to
#: pin to HTTPS. Kept equal by tests/regression/test_request_body_limit_and_hsts.py.
HSTS_VALUE = b"max-age=31536000"
_HSTS_HEADER = b"strict-transport-security"


class StrictTransportSecurityMiddleware:
    """Add ``Strict-Transport-Security`` to responses of https requests.

    "https" is ``scope["scheme"]``, which the proxy trust boundary
    (``install_proxy_boundary``) sets from ``X-Forwarded-Proto`` only when the
    peer is listed in ``TRUSTED_PROXY_IPS``. So this must be registered INSIDE
    that boundary, and a header from an untrusted peer changes nothing. A
    plain-http request never gets the header, and a response that already has
    one is left alone.
    """

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("scheme") != "https":
            await self.app(scope, receive, send)
            return

        async def send_with_hsts(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers") or [])
                if not any(bytes(name).lower() == _HSTS_HEADER for name, _ in headers):
                    headers.append((_HSTS_HEADER, HSTS_VALUE))
                    message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_with_hsts)
