"""
TestLookup — FastAPI Application Entry Point
"""
import asyncio
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from app.bootstrap import configure_metrics, configure_middlewares, register_routers
from app.core.config import settings
from app.core.http_client import close_http_client
from app.core.logging_config import configure_logging
from app.db.mongo import close_mongo, ensure_indexes as ensure_mongo_indexes
from app.db.postgres import close_db
from app.db.redis_client import close_redis

# ── Structured logging ────────────────────────────────────────
configure_logging()
logger = structlog.get_logger(__name__)

# ── Distributed tracing (OpenTelemetry) ──────────────────────
if settings.OTEL_ENABLED:
    from app.core.tracing import setup_tracing  # noqa: PLC0415

    setup_tracing(
        service_name=settings.OTEL_SERVICE_NAME,
        service_version=settings.APP_VERSION,
        environment=settings.APP_ENV,
        otlp_endpoint=settings.OTEL_EXPORTER_OTLP_ENDPOINT,
    )

# ── Prometheus metrics (initialise counters) ─────────────────
if settings.METRICS_ENABLED:
    from app.core.metrics import app_info  # noqa: PLC0415

    app_info.info(
        {
            "version": settings.APP_VERSION,
            "env": settings.APP_ENV,
            "llm_provider": settings.LLM_PROVIDER,
        }
    )

# ── Rate limiter (login brute-force protection) ───────────────
limiter = Limiter(key_func=get_remote_address, default_limits=[])


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown lifecycle hooks."""
    logger.info(
        "TestLookup starting",
        version=settings.APP_VERSION,
        env=settings.APP_ENV,
    )

    # Validate production secrets — fail fast on misconfig
    secret_warnings = settings.validate_production_secrets()
    for warning in secret_warnings:
        logger.warning("security_check_failed", message=warning)
    critical_warnings = [w for w in secret_warnings if w.startswith("CRITICAL")]
    if critical_warnings and settings.APP_ENV == "production":
        raise RuntimeError(
            f"Refusing to start in production with {len(critical_warnings)} critical "
            f"security issue(s): {'; '.join(critical_warnings)}"
        )

    # Ensure MongoDB indexes exist — centralized spec lives in app/db/mongo.py
    # so new collections/lookups only need to be registered in one place.
    try:
        await ensure_mongo_indexes()
        logger.info("MongoDB indexes verified")
    except Exception as e:
        logger.warning("Failed to create MongoDB indexes", error=str(e))

    # Start live event stream consumer (reads from Redis Streams, dispatches to WebSocket)
    from app.streams.live_consumer import LiveEventStreamConsumer
    _consumer = LiveEventStreamConsumer()
    _consumer_task = asyncio.create_task(_consumer.run(), name="live-event-consumer")
    logger.info("Live event stream consumer started")

    yield  # Application runs here

    # Shutdown consumer
    _consumer_task.cancel()
    try:
        await _consumer_task
    except asyncio.CancelledError:
        pass
    logger.info("Live event stream consumer stopped")

    # Shutdown DB connections and pooled outbound HTTP client
    await close_db()
    await close_mongo()
    await close_redis()
    await close_http_client()
    logger.info("TestLookup shutdown complete")


# ── Application factory ───────────────────────────────────────
app = FastAPI(
    title="TestLookup",
    description="360° AI-Powered Software Testing Intelligence Platform",
    version=settings.APP_VERSION,
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
    lifespan=lifespan,
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)  # type: ignore[arg-type]
configure_middlewares(app)
configure_metrics(app)
register_routers(app)


# ── Auth rate limits (P5-8: covers login + register) ────────────────────────
# Rate limit config: path → (production limit, error message)
_AUTH_RATE_LIMITS: dict[str, tuple[str, str]] = {
    "/api/v1/auth/login": (
        "10/minute",
        "Too many login attempts. Try again in a minute.",
    ),
    "/api/v1/auth/register": (
        "5/minute",
        "Too many registration attempts. Try again in a minute.",
    ),
}


@app.middleware("http")
async def rate_limit_auth(request: Request, call_next):
    """Apply rate limits to authentication endpoints.

    Production: login 10/min, register 5/min.
    Development: 200/min for both (no friction during dev).
    """
    config = _AUTH_RATE_LIMITS.get(request.url.path)
    if request.method == "POST" and config:
        prod_limit, error_msg = config
        limit = "200/minute" if settings.APP_ENV == "development" else prod_limit

        @limiter.limit(limit)
        async def _limited(request: Request):
            pass

        try:
            await _limited(request)
        except RateLimitExceeded:
            return JSONResponse(
                status_code=429,
                content={"detail": error_msg},
            )
    return await call_next(request)


# Note: the legacy ``GET /health`` shim was retired in item #10 cleanup —
# K8s probes should target ``/health/live`` (liveness) and ``/health/ready``
# (readiness). See ``backend/app/routers/health.py`` for the current contract.


@app.get("/", tags=["System"])
async def root():
    return JSONResponse({
        "name": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "docs": "/docs",
    })
