"""
TestLookup — Frontend telemetry receiver.

Accepts a batch of client-side events from the React SPA:
  - JavaScript errors (uncaught exceptions, unhandled promise rejections, React error boundaries)
  - Core Web Vitals (CLS, LCP, FCP, FID, TTFB, INP)

Events are forwarded into the server-side structured logging pipeline so they appear
alongside backend log lines in the same log aggregation system.

POST /api/v1/observability/frontend  (no auth required — called from anonymous browser sessions)

Because the route takes no credential, everything it accepts is bounded:

* at most ``MAX_BATCH_ITEMS`` errors and as many vitals per request (422 past
  that), and every string is cut to a fixed length;
* the page address is cut at the first ``?`` or ``#``: invite, password-reset
  and SSO values ride in the URL fragment so that they never reach a server;
* a per-address rate limit (``_AUTH_RATE_LIMITS`` in ``main.py``);
* records are logged at WARNING at most, never ERROR, so an anonymous caller
  cannot raise the error rate that alerts key on; and through structlog
  key-values, so the privacy redaction (``logging_config``) reads every field.
"""
import json
from datetime import datetime, timezone
from typing import Any, List, Optional

import structlog
from fastapi import APIRouter, Request
from pydantic import BaseModel, Field, field_validator

logger = structlog.get_logger("observability.frontend")

router = APIRouter(prefix="/api/v1/observability", tags=["Observability"])

#: Most errors, and most vitals, one request may carry. The browser buffers
#: fewer (``frontend/src/utils/errorReporting.ts``; a test pins the two).
MAX_BATCH_ITEMS = 50

_MAX_URL_CHARS = 2000
_MAX_LABEL_CHARS = 64
_MAX_USER_AGENT_CHARS = 500
_MAX_CONTEXT_CHARS = 2000


def _page_address(value: str) -> str:
    """``value`` up to its first ``?`` or ``#``: no query string, no fragment."""
    return value.split("?", 1)[0].split("#", 1)[0][:_MAX_URL_CHARS]


# ── Request schemas ───────────────────────────────────────────────────────────

class FrontendError(BaseModel):
    type: str                          # "error" | "unhandledrejection" | "boundary"
    message: str
    stack: Optional[str] = None
    component_stack: Optional[str] = None   # React error boundary stack
    url: str
    user_agent: Optional[str] = None
    timestamp: Optional[str] = None
    context: Optional[dict[str, Any]] = None

    @field_validator("type")
    @classmethod
    def truncate_type(cls, v: str) -> str:
        return v[:_MAX_LABEL_CHARS]

    @field_validator("message")
    @classmethod
    def truncate_message(cls, v: str) -> str:
        return v[:2000]

    @field_validator("stack", "component_stack")
    @classmethod
    def truncate_stack(cls, v: Optional[str]) -> Optional[str]:
        return v[:5000] if v else v

    @field_validator("url")
    @classmethod
    def strip_url(cls, v: str) -> str:
        return _page_address(v)

    @field_validator("user_agent")
    @classmethod
    def truncate_user_agent(cls, v: Optional[str]) -> Optional[str]:
        return v[:_MAX_USER_AGENT_CHARS] if v else v

    @field_validator("timestamp")
    @classmethod
    def truncate_timestamp(cls, v: Optional[str]) -> Optional[str]:
        return v[:_MAX_LABEL_CHARS] if v else v

    @field_validator("context")
    @classmethod
    def bound_context(cls, v: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
        if not v:
            return v
        try:
            size = len(json.dumps(v, default=str))
        except (TypeError, ValueError, RecursionError):
            return {"truncated": True}
        return v if size <= _MAX_CONTEXT_CHARS else {"truncated": True}


class WebVitalReport(BaseModel):
    name: str       # CLS | FID | LCP | FCP | TTFB | INP
    value: float
    rating: str     # "good" | "needs-improvement" | "poor"
    url: str
    delta: Optional[float] = None

    @field_validator("name", "rating")
    @classmethod
    def truncate_label(cls, v: str) -> str:
        return v[:_MAX_LABEL_CHARS]

    @field_validator("url")
    @classmethod
    def strip_url(cls, v: str) -> str:
        return _page_address(v)


class FrontendTelemetryBatch(BaseModel):
    errors: List[FrontendError] = Field(default_factory=list, max_length=MAX_BATCH_ITEMS)
    vitals: List[WebVitalReport] = Field(default_factory=list, max_length=MAX_BATCH_ITEMS)


# ── Endpoint ──────────────────────────────────────────────────────────────────

@router.post("/frontend", status_code=202, summary="Receive frontend telemetry batch")
async def receive_frontend_telemetry(
    payload: FrontendTelemetryBatch,
    request: Request,
):
    """
    Accepts and logs a batch of frontend errors and Web Vitals.
    Returns 202 for an accepted batch — the browser fires-and-forgets this call.
    A batch with more than 50 errors or 50 vitals is refused with 422.
    """
    client_ip = request.client.host if request.client else "unknown"
    now = datetime.now(timezone.utc).isoformat()

    for err in payload.errors:
        # WARNING, not ERROR: the caller is anonymous. ``error`` and ``stack``
        # are free-text fields to the privacy redaction, which is what a
        # browser's message and stack trace are.
        logger.warning(
            "frontend_error",
            kind=err.type,
            error=err.message,
            url=err.url,
            stack=err.stack,
            component_stack=err.component_stack,
            user_agent=err.user_agent,
            client_ip=client_ip,
            occurred_at=err.timestamp or now,
            context=err.context or {},
        )

    for vital in payload.vitals:
        log = logger.warning if vital.rating == "poor" else logger.info
        log(
            "web_vital",
            metric=vital.name,
            value=round(vital.value, 3),
            rating=vital.rating,
            delta=vital.delta,
            url=vital.url,
        )

    return {
        "received": {
            "errors": len(payload.errors),
            "vitals": len(payload.vitals),
        }
    }
