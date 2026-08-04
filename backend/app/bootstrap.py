from __future__ import annotations

from collections.abc import Sequence

from fastapi import APIRouter, Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.core.deps import get_current_user_or_api_key
from app.routers import (
    agent_investigations,
    agent_memory,
    agents,
    ai_evaluation,
    admin_maintenance,
    analysis_report,
    analyze,
    analytics,
    api_keys,
    app_settings,
    audit_dashboard,
    auth,
    chat,
    commit_attribution,
    compliance_packs,
    deep_investigation,
    debug,
    decision_trail,
    defect_jira,
    digests,
    duplicates,
    feature_flags as feature_flags_router,
    feedback,
    fixer,
    flaky_quarantine,
    github_integration,
    gitlab_integration,
    identity_events,
    ingest,
    integration_health,
    integrations,
    knowledge_sources,
    live,
    llm_cost_budget as llm_cost_budget_router,
    metrics,
    mfa,
    my_failures,
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
    retention,
    run_compare,
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
    suites,
    summary_report,
    test_execution_reviews,
    test_health,
    test_management,
    users,
    value_metric_assumptions,
    value_metrics,
    webhooks,
    webhooks_outbound,
)
from app.routers.health import router as health_router
from app.routers.observability import router as observability_router


PUBLIC_ROUTERS: Sequence[APIRouter] = (
    auth.router,
    # MFA is public for the same reason auth is: /mfa/verify and the forced
    # enrollment path carry an interstitial MFA token, not an access token, so
    # the router-wide protected dependency would 401 them before the handler
    # ran. Each endpoint declares its own auth requirement.
    mfa.router,
    webhooks.router,
    stream.router,
    observability_router,
    health_router,
    sso.router,            # SSO/SAML — public endpoints (metadata, ACS, login-url, status)
    scim.router,           # SCIM 2.0 — bearer-token auth (not JWT)
    shared_reports.router,  # Public shared report views (token-based, ENT-03)
    sdk.router,             # Client SDK downloads (no auth required)
    # live.router has its own auth: WebSocket auths via post-connect message,
    # POST /events uses verify_webhook_secret. Cannot be added to PROTECTED_ROUTERS
    # because OAuth2PasswordBearer crashes on WebSocket scope (no Request object).
    live.router,
)

PROTECTED_ROUTERS: Sequence[APIRouter] = (
    admin_maintenance.router,
    projects.router,
    # run_compare must be registered BEFORE runs.router because both share the
    # ``/api/v1/runs`` prefix and runs.router has ``GET /{run_id}`` which
    # otherwise swallows ``/compare`` and ``/compare/latest`` as a UUID path
    # param, yielding 422.
    run_compare.router,                # Tier 2 item 8: two-run compare
    runs.router,
    metrics.router,
    search.router,
    analyze.router,
    analytics.router,
    integrations.router,
    notifications.router,
    app_settings.router,
    agents.router,
    chat.router,
    feedback.router,
    feedback.lookup_router,            # US-2.4: fingerprint → analysis_id lookup (project-scoped)
    deep_investigation.router,
    release_readiness.router,
    releases.router,
    reports.router,
    run_intelligence.router,
    commit_attribution.router,       # Epic 8 US-8.1/US-8.2: commit range + suspect ranking
    scoring.router,
    onboarding.router,
    test_health.router,
    test_management.router,
    users.router,
    users.projects_router,
    api_keys.router,
    value_metrics.router,
    value_metric_assumptions.router,  # PMF US-12.1: per-project hours-saved assumptions
    retention.router,                 # PMF US-11.4: per-project retention policies + purge
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
    feature_flags_router.router,       # Tier 0A: generic feature flag store (ADMIN)
    decision_trail.router,             # Tier 0B: AI decision audit trail per run
    llm_cost_budget_router.router,     # Tier 1 item 2: LLM cost budget + usage meter
    flaky_quarantine.router,           # Tier 1 item 3: flaky-test quarantine workflow
    flaky_quarantine.manifest_router,  # US-5.1: CI quarantine manifest (project-scoped)
    compliance_packs.router,           # Tier 1 item 4: release compliance export pack
    github_integration.router,         # Tier 1 item 5: GitHub Checks integration
    gitlab_integration.router,         # PMF Epic 3: GitLab MR notes + commit statuses
    webhooks_outbound.router,          # Tier 2 item 6: outbound webhook subscriptions
    suites.router,                     # Phase 3: TestSuite + CanonicalTestCase CRUD
    my_failures.router,                # 0080: per-user "My Failures" inbox of auto-assigned failures
    test_execution_reviews.router,     # 0081: per-TestCase human review transitions
    summary_report.router,             # Per-project consolidated summary report + PDF export
    analysis_report.router,            # PMF US-7.5: self-contained HTML analysis report download
    duplicates.router,                 # Phase 4: per-project duplicate authored-test-case review queue
    defect_jira.router,                # PMF US-6.1/6.3: one-click Jira defects (project-scoped)
    agent_investigations.router,       # AI-1/AI-3: Investigator + agent policies + agent-runs ledger
    fixer.router,                      # AI-2: the Fixer — config + run + fix-attempts
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
