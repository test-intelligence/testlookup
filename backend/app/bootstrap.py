from __future__ import annotations

from collections.abc import Sequence

from fastapi import APIRouter, Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.core.deps import get_current_user_or_api_key
from app.routers import (
    agent_memory,
    agents,
    ai_evaluation,
    analyze,
    analytics,
    api_keys,
    app_settings,
    audit_dashboard,
    auth,
    chat,
    deep_investigation,
    debug,
    digests,
    feedback,
    identity_events,
    ingest,
    integration_health,
    integrations,
    knowledge_sources,
    live,
    metrics,
    notifications,
    onboarding,
    ownership,
    performance,
    projects,
    rag_generation,
    release_gate_policies,
    release_readiness,
    releases,
    reports,
    run_intelligence,
    runs,
    saved_views,
    scim,
    scoring,
    sdk,
    search,
    seed,
    shared_reports,
    sso,
    stream,
    test_health,
    test_management,
    users,
    value_metrics,
    webhooks,
)
from app.routers.health import router as health_router
from app.routers.observability import router as observability_router


PUBLIC_ROUTERS: Sequence[APIRouter] = (
    auth.router,
    webhooks.router,
    stream.router,
    observability_router,
    health_router,
    sso.router,            # SSO/SAML — public endpoints (metadata, ACS, login-url, status)
    scim.router,           # SCIM 2.0 — bearer-token auth (not JWT)
    shared_reports.router,  # Public shared report views (token-based, ENT-03)
    sdk.router,             # Client SDK downloads (no auth required)
)

PROTECTED_ROUTERS: Sequence[APIRouter] = (
    projects.router,
    runs.router,
    metrics.router,
    search.router,
    analyze.router,
    analytics.router,
    integrations.router,
    notifications.router,
    app_settings.router,
    live.router,
    agents.router,
    chat.router,
    feedback.router,
    deep_investigation.router,
    release_readiness.router,
    releases.router,
    reports.router,
    run_intelligence.router,
    scoring.router,
    onboarding.router,
    test_health.router,
    test_management.router,
    users.router,
    users.projects_router,
    api_keys.router,
    value_metrics.router,
    scim.token_router,              # SCIM token management (admin-only, JWT-protected)
    identity_events.router,          # Identity audit events (admin-only, JWT-protected)
    release_gate_policies.router,    # Release gate policy CRUD (ENT-02)
    ownership.router,                # Service ownership rules (ENT-04)
    saved_views.router,              # Saved views (ENT-05)
    digests.router,                  # Digest subscriptions (ENT-05)
    integration_health.router,       # Integration health probes (OPS-01)
    audit_dashboard.router,          # Unified audit dashboard (OPS-04)
    ai_evaluation.router,            # AI evaluation dashboards (OPS-02)
    performance.router,              # Performance budgets & config (OPS-03)
    agent_memory.router,             # Unified agent memory & recall (P3)
    seed.router,                      # Dev-only seed data management
    ingest.router,                     # Unified test data ingestion (JSON batch + file upload)
    knowledge_sources.router,          # Knowledge source registry (RAG-1/2/3)
    rag_generation.router,             # RAG grounded generation (RAG-7 through RAG-14)
)


def configure_middlewares(app: FastAPI) -> None:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "Authorization", "X-API-Key", "X-Webhook-Secret", "X-Request-ID"],
    )

    # Import locally so middleware setup stays close to other app wiring.
    from app.middleware.telemetry import TelemetryMiddleware

    app.add_middleware(TelemetryMiddleware)


def configure_metrics(app: FastAPI) -> None:
    if not settings.METRICS_ENABLED:
        return

    from prometheus_fastapi_instrumentator import Instrumentator

    Instrumentator(
        should_group_status_codes=True,
        should_ignore_untemplated=True,
        excluded_handlers=["/metrics", "/health/live", "/health/ready"],
    ).instrument(app).expose(app, endpoint="/metrics", include_in_schema=False)


def register_routers(app: FastAPI) -> None:
    for router in PUBLIC_ROUTERS:
        app.include_router(router)

    # CLI-5: Accept both JWT and API key auth on protected routes
    protected_deps = [Depends(get_current_user_or_api_key)]
    for router in PROTECTED_ROUTERS:
        app.include_router(router, dependencies=protected_deps)

    app.include_router(
        debug.router,
        prefix="/api/v1/debug",
        tags=["Debug"],
        dependencies=protected_deps,
    )
