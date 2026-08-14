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

    # AI_OFFLINE_MODE must cover model WEIGHTS, not just inference. ChromaDB's
    # default embedder fetches 79 MB from AWS S3 on first use; installed here so
    # a sealed deployment degrades instead of dialling out.
    try:
        from app.services.local_embedder_guard import install_offline_embedder_guard

        install_offline_embedder_guard()
    except Exception as exc:  # noqa: BLE001 — never block startup on the guard
        logger.warning("embedder_guard_install_failed", error_type=type(exc).__name__)

    # Validate production secrets — fail fast on misconfig (production AND staging)
    secret_warnings = settings.validate_production_secrets()
    for warning in secret_warnings:
        logger.warning("security_check_failed", message=warning)
    critical_warnings = settings.critical_security_failures()
    if critical_warnings:
        raise RuntimeError(
            f"Refusing to start in {settings.APP_ENV} with {len(critical_warnings)} critical "
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
    # S4-audit S7: the refresh endpoint mints new access tokens from a refresh
    # token, so an unthrottled it is a refresh-token-grinding / token-mint
    # amplification vector. 30/min is well above any legitimate client's
    # refresh cadence (access tokens live for minutes) while still blocking
    # automated abuse.
    "/api/v1/auth/refresh": (
        "30/minute",
        "Too many token refresh attempts. Try again in a minute.",
    ),
    # ── MFA (0117) ─────────────────────────────────────────────────────────
    # The second leg of login is a 6-digit-code oracle, so it needs its own
    # ceiling. Per-account lockout (users.failed_login_attempts) is the real
    # control — global, durable, and unaffected by which worker serves the
    # request — but an IP limit still blunts distributed guessing against many
    # accounts at once.
    "/api/v1/auth/mfa/verify": (
        "12/minute",
        "Too many verification attempts. Try again in a minute.",
    ),
    "/api/v1/auth/mfa/enroll/start": (
        "6/minute",
        "Too many enrollment attempts. Try again in a minute.",
    ),
    "/api/v1/auth/mfa/enroll/confirm": (
        "8/minute",
        "Too many enrollment attempts. Try again in a minute.",
    ),
}


def _auth_rate_limit_key(request: Request) -> str:
    """Bucket key: client address **plus the path**.

    Without the path, every entry in ``_AUTH_RATE_LIMITS`` shares one counter
    per client: slowapi derives its storage key from ``key_func`` plus the
    limit string, so two paths only landed in different buckets when their
    limit strings happened to differ. That held by accident for 10/5/30 and
    would have broken silently the moment two entries were given the same
    limit. Making the path part of the key removes the coupling.

    Storage is per-process (``Limiter`` is constructed without a
    ``storage_uri``), so these are per-worker ceilings — a coarse
    anti-automation measure, not the account-protection control. Per-account
    lockout (``users.failed_login_attempts`` / ``locked_until``, migration
    0117) is the control that is actually global.
    """
    return f"{get_remote_address(request)}|{request.url.path}"


def _build_auth_limiters() -> dict[str, tuple]:
    """Build one decorated callable per rate-limited path, **once**.

    This used to be done inside the middleware, per request. That was a real
    defect, not a style issue: ``Limiter.limit`` registers its limits under
    ``f"{func.__module__}.{func.__name__}"``, and re-decorating a fresh local
    function on every request kept *appending* to the same
    ``_route_limits["app.main._limited"]`` list. Request N therefore evaluated
    N limits and recorded N hits against the same counter, so the effective
    ceiling collapsed quadratically — an IP was permanently 429'd off
    ``/auth/login`` after roughly four or five attempts against a nominal
    10/minute, and the registration list grew without bound for the life of
    the process. Building once fixes both.

    Each path gets a distinct ``__name__`` so the per-path registrations do
    not share a bucket at slowapi's level either (the key function already
    separates them at the storage level; this keeps the two consistent).
    """
    built: dict[str, tuple] = {}
    for path, (prod_limit, error_msg) in _AUTH_RATE_LIMITS.items():
        limit = "200/minute" if settings.APP_ENV == "development" else prod_limit

        async def _limited(request: Request):
            pass

        _limited.__name__ = "auth_rate_limit_" + path.strip("/").replace("/", "_")
        decorated = limiter.limit(limit, key_func=_auth_rate_limit_key)(_limited)
        built[path] = (decorated, error_msg)
    return built


_AUTH_LIMITERS: dict[str, tuple] = _build_auth_limiters()


@app.middleware("http")
async def rate_limit_auth(request: Request, call_next):
    """Apply rate limits to authentication endpoints.

    Production: login 10/min, register 5/min, MFA verify 12/min.
    Development: 200/min for all (no friction during dev).
    """
    entry = _AUTH_LIMITERS.get(request.url.path)
    if request.method == "POST" and entry:
        limited, error_msg = entry
        try:
            await limited(request)
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
