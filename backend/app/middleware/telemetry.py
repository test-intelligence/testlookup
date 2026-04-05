"""
TestLookup — Telemetry middleware.

For every HTTP request:
  1. Assigns a unique X-Request-ID (respects client-supplied header if present).
  2. Binds request_id, method, and path into structlog context variables so every
     log statement emitted during the request automatically carries those fields.
  3. Emits a structured access-log line with status_code, duration_ms, and client IP
     after the response is complete.
  4. Returns X-Request-ID in the response headers so clients can correlate requests.

Paths listed in _SKIP_PATHS are exempt from access logging to reduce noise.
"""
import time
import uuid

import structlog
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp

logger = structlog.get_logger("http.access")

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
        request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        start = time.perf_counter()

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
        return response
