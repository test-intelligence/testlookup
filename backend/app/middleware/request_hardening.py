"""Request hardening at the ASGI layer (Trivy findings on PR #26).

Two starlette advisories are reachable in this app and cannot be fixed by a
version bump yet (``fastapi==0.115.5`` pins starlette below 0.42; the fixed
starlette releases need a FastAPI major upgrade, tracked as a follow-up):

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
"""

from __future__ import annotations

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
