from __future__ import annotations
import inspect

from collections.abc import Sequence

from fastapi import APIRouter, Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.core.deps import get_current_user_or_api_key
from app.routers import (
    agent_configs,
    agent_investigations,
    agent_invoke,
    agent_actions,
    agent_memory,
    agents,
    ai_evaluation,
    admin_maintenance,
    admin_storage,
    analysis_report,
    activity,
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
    release_attribution_rules,
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
    reviews,
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
from starlette.routing import request_response


PUBLIC_ROUTERS: Sequence[APIRouter] = (
    auth.router,
    # MFA is public for the same reason auth is: /mfa/verify and the forced
    # enrollment path carry an interstitial MFA token, not an access token, so
    # the router-wide protected dependency would 401 them before the handler
    # ran. Each endpoint declares its own auth requirement.
    mfa.router,
    # Jira's resolution webhook: no user session, the HMAC signature is the
    # credential (routers/feedback.py). Registered before PROTECTED_ROUTERS,
    # so the literal path also wins over /feedback/{analysis_id}.
    feedback.jira_webhook_router,
    webhooks.router,
    stream.router,
    observability_router,
    health_router,
    sso.router,            # SSO/SAML — public endpoints (metadata, ACS, login-url, status)
    scim.router,           # SCIM 2.0 — bearer-token auth (not JWT)
    shared_reports.router,  # Public shared report views (token-based, ENT-03)
    sdk.router,             # Client SDK downloads (no auth required)
    # live.router has its own auth: the WebSocket authenticates via a
    # post-connect message, and POST /events/{run_id} resolves a project-scoped
    # API key inline (see routers/live.py). It cannot join PROTECTED_ROUTERS
    # because OAuth2PasswordBearer crashes on a WebSocket scope (no Request).
    #
    # It also mounts at /ws, OUTSIDE /api/v1 — which is where both
    # authorization ratchets stop looking. That is why re-audit H1 lived here
    # unnoticed. tests/test_architectural_authorization.py now scans this
    # prefix too; a new router mounted outside /api/v1 must satisfy it.
    live.router,
)

PROTECTED_ROUTERS: Sequence[APIRouter] = (
    admin_maintenance.router,
    admin_storage.router,
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
    # E1.2: AFTER agents.router, so /agents/{agent_id}/invoke is matched only
    # once every literal /agents/... route has had its chance.
    agent_invoke.router,
    agent_configs.router,              # E4.1: per-project agent configuration
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
    # BEFORE test_management: rag_generation owns the literal
    # ``/test-management/cases/stale``, and test_management_cases owns
    # ``/cases/{case_id}``. FastAPI matches in registration order, so with
    # test_management first the literal was unreachable — "stale" was parsed as
    # a UUID and every call 422'd. Guarded by
    # tests/test_architectural_route_shadowing.py.
    rag_generation.router,             # RAG grounded generation (RAG-7 through RAG-14)
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
    release_attribution_rules.router,  # Attribution rules — ladder rung 3 (S3a)
    ownership.router,                # Service ownership rules (ENT-04)
    saved_views.router,              # Saved views (ENT-05)
    digests.router,                  # Digest subscriptions (ENT-05)
    integration_health.router,       # Integration health probes (OPS-01)
    audit_dashboard.router,          # Unified audit dashboard (OPS-04)
    activity.router,                 # Project activity ledger (epic ACT)
    ai_evaluation.router,            # AI evaluation dashboards (OPS-02)
    performance.router,              # Performance budgets & config (OPS-03)
    agent_memory.router,             # Unified agent memory & recall (P3)
    seed.router,                      # Dev-only seed data management
    ingest.router,                     # Unified test data ingestion (JSON batch + file upload)
    knowledge_sources.router,          # Knowledge source registry (RAG-1/2/3)
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
    agent_actions.router,               # Generic typed action proposal review ledger
    reviews.router,                    # E8.2: human review gate for AI reports
    fixer.router,                      # AI-2: the Fixer — config + run + fix-attempts
)


def _add_request_hardening(app: FastAPI) -> None:
    """starlette CVE-2026-54283 / CVE-2025-62727 until the FastAPI/Starlette
    upgrade: see middleware/request_hardening.py.

    Registered FIRST in configure_middlewares on purpose (R-B45-T-3):
    ``add_middleware`` makes each new middleware the outermost, so these sit
    INSIDE Telemetry and CORS. A 413 is then logged and counted like any other
    response, and a browser receives it with CORS headers instead of seeing a
    CORS failure. The proxy trust boundary (install_proxy_boundary) stays
    outermost.
    """
    from app.middleware.request_hardening import (
        FormBodyLimitMiddleware,
        RangeHeaderStripMiddleware,
    )

    app.add_middleware(
        FormBodyLimitMiddleware,
        max_bytes=settings.FORM_URLENCODED_MAX_BYTES,
        path_limits={"/api/v1/sso/acs": settings.FORM_URLENCODED_ACS_MAX_BYTES},
    )
    app.add_middleware(RangeHeaderStripMiddleware)


def configure_middlewares(app: FastAPI) -> None:
    _add_request_hardening(app)  # innermost: inside Telemetry and CORS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "Authorization", "X-API-Key", "X-Webhook-Secret", "X-Request-ID"],
        # E8.3: the two review headers must be exposed, or a browser cannot read
        # them and the SPA would show an AI report with no review state.
        expose_headers=["X-Refresh-Retry-Safe", "Retry-After", "X-TestLookup-AI-Generated", "X-TestLookup-Review-State"],
    )

    # Import locally so middleware setup stays close to other app wiring.
    from app.middleware.telemetry import TelemetryMiddleware
    from app.middleware.scim_request_limit import SCIMRequestBodyLimitMiddleware

    app.add_middleware(TelemetryMiddleware)
    app.add_middleware(SCIMRequestBodyLimitMiddleware)

    # NOTE: the proxy trust boundary is NOT installed here. It must be the
    # OUTERMOST middleware, and main.py registers more middleware after this
    # function returns. See install_proxy_boundary below.


def install_proxy_boundary(app: FastAPI) -> None:
    """Correct ``request.client.host`` before anything else reads it.

    Re-audit H2. Call this **last**, after every other middleware is
    registered: Starlette's ``add_middleware`` inserts at index 0, so the last
    one added is the outermost and the first to run. Everything downstream --
    the login/MFA rate limiter's bucket key, and every IP written for lockout
    and audit forensics -- reads ``request.client.host``, and each must see the
    real caller rather than the ingress.

    Getting the order wrong is silent: the header is still honoured for the
    endpoints, so only the middleware registered outside this one keeps
    reporting the proxy. That is exactly what happened -- ``rate_limit_auth``
    is registered in main.py after ``configure_middlewares``, so the single
    loudest consequence in H2 was still unfixed while the setting looked live.

    It lives in the app rather than in ``gunicorn_conf.py`` because gunicorn's
    ``forwarded_allow_ips`` rejects CIDRs, and rejects them while building its
    Config from the environment, before any config file runs. This is the same
    uvicorn middleware that setting would have configured, minus the validator.

    Empty means trust nothing, so an undeclared topology never starts believing
    a header any client can set.
    """
    if not settings.TRUSTED_PROXY_IPS.strip():
        return

    from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

    app.add_middleware(
        ProxyHeadersMiddleware, trusted_hosts=settings.TRUSTED_PROXY_IPS
    )


def configure_metrics(app: FastAPI) -> None:
    if not settings.METRICS_ENABLED:
        return

    from prometheus_fastapi_instrumentator import Instrumentator

    Instrumentator(
        should_group_status_codes=True,
        should_ignore_untemplated=True,
        excluded_handlers=["/metrics", "/health/live", "/health/ready"],
    ).instrument(app).expose(app, endpoint="/metrics", include_in_schema=False)

    # Refresh Celery queue depths on each scrape. Registered as a route
    # dependency on /metrics so the numbers are read when Prometheus asks,
    # never from a timer that can go stale while the scheduler is the sick one.
    _install_celery_queue_depth_collector(app)


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


async def _refresh_celery_queue_depths() -> None:
    """Read pending task counts straight off the Redis broker.

    Celery queues are Redis lists keyed by queue name, so LLEN is the depth.
    Best-effort: metrics collection must never break the scrape or the app —
    a failed read leaves the previous sample rather than raising.
    """
    try:
        from app.core.metrics import celery_queue_length
        from app.db.redis_client import get_redis
        from app.worker.ingestion_routing import LEGACY_INGESTION_QUEUE, all_shard_queues

        queues = ["critical", "ai_analysis", "default", LEGACY_INGESTION_QUEUE]
        queues += list(all_shard_queues())

        redis = get_redis()
        for name in queues:
            try:
                depth = await redis.llen(name)
            except Exception:  # noqa: BLE001 — one bad queue must not hide the rest
                continue
            celery_queue_length.labels(queue_name=name).set(float(depth or 0))
    except Exception as exc:  # noqa: BLE001
        import structlog

        structlog.get_logger(__name__).debug(
            "celery_queue_depth_collection_failed", error=str(exc)
        )


def _install_celery_queue_depth_collector(app) -> None:
    """Wrap the instrumentator's /metrics route so depths refresh per scrape."""
    for route in app.routes:
        if getattr(route, "path", None) != "/metrics":
            continue
        original = route.endpoint

        async def _endpoint(*args, __orig=original, **kwargs):
            await _refresh_celery_queue_depths()
            result = __orig(*args, **kwargs)
            if inspect.isawaitable(result):
                result = await result
            return result

        route.endpoint = _endpoint
        route.app = request_response(_endpoint)
        return
