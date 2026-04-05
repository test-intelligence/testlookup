"""
TestLookup — Frontend telemetry receiver.

Accepts a batch of client-side events from the React SPA:
  - JavaScript errors (uncaught exceptions, unhandled promise rejections, React error boundaries)
  - Core Web Vitals (CLS, LCP, FCP, FID, TTFB, INP)

Events are forwarded into the server-side structured logging pipeline so they appear
alongside backend log lines in the same log aggregation system.

POST /api/v1/observability/frontend  (no auth required — called from anonymous browser sessions)
"""
import logging
from datetime import datetime, timezone
from typing import Any, List, Optional

from fastapi import APIRouter, Request
from pydantic import BaseModel, field_validator

logger = logging.getLogger("observability.frontend")

router = APIRouter(prefix="/api/v1/observability", tags=["Observability"])


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

    @field_validator("message")
    @classmethod
    def truncate_message(cls, v: str) -> str:
        return v[:2000]

    @field_validator("stack")
    @classmethod
    def truncate_stack(cls, v: Optional[str]) -> Optional[str]:
        return v[:5000] if v else v


class WebVitalReport(BaseModel):
    name: str       # CLS | FID | LCP | FCP | TTFB | INP
    value: float
    rating: str     # "good" | "needs-improvement" | "poor"
    url: str
    delta: Optional[float] = None


class FrontendTelemetryBatch(BaseModel):
    errors: List[FrontendError] = []
    vitals: List[WebVitalReport] = []


# ── Endpoint ──────────────────────────────────────────────────────────────────

@router.post("/frontend", status_code=202, summary="Receive frontend telemetry batch")
async def receive_frontend_telemetry(
    payload: FrontendTelemetryBatch,
    request: Request,
):
    """
    Accepts and logs a batch of frontend errors and Web Vitals.
    Always returns 202 — the browser fires-and-forgets this call.
    """
    client_ip = request.client.host if request.client else "unknown"
    now = datetime.now(timezone.utc).isoformat()

    for err in payload.errors:
        logger.error(
            "frontend_error",
            extra={
                "error_type": err.type,
                "error_message": err.message,
                "error_url": err.url,
                "error_stack": err.stack,
                "component_stack": err.component_stack,
                "user_agent": err.user_agent,
                "client_ip": client_ip,
                "occurred_at": err.timestamp or now,
                "fe_context": err.context or {},
            },
        )

    for vital in payload.vitals:
        level = logging.WARNING if vital.rating == "poor" else logging.INFO
        logger.log(
            level,
            "web_vital",
            extra={
                "metric": vital.name,
                "value": round(vital.value, 3),
                "rating": vital.rating,
                "delta": vital.delta,
                "url": vital.url,
            },
        )

    return {
        "received": {
            "errors": len(payload.errors),
            "vitals": len(payload.vitals),
        }
    }
