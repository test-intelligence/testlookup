"""
TestLookup — Telemetry middleware.

For every HTTP request:
  1. Assigns a unique X-Request-ID. A client-supplied header is kept only when
     it is well formed (``_REQUEST_ID_RE``: 1-128 of ``A-Z a-z 0-9 . _ : -``);
     anything else -- too long, a CR/LF, markup, a space -- is replaced by a
     fresh UUID, because the value is echoed into a response header, every log
     line and the VIZ-210 error body.
  2. Binds request_id, method, and path into structlog context variables so every
     log statement emitted during the request automatically carries those fields.
  3. Emits a structured access-log line with status_code, duration_ms, and client IP
     after the response is complete.
  4. Returns X-Request-ID in the response headers so clients can correlate requests.

Paths listed in _SKIP_PATHS are exempt from access logging to reduce noise.

The ``REQUEST_ID_CTX`` context variable is available for non-structlog code
(e.g., MongoDB query comments, external HTTP headers) to read the current
request's correlation ID.
"""
import contextvars
import re
import time
import uuid

import structlog
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp

logger = structlog.get_logger("http.access")

# ── Correlation ID context var ───────────────────────────────────────────────
# Readable from any async code within the same request scope.
REQUEST_ID_CTX: contextvars.ContextVar[str] = contextvars.ContextVar(
    "request_id", default=""
)

#: What an incoming ``X-Request-ID`` may look like to be trusted (VIZ-210).
_REQUEST_ID_RE = re.compile(r"[A-Za-z0-9._:-]{1,128}")


def accepted_request_id(value: str | None) -> str:
    """The incoming id when well formed, otherwise a new UUID4."""
    if value and _REQUEST_ID_RE.fullmatch(value):
        return value
    return str(uuid.uuid4())


# Paths that generate too much noise if logged on every request
_SKIP_PATHS = frozenset(
    {"/health", "/health/live", "/health/ready", "/metrics", "/", "/favicon.ico"}
)


class TelemetryMiddleware(BaseHTTPMiddleware):
    """
    Injects X-Request-ID and emits structured access-log lines.
    Must be added AFTER CORSMiddleware so CORS headers are already present.
    """

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        request_id = accepted_request_id(request.headers.get("X-Request-ID"))
        # On the request state too: the unhandled-error handler runs OUTSIDE
        # this middleware (after the context below is cleared) and still has to
        # put the id in the error body and header.
        request.state.request_id = request_id
        start = time.perf_counter()

        # Set correlation ID for non-structlog consumers
        _token = REQUEST_ID_CTX.set(request_id)

        # Bind context — all log calls in the same async context will carry these fields
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(
            request_id=request_id,
            method=request.method,
            path=request.url.path,
        )

        try:
            response = await call_next(request)
        except Exception:
            structlog.contextvars.clear_contextvars()
            REQUEST_ID_CTX.reset(_token)
            raise

        duration_ms = round((time.perf_counter() - start) * 1000, 2)

        if request.url.path not in _SKIP_PATHS:
            log_fn = logger.warning if response.status_code >= 500 else logger.info
            log_fn(
                "http_request",
                status_code=response.status_code,
                duration_ms=duration_ms,
                client_ip=request.client.host if request.client else "unknown",
            )

        response.headers["X-Request-ID"] = request_id
        structlog.contextvars.clear_contextvars()
        REQUEST_ID_CTX.reset(_token)
        return response
