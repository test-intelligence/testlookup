# AGENTS.md — TestLookup

## Project Overview

**TestLookup** is a 360° AI-powered software testing intelligence platform. It ingests test results from 50+ frameworks, uses a LangChain ReAct agent (via Ollama locally or cloud LLMs) to correlate failures, and pushes structured root-cause analysis to Jira. It includes a deep multi-agent investigation network, continuous fine-tuning pipeline, real-time live streaming, full observability stack, user/API-key management, and an MCP server for AI assistant integration.

- Local-LLM capable (air-gapped via Ollama)
- Multi-framework ingestion (Allure, TestNG, JUnit, etc.)
- OpenShift/Kubernetes native (Kustomize)

---

## Architecture

```
React SPA (frontend:3000)
      ↓
FastAPI Backend (backend:8000)
      ↓
┌───────────────────────────────────────────────────────────────────┐
│  PostgreSQL  MongoDB  Redis  MinIO  ChromaDB  Ollama              │
└───────────────────────────────────────────────────────────────────┘
      ↓
Celery Workers (4 queues: critical, ingestion, ai_analysis, default)
      ↓
MCP Server (mcp:8002) — AI assistant integration (stdio + SSE)
```

**Backend:** FastAPI + SQLAlchemy (async) + Motor (MongoDB) + Celery
**Frontend:** React 18 + Vite + TypeScript + Tailwind CSS + Zustand + SWR
**AI Layer:** LangChain ReAct agent + LangGraph multi-agent pipelines + ML classifier (scikit-learn) + rules engine
**Databases:** PostgreSQL 16 (structured), MongoDB 7 (logs/artifacts), Redis 7 (broker + streams), MinIO (S3 object store), ChromaDB (vectors)
**Observability:** OpenTelemetry → Jaeger, Prometheus, Grafana

---

## Analysis Engine Modes

The system supports three test analysis engines. Admin users toggle via **Settings > AI Configuration** or `ANALYSIS_MODE` env var:

| Mode | Engine | Latency/test | Dependencies | Use When |
|------|--------|-------------|--------------|----------|
| `llm` | LangChain ReAct + 5 tools | ~300ms | Running LLM | Maximum accuracy needed |
| `ml` | scikit-learn Gradient Boost | ~2ms | Trained model | LLM unavailable or cost concern |
| `rules` | Pattern match + statistics | ~0.2ms | None | Air-gapped, no model trained |
| `auto` | ML → LLM → Rules fallback | varies | Best available | Recommended default |

All modes produce identical output shapes (same `AIAnalysis` schema, same 4-layer summary structure), so the frontend, reports, and dashboards work identically regardless of mode.

**Dispatch:** `analysis_agent._analyse_one()` → `analysis_router.get_analysis_mode()` → engine

**ML Feature Vector (28 features):** error keywords (9), execution context (7), historical signals (8), environment signals (4). See `docs/ML_ANALYSIS_ENGINE_DESIGN.md` for full specification.

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Backend runtime | Python 3.11+ |
| Backend framework | FastAPI 0.115.5 |
| ORM | SQLAlchemy 2.0.36 (async) + asyncpg 0.30 |
| NoSQL | Motor 3.7 (MongoDB) + PyMongo 4.9 |
| Object storage | aioboto3 (MinIO/S3) |
| Background jobs | Celery 5.4 + Flower 2.0 |
| AI/LLM | LangChain 0.3.27 + LangGraph 0.6.11 |
| ML classifier | scikit-learn HistGradientBoosting (LLM-free mode) |
| Rules engine | Pattern matching + statistical heuristics (zero-dependency mode) |
| Local LLM | Ollama (qwen2.5, llama3, mistral) |
| Vector store | ChromaDB 0.5.20 |
| DB migrations | Alembic 1.14 |
| Export | OpenPyXL 3.1 + python-docx 1.1 + ReportLab 4.2 |
| Frontend framework | React 18.3 |
| Build tool | Vite 6 |
| Language | TypeScript 5.6 |
| State management | Zustand 5 |
| Data fetching | SWR 2.2 + Axios |
| UI components | Radix UI + Recharts + D3 + Lucide React |
| Styling | Tailwind CSS 3.4 |
| Linting (BE) | ruff + mypy |
| Linting (FE) | ESLint 9 + Prettier 3.4 |
| Testing (BE) | pytest 8.3 + pytest-asyncio 0.24 + pytest-cov |
| Testing (FE) | Vitest 4.1 + Playwright 1.58 + React Testing Library 16.1 |
| Tracing | OpenTelemetry SDK 1.27 → Jaeger |
| Metrics | Prometheus + prometheus-fastapi-instrumentator 7.0 |
| Dashboards | Grafana 11.3 |

---

## Common Commands

All commands are via `make` (see `Makefile` for full list):

```bash
# Development
make dev                  # Start full stack (docker compose up -d --build)
                          # Auto-creates .env from .env.example if missing
                          # Migrations run automatically at container start (with retry)
                          # Seed data runs automatically via seed-init service
make dev-setup            # First-time setup: make dev + pull-llm
make dev-lite             # Minimal stack (no Ollama/ChromaDB)
make dev-lite-stop        # Stop lite stack
make seed-data            # Re-run seed (idempotent — safe at any time)
make seed-data-reset      # Wipe and regenerate seed data
make dev-logs             # Tail all service logs
make dev-logs-seed        # Watch seed-init output
make stop                 # Stop all services
make restart              # Restart all services
make clean                # Stop + remove all volumes (destructive)

# Database
make migrate              # Run pending Alembic migrations (manual fallback)
make migrate-create MSG="name"  # Auto-generate new migration
make migrate-down         # Rollback last migration
make migrate-status       # Show current migration state

# AI/LLM
make pull-llm             # Download Ollama models (qwen2.5:7b + nomic-embed-text)
make pull-llm-large       # Download 14B models (16GB+ VRAM required)
make list-llm             # Show downloaded models

# Testing
make test-backend         # pytest tests/ -v
make test-backend-cov     # pytest with HTML coverage report
make test-frontend        # vitest
make test-e2e             # playwright
make test-agent           # AI agent unit tests (mocked tools)

# Code Quality
make lint                 # ruff check + eslint
make format               # ruff format + prettier
make type-check           # mypy + tsc

# Build & Deploy
make build                # Build production Docker images
make build-push           # Build + push to registry (REGISTRY, VERSION env vars)
make simulate-upload      # Send a sample test run to the API

# Kubernetes
make k8s-deploy-dev       # kubectl apply -k k8s/overlays/dev
make k8s-deploy-staging
make k8s-deploy-prod
make k8s-deploy-openshift # OpenShift Routes instead of Ingress
make k8s-status           # Show pods, services, ingress
make k8s-status-async     # Show worker deployments + HPAs
make k8s-scale-worker     # Manual worker scaling

# Shell
make shell-backend        # bash in backend container
make shell-db             # psql in postgres container

# MCP Server
make mcp-install          # pip install -r mcp/requirements.txt
make mcp-start            # python mcp/server.py --transport stdio
make mcp-sse              # python mcp/server.py --transport sse --port 8002
make mcp-sse-docker       # docker compose up -d mcp (port 8002)

make help                 # Show all commands
```

### Monitoring stack (optional overlay)
```bash
docker compose -f docker-compose.yml -f docker-compose.monitoring.yml up -d
```

### Without Docker (hot-reload dev)

**Backend:**
```bash
cd backend
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp ../.env .env
alembic upgrade head
uvicorn app.main:app --reload --port 8000
```

**Frontend:**
```bash
cd frontend
npm install
npm run dev   # → http://localhost:3000
```

---

## Service URLs (Local Dev)

| Service | URL |
|---------|-----|
| Dashboard | http://localhost:3000 |
| API + Swagger | http://localhost:8000/docs |
| MinIO Console | http://localhost:9001 (MINIO_ACCESS_KEY / MINIO_SECRET_KEY from .env) |
| Flower (Celery) | http://localhost:5555 |
| MCP SSE Server | http://localhost:8002/sse |
| PostgreSQL | localhost:5433 (host port remapped from 5432 to avoid conflict with local installations) |
| MongoDB | localhost:27017 |
| Redis | localhost:6379 |
| Ollama | http://localhost:11434 |
| ChromaDB | http://localhost:8001 |
| Jaeger UI | http://localhost:16686 (monitoring overlay) |
| Prometheus | http://localhost:9090 (monitoring overlay) |
| Grafana | http://localhost:3001 — admin / GF_SECURITY_ADMIN_PASSWORD from .env (monitoring overlay) |

---

## Project Structure

```
testlookup/
├── backend/
│   ├── app/
│   │   ├── main.py              # FastAPI app factory + lifespan (OTEL, Prometheus, rate limiting, MongoDB indexes)
│   │   ├── bootstrap.py         # Router registration (8 public + 41 protected) + middleware setup
│   │   ├── core/
│   │   │   ├── config.py        # Pydantic BaseSettings (all env vars) — uses Pydantic v2 SettingsConfigDict
│   │   │   ├── security.py      # JWT helpers (create_access_token, verify_token)
│   │   │   ├── deps.py          # FastAPI dependencies (get_current_active_user, require_role)
│   │   │   ├── logging_config.py # configure_logging(); structlog + OTEL trace_id injection
│   │   │   ├── tracing.py       # setup_tracing(); FastAPI/SQLAlchemy/httpx/Redis auto-instrumentation
│   │   │   └── metrics.py       # Prometheus custom metrics (counters, histograms, gauges)
│   │   ├── middleware/
│   │   │   └── telemetry.py     # TelemetryMiddleware: X-Request-ID, structured access log
│   │   ├── db/
│   │   │   ├── postgres.py      # Async SQLAlchemy engine + session factory
│   │   │   ├── mongo.py         # Motor async MongoDB client
│   │   │   ├── redis_client.py  # Redis async client
│   │   │   └── storage.py       # STORAGE_BACKEND router (minio | local)
│   │   ├── models/
│   │   │   ├── enums.py         # Shared enumerations
│   │   │   ├── postgres.py      # SQLAlchemy ORM models (all tables)
│   │   │   └── schemas.py       # Pydantic v2 request/response schemas
│   │   ├── routers/             # 51 router files
│   │   │   ├── webhooks.py      # POST /webhook/ingest
│   │   │   ├── projects.py      # CRUD for projects
│   │   │   ├── runs.py          # Test run listing and detail
│   │   │   ├── run_intelligence.py  # Run intelligence snapshots + diff
│   │   │   ├── metrics.py       # Dashboard KPI metrics
│   │   │   ├── search.py        # Full-text search across test cases
│   │   │   ├── analyze.py       # Trigger AI root-cause analysis
│   │   │   ├── analytics.py     # /flaky-tests, /failure-categories, /top-failing, /coverage, /defects, /ai-summary
│   │   │   ├── auth.py          # POST /auth/register, /auth/login (JWT), GET /auth/me, change-password
│   │   │   ├── live.py          # WebSocket /ws/live/{project_id}
│   │   │   ├── stream.py        # Live streaming: sessions, batch ingest, SSE
│   │   │   ├── integrations.py  # External integrations (Jira, etc.)
│   │   │   ├── integration_health.py  # Integration probe results + health dashboard
│   │   │   ├── agents.py        # Multi-agent pipeline control API
│   │   │   ├── agent_memory.py  # Agent memory entries CRUD
│   │   │   ├── chat.py          # Conversation agent API
│   │   │   ├── deep_investigation.py  # POST /deep-investigate, GET /clusters, GET /findings
│   │   │   ├── release_readiness.py   # GET /release-readiness, POST /override
│   │   │   ├── release_gate_policies.py # Release gate policy CRUD
│   │   │   ├── releases.py      # Release management CRUD + phases + linked runs
│   │   │   ├── users.py         # User CRUD, invite, admin-create, project members
│   │   │   ├── api_keys.py      # Scoped API key generate / list / revoke
│   │   │   ├── test_management.py          # Test case management (main)
│   │   │   ├── test_management_cases.py    # Test case CRUD
│   │   │   ├── test_management_plans.py    # Test plan management
│   │   │   ├── test_management_strategies.py # Test strategy management
│   │   │   ├── test_management_audit.py    # Test case audit trail
│   │   │   ├── test_management_ai.py       # AI-assisted test generation
│   │   │   ├── test_management_exports.py  # Test data exports
│   │   │   ├── test_management_shared.py   # Shared test management utilities
│   │   │   ├── test_health.py   # Test health scoring
│   │   │   ├── notifications.py # Notification preferences
│   │   │   ├── reports.py       # HTML / PDF / email report generation
│   │   │   ├── shared_reports.py # Public share link access (no JWT)
│   │   │   ├── feedback.py      # AI feedback + Jira webhook + training management
│   │   │   ├── app_settings.py  # Application settings
│   │   │   ├── health.py        # /health/live, /health/ready, /health/details
│   │   │   ├── observability.py # Frontend error + web vital ingestion
│   │   │   ├── sso.py           # SSO/SAML endpoints (public router)
│   │   │   ├── scim.py          # SCIM 2.0 provisioning (bearer-token auth)
│   │   │   ├── identity_events.py # Identity event log
│   │   │   ├── ownership.py     # Service ownership rules (project-scoped)
│   │   │   ├── saved_views.py   # Saved filter views per user
│   │   │   ├── digests.py       # Digest subscription management
│   │   │   ├── audit_dashboard.py # Unified audit dashboard
│   │   │   ├── ai_evaluation.py # AI evaluation datasets + runs
│   │   │   ├── scoring.py       # Scoring/criticality endpoints
│   │   │   ├── performance.py   # Performance budgets + benchmarks
│   │   │   ├── onboarding.py    # Getting started / onboarding flow
│   │   │   ├── value_metrics.py # ROI / value metrics
│   │   │   └── debug.py         # Dev-only debug endpoints
│   │   ├── services/            # 83 service files
│   │   │   ├── agent.py         # LangChain ReAct agent (5 tools, AgentExecutor with timeout)
│   │   │   ├── ingestion.py     # Orchestrates parser → DB persistence
│   │   │   ├── metrics_service.py  # Analytics queries (Python-side datetime arithmetic)
│   │   │   ├── llm_factory.py   # LLM provider switcher (ollama/openai/gemini/lmstudio/vllm)
│   │   │   ├── llm_json_parser.py  # LLM JSON response parsing
│   │   │   ├── jira_client.py   # Jira REST API integration
│   │   │   ├── allure_parser.py # Allure JSON report parser
│   │   │   ├── testng_parser.py # TestNG XML parser
│   │   │   ├── ocp_client.py    # OpenShift/K8s event client
│   │   │   ├── model_registry.py # Redis-backed hot-swap model registry
│   │   │   ├── release_service.py # Release management service
│   │   │   ├── release_council_service.py # Release council multi-signal evaluation
│   │   │   ├── release_linker.py  # Link test runs to releases
│   │   │   ├── stream_service.py  # Live streaming session management
│   │   │   ├── runs_service.py  # Test run queries + run-level logic
│   │   │   ├── run_diff_service.py  # Run-to-run comparison
│   │   │   ├── run_intelligence_service.py  # Intelligence snapshot assembly
│   │   │   ├── run_summary_service.py  # Run summary generation
│   │   │   ├── search_service.py  # Full-text search with ranking
│   │   │   ├── search_ranking.py  # Search result ranking logic
│   │   │   ├── semantic_search.py  # Vector-based semantic search
│   │   │   ├── semantic_cache.py  # LLM response caching via embeddings
│   │   │   ├── analytics_service.py  # Analytics queries
│   │   │   ├── chat_service.py  # Conversation agent service
│   │   │   ├── criticality_service.py  # Failure criticality scoring
│   │   │   ├── cluster_ranking_service.py  # Failure cluster ranking
│   │   │   ├── defect_promotion_service.py  # Promote failures to defects
│   │   │   ├── evidence_service.py  # Evidence collection
│   │   │   ├── evidence_bundle_service.py  # Evidence bundle assembly
│   │   │   ├── feedback_service.py  # AI feedback collection
│   │   │   ├── notification_service.py  # Notification dispatch
│   │   │   ├── notification/    # Notification providers
│   │   │   │   ├── email_service.py
│   │   │   │   ├── slack_service.py
│   │   │   │   ├── teams_service.py
│   │   │   │   └── manager.py   # Notification routing
│   │   │   ├── report_composition_service.py  # Report content assembly
│   │   │   ├── report_html_renderer.py  # HTML report rendering
│   │   │   ├── report_pdf_renderer.py   # PDF report rendering (ReportLab)
│   │   │   ├── report_service.py  # Report orchestration
│   │   │   ├── share_link_service.py  # Shareable report links
│   │   │   ├── access_audit_service.py  # Access audit logging
│   │   │   ├── settings_audit_service.py  # Settings change audit
│   │   │   ├── audit_dashboard_service.py  # Unified audit queries
│   │   │   ├── sso_service.py   # SSO/SAML service
│   │   │   ├── scim_service.py  # SCIM provisioning service
│   │   │   ├── secret_service.py  # Secret management
│   │   │   ├── ownership_resolver_service.py  # Service ownership resolution
│   │   │   ├── policy_evaluator_service.py  # Release gate policy evaluation
│   │   │   ├── digest_content_service.py  # Digest content generation
│   │   │   ├── integration_probe_service.py  # Integration health probes
│   │   │   ├── intelligence_snapshot_service.py  # Run intelligence snapshots
│   │   │   ├── summary_assembler.py  # Summary assembly
│   │   │   ├── summary_renderer.py  # Summary rendering
│   │   │   ├── ai_eval_service.py  # AI evaluation metrics
│   │   │   ├── eval_gate_service.py  # Evaluation gate decisions
│   │   │   ├── golden_datasets.py  # Golden dataset management
│   │   │   ├── agent_cost_service.py  # Agent execution cost tracking
│   │   │   ├── agent_memory_service.py  # Agent memory persistence
│   │   │   ├── mock_generator.py  # Test mock generation
│   │   │   ├── action_policy.py  # Action policy enforcement
│   │   │   ├── role_actions_service.py  # Role-based action resolution
│   │   │   ├── feature_flag_service.py  # Feature flag management
│   │   │   ├── input_sanitizer.py  # Input sanitization
│   │   │   ├── prompt_redaction.py  # PII redaction from prompts
│   │   │   ├── resilience.py    # Circuit breaker + retry patterns
│   │   │   ├── artifact_store.py  # Artifact storage abstraction
│   │   │   ├── onboarding_service.py  # Onboarding flow service
│   │   │   ├── performance_budgets.py  # Performance budget definitions
│   │   │   ├── pipeline_event_log.py  # Pipeline execution logging
│   │   │   ├── test_case_ai_agent.py  # AI-powered test case analysis
│   │   │   ├── test_health_coach_service.py  # Test health coaching
│   │   │   ├── test_management_ai_service.py  # AI test management
│   │   │   ├── test_management_query_service.py  # Test management queries
│   │   │   ├── test_management_service.py  # Test management CRUD
│   │   │   ├── value_metrics_service.py  # Value/ROI metrics
│   │   │   └── training/        # Continuous fine-tuning pipeline
│   │   │       ├── classifier.py  # Fast single-call failure classifier
│   │   │       ├── exporter.py    # Training data export (3 tracks)
│   │   │       ├── finetuner.py   # Provider-specific job submission
│   │   │       └── evaluator.py   # Holdout A/B evaluation gate
│   │   ├── agents/              # LangGraph multi-agent workflow (19 files)
│   │   │   ├── workflow.py      # Standard + deep LangGraph pipelines
│   │   │   ├── state.py         # WorkflowState typed dict
│   │   │   ├── base.py          # BaseAgent (stage tracking + broadcast)
│   │   │   ├── ingestion_agent.py
│   │   │   ├── anomaly_agent.py
│   │   │   ├── analysis_agent.py
│   │   │   ├── summary_agent.py
│   │   │   ├── triage_agent.py
│   │   │   ├── live_monitor.py  # Live in-memory run state monitor
│   │   │   ├── conversation.py  # RAG chat agent
│   │   │   ├── cluster_agent.py         # Semantic failure clustering
│   │   │   ├── log_intelligence_agent.py
│   │   │   ├── contract_agent.py        # API schema drift validation
│   │   │   ├── flaky_sentinel_agent.py  # Flaky lifecycle investigation
│   │   │   ├── test_health_agent.py     # Automation code quality scan
│   │   │   ├── release_risk_agent.py    # GO/NO_GO recommendation
│   │   │   ├── defect_commander.py      # Defect promotion workflow agent
│   │   │   └── regression_watchman.py   # Regression detection agent
│   │   ├── tools/               # LangChain agent tools (11 tools)
│   │   │   ├── fetch_stacktrace.py
│   │   │   ├── fetch_rest_payload.py
│   │   │   ├── query_splunk.py
│   │   │   ├── check_flakiness.py
│   │   │   ├── analyze_ocp.py
│   │   │   ├── embed_and_cluster.py     # ChromaDB + Jaccard clustering
│   │   │   ├── reconstruct_trace.py     # Splunk distributed trace reconstruction
│   │   │   ├── detect_log_anomaly.py    # Log rate anomaly vs baseline
│   │   │   ├── validate_api_contract.py # OpenAPI schema drift
│   │   │   ├── fetch_build_changes.py   # GitHub API commit lookup
│   │   │   └── fetch_app_metrics.py     # Prometheus metrics
│   │   ├── streams/             # Redis Streams infrastructure
│   │   │   ├── __init__.py      # Constants
│   │   │   ├── producer.py      # XADD publishers + batch pipeline
│   │   │   ├── live_consumer.py # Asyncio stream consumer
│   │   │   ├── live_run_state.py
│   │   │   └── circuit_breaker.py # LLM CLOSED/OPEN/HALF_OPEN guard
│   │   └── worker/
│   │       ├── celery_app.py    # Celery app + Redis broker config
│   │       ├── tasks.py         # Background tasks (AI triage, quality gates, live pipeline)
│   │       └── training_tasks.py # Export + fine-tune + trigger-check tasks
│   ├── migrations/              # Alembic migration versions (0001–0044)
│   ├── tests/                   # pytest test suite (53 test files)
│   │   ├── conftest.py
│   │   ├── test_agent.py
│   │   ├── test_agent_memory.py
│   │   ├── test_agent_reliability.py
│   │   ├── test_analysis_agent.py
│   │   ├── test_app_settings.py
│   │   ├── test_bl01_tenant_isolation.py
│   │   ├── test_bl02_secret_hardening.py
│   │   ├── test_bl03_snapshot_freshness.py
│   │   ├── test_criticality_service.py
│   │   ├── test_epic1_secure_controls.py
│   │   ├── test_epic2_intelligence_front_door.py
│   │   ├── test_epic3_baseline_explainability.py
│   │   ├── test_epic4_evidence_provenance.py
│   │   ├── test_epic5_cluster_triage.py
│   │   ├── test_epic6_admin_scalability.py
│   │   ├── test_epic7_onboarding.py
│   │   ├── test_epic8_launch_hardening.py
│   │   ├── test_epic11_quality_observability.py
│   │   ├── test_ent03_report_export.py
│   │   ├── test_ent04_ownership.py
│   │   ├── test_ent05_saved_views_digests.py
│   │   ├── test_new_features.py
│   │   ├── test_ops01_integration_health.py
│   │   ├── test_ops02_ai_eval.py
│   │   ├── test_ops03_performance.py
│   │   ├── test_ops04_audit_observability.py
│   │   ├── test_p2_features.py
│   │   ├── test_phase4_safety.py
│   │   ├── test_phase5_eval_gate.py
│   │   ├── test_phase6_observability.py
│   │   ├── test_project_and_release.py
│   │   ├── test_release_council.py
│   │   ├── test_release_gate_policies.py
│   │   ├── test_release_phases.py
│   │   ├── test_resilience.py
│   │   ├── test_role_actions_service.py
│   │   ├── test_roi01_real_defect_candidates.py
│   │   ├── test_roi02_value_metrics.py
│   │   ├── test_roi03_summary_context.py
│   │   ├── test_roi04_background_indexing.py
│   │   ├── test_run_diff_service.py
│   │   ├── test_search_ranking.py
│   │   ├── test_storage.py
│   │   ├── test_summary_assembler.py
│   │   ├── test_sso_scim.py
│   │   ├── test_test_health_coach.py
│   │   ├── test_user_management.py
│   │   └── services/            # Service-layer unit tests
│   │       ├── test_batch1_pure_services.py
│   │       ├── test_batch2_core_services.py
│   │       ├── test_batch3_clients_notifications.py
│   │       ├── test_batch4_training_ai.py
│   │       ├── test_batch5_all_projects.py
│   │       └── test_service_layer_refactors.py
│   ├── requirements.txt
│   └── Dockerfile               # Multi-stage; dev CMD auto-runs alembic upgrade head
├── frontend/
│   ├── src/
│   │   ├── App.tsx              # Router + layout + useWebVitals (42 routes)
│   │   ├── main.tsx             # Vite entry + ErrorBoundary + installGlobalErrorHandlers
│   │   ├── pages/
│   │   │   ├── OverviewPage.tsx
│   │   │   ├── OnboardingPage.tsx     # Getting started wizard
│   │   │   ├── ValueMetricsPage.tsx   # ROI / value metrics dashboard
│   │   │   ├── IntelligenceHubPage.tsx # AI intelligence hub
│   │   │   ├── ProjectsPage.tsx       # Project list + New Project modal
│   │   │   ├── RunsPage.tsx
│   │   │   ├── RunDetailPage.tsx
│   │   │   ├── RunIntelligencePage.tsx # Per-run intelligence snapshot
│   │   │   ├── SuiteDetailPage.tsx    # Test suite detail view
│   │   │   ├── TestCasePage.tsx
│   │   │   ├── SearchPage.tsx
│   │   │   ├── TrendsPage.tsx
│   │   │   ├── FailureAnalysisPage.tsx
│   │   │   ├── CoveragePage.tsx
│   │   │   ├── DefectsPage.tsx
│   │   │   ├── SettingsPage.tsx
│   │   │   ├── AgentStatusPage.tsx    # AI pipeline stage monitor
│   │   │   ├── ChatPage.tsx           # Conversation agent interface
│   │   │   ├── DeepInvestigationPage.tsx  # Cluster list + finding panel
│   │   │   ├── ReleaseGatePage.tsx    # GO/NO_GO banner + override form
│   │   │   ├── FlakyCoachPage.tsx     # Flaky test coaching
│   │   │   ├── LiveExecutionPage.tsx  # Real-time live test dashboard
│   │   │   ├── TestManagementPage.tsx # Test case management
│   │   │   ├── ReleasesPage.tsx       # Release management
│   │   │   ├── PolicyEditorPage.tsx   # Release gate policy editor
│   │   │   ├── OwnershipEditorPage.tsx # Service ownership rules
│   │   │   ├── UserManagementPage.tsx # Users + API keys (add/invite/manage)
│   │   │   ├── ProjectMembersTab.tsx  # Project member management
│   │   │   ├── LoginPage.tsx          # Authentication page
│   │   │   ├── ResetPasswordPage.tsx  # First-time password reset
│   │   │   └── settings/             # Settings sub-pages
│   │   │       ├── AIConfigPage.tsx
│   │   │       ├── AIEvalDashboardPage.tsx  # AI evaluation metrics
│   │   │       ├── AuditDashboardPage.tsx   # Unified audit log
│   │   │       ├── DigestsPage.tsx          # Digest subscriptions
│   │   │       ├── IntegrationHealthPage.tsx # Integration probe status
│   │   │       ├── IntegrationsPage.tsx     # External integrations
│   │   │       ├── NotificationsPage.tsx
│   │   │       ├── PerformancePage.tsx      # Performance budgets
│   │   │       ├── SSOSettingsPage.tsx      # SSO/SAML configuration
│   │   │       └── StoragePage.tsx
│   │   ├── components/
│   │   │   ├── ui/              # LoadingSpinner, StatusBadge, MetricCard, PageHeader, EmptyState, Pagination, AppLogo, ThemeToggle
│   │   │   ├── charts/          # PassRateGauge, DefectDonut, TrendChart
│   │   │   ├── layout/          # AppLayout, Sidebar (4 groups), TopBar
│   │   │   ├── ai/              # AIAnalysisPanel, LogViewer, CriticalityMatrix, DefectPromotionModal, RoleActionCard
│   │   │   ├── auth/            # ProtectedRoute (role gating + must_change_password redirect)
│   │   │   ├── workflow/        # WorkflowTimeline, workflowPresets
│   │   │   └── ErrorBoundary.tsx # React error boundary + backend error reporting
│   │   ├── services/            # 33 API service files
│   │   │   ├── api.ts           # Axios base instance
│   │   │   ├── http.ts          # HTTP utilities
│   │   │   ├── projectsService.ts
│   │   │   ├── runsService.ts
│   │   │   ├── runIntelligenceService.ts
│   │   │   ├── metricsService.ts
│   │   │   ├── analyticsService.ts
│   │   │   ├── aiService.ts
│   │   │   ├── aiEvalService.ts
│   │   │   ├── agentService.ts
│   │   │   ├── searchService.ts
│   │   │   ├── deepInvestigationService.ts
│   │   │   ├── liveStreamService.ts
│   │   │   ├── chatService.ts
│   │   │   ├── userManagementService.ts
│   │   │   ├── releasesService.ts
│   │   │   ├── releaseCouncilService.ts
│   │   │   ├── testManagementService.ts
│   │   │   ├── testHealthService.ts
│   │   │   ├── reportExportService.ts
│   │   │   ├── notificationService.ts
│   │   │   ├── appSettingsService.ts
│   │   │   ├── ssoService.ts
│   │   │   ├── ownershipService.ts
│   │   │   ├── policyService.ts
│   │   │   ├── savedViewsService.ts
│   │   │   ├── digestService.ts
│   │   │   ├── integrationHealthService.ts
│   │   │   ├── auditDashboardService.ts
│   │   │   ├── performanceService.ts
│   │   │   ├── onboardingService.ts
│   │   │   ├── defectPromotionService.ts
│   │   │   └── valueMetricsService.ts
│   │   ├── hooks/               # 20 custom hooks
│   │   │   ├── useRuns.ts
│   │   │   ├── useRunIntelligence.ts
│   │   │   ├── useMetrics.ts
│   │   │   ├── useDeepInvestigation.ts
│   │   │   ├── useDefectPromotion.ts
│   │   │   ├── useLiveExecution.ts
│   │   │   ├── useUserManagement.ts
│   │   │   ├── usePermissions.ts    # canManageUsers, canGenerateApiKeys, isAdmin
│   │   │   ├── useWebVitals.ts      # CLS/FID/LCP/FCP/TTFB/INP reporting
│   │   │   ├── useTestManagement.ts
│   │   │   ├── useTestHealth.ts
│   │   │   ├── useReleases.ts
│   │   │   ├── useReleaseCouncil.ts
│   │   │   ├── useAgentRuns.ts
│   │   │   ├── useChat.ts
│   │   │   ├── useNotifications.ts
│   │   │   ├── useProjectChange.ts
│   │   │   └── useProjectScopedSWR.ts  # Project-scoped SWR helper
│   │   ├── store/
│   │   │   ├── projectStore.ts  # Zustand: selected project + project list
│   │   │   ├── authStore.ts     # Zustand: current user + JWT token
│   │   │   └── themeStore.ts    # Zustand: dark/light theme toggle
│   │   └── utils/
│   │       ├── formatters.ts
│   │       └── errorReporting.ts  # installGlobalErrorHandlers, reportBoundaryError, reportWebVital
├── mcp/                         # MCP Server (20 tools, 10 resources, 6 prompts)
│   ├── server.py                # MCP protocol server (SSE/stdio)
│   ├── client.py                # Backend API client
│   ├── config.py                # MCP configuration
│   ├── Dockerfile
│   ├── requirements.txt
│   └── Makefile
├── infra/
│   ├── monitoring/              # Prometheus, Grafana, alerting rules
│   │   ├── prometheus.yml       # Scrape config (backend:8000, worker:9191)
│   │   ├── prometheus-rules/    # Alert rules
│   │   └── grafana/             # Dashboards + provisioning
│   └── cloudrun/                # GCP Cloud Run build configs
│       ├── cloudbuild.backend.yaml
│       ├── cloudbuild.frontend.yaml
│       └── cloudbuild.mcp.yaml
├── k8s/
│   ├── base/                    # Namespace, ConfigMap, Secrets, RBAC, Deployments, HPA, PDB, Services, Ingress
│   └── overlays/                # dev, staging, prod, openshift
├── client/
│   └── testlookup_reporter.py   # Python client SDK + pytest plugin
├── scripts/
│   ├── seed_dev_data.py         # Database seeding
│   ├── init-db.sql              # PostgreSQL initialization
│   ├── setup-minio.sh           # MinIO bucket/webhook config
│   ├── simulate-upload.sh       # Test upload simulation (bash)
│   ├── simulate_upload.py       # Test upload simulation (python)
│   ├── simulate_live_stream.py  # Live stream simulation
│   ├── local_dev_setup_macos.sh
│   ├── local_dev_setup_unix.sh
│   ├── local_dev_setup_windows.ps1
│   └── local_dev_setup_common.sh
├── docs/                        # 21 documentation files
│   ├── DEVELOPMENT.md
│   ├── JENKINS_PIPELINE.md
│   ├── LAUNCH_SIGNOFF_MATRIX.md
│   ├── LAUNCH_READINESS_CHECKLIST.md
│   ├── MULTI_CLOUD_DEPLOYMENT_STRATEGY.md
│   ├── local_dev_deployment.md
│   ├── cloud-run-cloud-sql.md
│   ├── RUNSCOPE_AI_ENGINEERING_BLUEPRINT.md
│   ├── RUNSCOPE_AI_QUARTERLY_ROADMAP.md
│   ├── RUNSCOPE_AI_ROADMAP_ONE_PAGER.md
│   ├── RUNSCOPE_AI_FEATURE_INVENTORY.md
│   ├── RUNSCOPE_AI_PERSONAS_JTBD_AND_USER_JOURNEYS.md
│   ├── RUNSCOPE_AI_USER_STORIES_AND_ACCEPTANCE_CRITERIA.md
│   ├── RUNSCOPE_AI_NON_FUNCTIONAL_REQUIREMENTS.md
│   ├── RUNSCOPE_AI_REQUIREMENTS_TRACEABILITY_MATRIX.md
│   ├── RUNSCOPE_AI_PRODUCT_REQUIREMENTS_DOCUMENT.md
│   ├── RUNSCOPE_AI_RENAME_AND_TRANSITION_PLAN.md
│   ├── RUNSCOPE_AI_DOCS_INDEX.md
│   └── deep-research-report.md
├── .github/
│   ├── workflows/
│   │   ├── ci.yml               # GitHub Actions CI/CD (test → build → deploy-dev)
│   │   └── codacy.yml           # Codacy security scanning (bandit → SARIF)
│   └── dependabot.yml           # Weekly dependency updates (pip, npm, github-actions)
├── Jenkinsfile                  # Jenkins CI/CD pipeline (test → build → push → deploy to VM)
├── docker-compose.yml           # Local dev stack — 14 services (auto-runs migrations at startup)
├── docker-compose.monitoring.yml # Jaeger + Prometheus + Grafana overlay
├── docker-compose.dev-lite.yml  # Lightweight stack (no Ollama/ChromaDB)
├── docker-compose.gcp-vm.yml   # GCP VM production override
├── Makefile
├── .codacy.yaml                 # Security scanning config
├── .env.example
├── .env.gcp-vm.example
├── SECURITY.md
├── SECURITY_UPDATES.md
├── deploymentsteps.md
└── deployment_and_testing_strategy.md
```

---

## Environment Configuration

Copy `.env.example` to `.env` and configure (~165 variables):

| Variable | Description |
|----------|-------------|
| `APP_ENV` | development / staging / production |
| `APP_SECRET_KEY` | Application secret key |
| `LLM_PROVIDER` | ollama \| openai \| gemini \| lmstudio \| localai \| vllm |
| `LLM_MODEL` | Model name (e.g., qwen2.5:7b, gpt-4o) |
| `LLM_TEMPERATURE` | LLM temperature setting |
| `AI_OFFLINE_MODE` | true = Ollama only, no internet calls |
| `AI_CONFIDENCE_THRESHOLD` | Minimum confidence (default 80) |
| `AI_TIMEOUT_SECONDS` | Agent execution timeout |
| `STORAGE_BACKEND` | minio \| local (required — no default) |
| `POSTGRES_*` | PostgreSQL connection settings (host, port 5433, db, user, password) |
| `MONGO_*` | MongoDB connection settings |
| `REDIS_*` | Redis broker settings |
| `MINIO_*` | Object storage settings (endpoint, keys, bucket, SSL) |
| `CHROMA_*` | Vector store settings (host, port, collection) |
| `EMBEDDING_PROVIDER` | ollama \| openai |
| `EMBEDDING_MODEL` | nomic-embed-text \| text-embedding-3-small |
| `JIRA_*` | Jira integration — JIRA_ENABLED, domain, email, token, project key (optional) |
| `SPLUNK_*` | Splunk log query — SPLUNK_ENABLED, base_url, token, index (optional) |
| `OCP_*` | OpenShift/K8s — OCP_ENABLED, api_url, sa_token, namespace (optional) |
| `SLACK_*` | Slack — SLACK_ENABLED, bot_token, webhook_url, channel (optional) |
| `TEAMS_*` | Teams — TEAMS_ENABLED, webhook_url (optional) |
| `SMTP_*` | Email — SMTP_ENABLED, host, port, user, password, from (optional) |
| `JWT_SECRET_KEY` | Must be a strong random value in prod |
| `JWT_ALGORITHM` | Token algorithm (default HS256) |
| `JWT_ACCESS_TOKEN_EXPIRE_MINUTES` | Token TTL |
| `DEEP_INVESTIGATION_ENABLED` | true \| false — enables deep LangGraph pipeline |
| `OTEL_ENABLED` | true \| false — enables OpenTelemetry tracing |
| `METRICS_ENABLED` | true \| false — enables Prometheus metrics endpoint |
| `FINETUNE_ENABLED` | true \| false — enables continuous fine-tuning pipeline |
| `DEV_AUTO_LOGIN_ENABLED` | true \| false — enables `/api/v1/auth/dev-login` (dev only) |
| `SSO_ENABLED` | true \| false — enables SSO/SAML authentication |
| `SCIM_ENABLED` | true \| false — enables SCIM 2.0 provisioning endpoints |
| `SAML_SP_ENTITY_ID` | SP entity ID for SAML metadata |
| `SAML_BASE_URL` | Base URL for ACS/SLO URL construction |
| `SSO_ADMIN_FALLBACK_ENABLED` | true \| false — allow admin password login when SSO enforced |
| `CELERY_WORKER_CONCURRENCY` | Worker concurrency (default 4) |
| `LOG_LEVEL` | DEBUG \| INFO \| WARNING \| ERROR |
| `LOG_FORMAT` | json \| text |
| `SEARCH_*` | Search tuning: INDEX_BATCH_SIZE, INCREMENTAL_LIMIT, QUERY_TIMEOUT_MS, MAX_RESULTS |
| `GF_SECURITY_ADMIN_PASSWORD` | Grafana admin password (monitoring overlay) |
| `E2E_ADMIN_PASSWORD` | E2E test password (when dev-login disabled) |

---

## Database Migrations

Migrations run **automatically** at container startup (`alembic upgrade head` is prepended to the uvicorn CMD in both `Dockerfile` and `docker-compose.yml`). No manual step is required after `make dev`.

Current migrations (44 total):

| Revision | Description |
|----------|-------------|
| 0001 | Initial schema (users, projects, test_runs, test_cases) |
| 0002 | Notification tables |
| 0003 | Agent tables (chat sessions, pipeline runs) |
| 0004 | Performance indexes |
| 0005 | AI feedback + model registry |
| 0006 | Deep investigation (failure_clusters, deep_findings, release_decisions, contract_violations) |
| 0007 | Test case management |
| 0008 | Live sessions |
| 0009 | Release management |
| 0010 | Live session release name |
| 0011 | App settings |
| 0012 | User management (project_members, api_keys, user_invitations) |
| 0013 | Managed test case suite name |
| 0014 | User must_change_password |
| 0015 | AI enhancements |
| 0016 | Role actions |
| 0017 | Pipeline metadata |
| 0018 | Criticality + regression |
| 0019 | Defect promotion |
| 0020 | Component owner map |
| 0021 | Release council + test health |
| 0022 | Project dates + metadata |
| 0023 | Secret refs + settings audit |
| 0024 | Run intelligence snapshots |
| 0025 | Run baselines + diffs |
| 0026 | Evidence + provenance |
| 0027 | Defect candidates + promotion source |
| 0028 | Access audit logs |
| 0029 | Onboarding + usage events |
| 0030 | Feature flags + health checks |
| 0031 | SSO/SAML/SCIM (sso_configurations, federated_identities, scim_tokens, identity_events) |
| 0032 | Release gate policies (release_gate_policies + release_decisions policy columns) |
| 0033 | Report share links (report_share_links for time-limited shareable reports) |
| 0034 | Service ownership rules (service_ownership_rules for component/team routing) |
| 0035 | Saved views + digest subscriptions (saved_views, digest_subscriptions) |
| 0036 | Integration probe results (integration_probe_results + health check columns) |
| 0037 | Tenant metric snapshots (tenant_metric_snapshots for project-scoped observability) |
| 0038 | AI evaluation datasets and runs (ai_eval_datasets, ai_eval_runs) |
| 0039 | Stage checkpoint data |
| 0040 | Agent memory entries |
| 0041 | Defect approval workflow |
| 0042 | AI evaluation baselines |
| 0043 | Agent stage observability |
| 0044 | Defect open unique index |

---

## Coding Conventions

### Backend

- **Pydantic v2 only.** Use `@field_validator(..., mode="before")` + `@classmethod` for validators. Use `model_config = SettingsConfigDict(...)` in Settings. Never use deprecated v1 `@validator` or `class Config`.
- **Async everywhere.** All DB calls, HTTP calls, and service methods must be `async def`. SQLAlchemy sessions use `async with AsyncSession` from `backend/app/db/postgres.py`.
- **No SQL INTERVAL string literals with parameters.** PostgreSQL cannot bind params inside string literals like `INTERVAL ':days days'`. Always compute `period_start` in Python (`datetime.now(timezone.utc) - timedelta(days=days)`) and pass as a bound param.
- **Router pattern:** Thin routers — business logic belongs in `services/`, not routers. Routers only handle HTTP concerns (status codes, request parsing, dependency injection).
- **Celery tasks** in `worker/tasks.py` are fire-and-forget — they accept simple serializable args (IDs, dicts), not ORM objects. 4 queues: `critical`, `ingestion`, `ai_analysis`, `default`.
- **AgentExecutor** must include `max_execution_time=settings.AI_TIMEOUT_SECONDS` to prevent runaway LLM calls.
- **Role-based access:** Use `require_role(UserRole.X)` as a dependency. Role hierarchy: VIEWER < TESTER < QA_ENGINEER < QA_LEAD < ADMIN.
- **API key storage:** Keys are stored as SHA-256 hashes. The raw key is only returned once at creation. `key_hint` = `raw_key[:8] + "..."` (max 11 chars; column is `String(12)`).
- **Structured logging:** Use `structlog.get_logger(__name__)` — never `print()` or raw `logging.getLogger()`.
- **Input sanitization:** Use `input_sanitizer.py` for user-provided text. Use `prompt_redaction.py` to strip PII from LLM prompts.

### Frontend

- **SWR for all data fetching.** Add hooks in `hooks/` that wrap `useSWR`; pages import hooks, not raw service calls directly. Use `useProjectScopedSWR` for project-filtered queries.
- **Zustand for global state.** Project selection in `projectStore.ts`, auth in `authStore.ts`, theme in `themeStore.ts`. Per-page state stays local.
- **`api.ts` is the Axios base.** All service files import from `services/api.ts`. Never create a second Axios instance.
- **TestCase breadcrumbs** use `runId?.slice(0,8)` — the `build_number` field lives on `TestRun`, not `TestCase`.
- **TypeScript strict mode is on** — avoid `any`; use `unknown` + type guards when necessary.
- **Sidebar navigation** has four groups: Dashboard, Testing, AI Intelligence, Management. Settings is in the footer (admin/QA_LEAD only).

---

## Known Pitfalls

These bugs have been encountered and fixed — avoid reintroducing them:

1. **SQL INTERVAL parameterization** — `INTERVAL ':days days'` does NOT work in PostgreSQL. Use Python `timedelta` instead. See `services/metrics_service.py` and `routers/search.py`.

2. **Pydantic v1 syntax** — `@validator` and `class Config` are removed in Pydantic v2. All validators in `core/config.py` use `@field_validator`.

3. **Missing `.env`** — `make dev` uses a Make file-target (`.env:`) that copies `.env.example` → `.env` only when `.env` is absent. This works on all platforms without shell tests. If you bypass Make, run `cp .env.example .env` manually. `STORAGE_BACKEND` defaults to `minio` in `config.py` but must be in `.env` so Docker Compose substitution works.

4. **Seed script location** — `make seed-data` runs `python /app/scripts/seed_dev_data.py` inside the backend container. The volume mount `./backend:/app` means the script must live at `backend/scripts/seed_dev_data.py` — NOT `scripts/seed_dev_data.py` (repo root). The seed also runs automatically via the `seed-init` Docker Compose service on every `make dev`.

5. **AgentExecutor timeout** — Without `max_execution_time`, a slow Ollama model will hang the request indefinitely.

6. **TestCase `build_number`** — This field is on `TestRun`, not `TestCase`. Don't reference `tc.build_number`.

7. **API key `key_hint` column width** — The `api_keys.key_hint` column is `String(12)`. The key hint must be built as `raw_key[:8] + "..."` (11 chars). Using `[:10]` produces 13 chars and causes a PostgreSQL string overflow error.

8. **Migration retry loop** — The backend startup command now retries `alembic upgrade head` every 5 seconds until it succeeds. If you see "relation does not exist" errors, watch `make logs` — the retry will resolve it once PostgreSQL finishes init. Run `make migrate` if you need to force it immediately.

9. **UserRole stored as String(20)** — The `role` columns in `project_members`, `api_keys`, and `user_invitations` are `String(20)` (not a native PostgreSQL enum), so `UserRole(str, Enum)` values serialize/deserialize transparently.

10. **All-Projects mode** — Several pages support an `ALL_PROJECTS_ID = "all"` sentinel value from `projectStore`. Backend optional `project_id` query params must be `Optional[uuid.UUID] = None` and the `None` path must omit the WHERE filter entirely. Never pass the literal string `"all"` to the backend as a UUID.

11. **Live session token auth** — The `/stream/events/batch` hot path uses `X-Session-Token` header (Redis O(1) lookup), not JWT. Do not accidentally require JWT middleware on that endpoint.

12. **Dev auto-login endpoint** — `POST /api/v1/auth/dev-login` returns 404 unless `APP_ENV=development` AND `DEV_AUTO_LOGIN_ENABLED=true`. It lives in the public `auth` router so it is reachable before authentication. Never gate it behind JWT middleware.

13. **Self-registration role + must_change_password** — `POST /api/v1/auth/register` always creates users with `role=VIEWER` and `must_change_password=True`. The `POST /api/v1/auth/first-time-reset` endpoint (requires JWT, no current password) clears this flag. `ProtectedRoute` redirects any authenticated user with `must_change_password=True` to `/reset-password`. Migration `0014` adds the `must_change_password` column to the `users` table.

14. **SSO enforcement + admin fallback** — When `SSOEnforcementMode.SSO_REQUIRED` is active, only ADMIN users can use password login (if `SSO_ADMIN_FALLBACK_ENABLED=true`). Non-admin password login is blocked with 403. The SSO router (`/api/v1/sso/*`) and SCIM router (`/api/v1/scim/v2/*`) are registered as PUBLIC routers (no JWT required); SCIM uses its own bearer token auth, and SSO admin endpoints require `require_role(UserRole.ADMIN)` internally.

15. **SCIM token `token_hint` column width** — Same pattern as API key: `raw_token[:8] + "..."` = 11 chars, column is `String(12)`. The SCIM token prefix is `scim_` so hints look like `scim_abc...`.

16. **SSO certificate in responses** — Never expose the raw IdP X.509 certificate in API responses. `SSOConfigResponse` uses `idp_certificate_fingerprint` (SHA-256 hex digest) instead. The raw certificate is only accepted on create/update.

17. **Release gate policy backward compatibility** — When no `ReleaseGatePolicy` is configured, the system falls back to hardcoded thresholds from `config.py` (GO < 20, NO_GO >= 55, pass_rate_minimum = 90%). Policy precedence is: project-specific → system default (project_id IS NULL) → hardcoded. The `criticality_service.py` functions `compute_composite()` and `score_to_recommendation()` accept optional keyword-only policy parameters; existing callers using positional args are unaffected.

18. **Policy dimension weights sum** — The `dimension_weights` in a `PolicyDocument` must sum to 1.0 (within ±0.01 tolerance). The router validates this on create/update. The frontend also validates client-side with a live sum indicator.

19. **Report share link tokens** — Share tokens are generated with `secrets.token_urlsafe(48)` (64-char base64). The `shared_reports.router` is registered as a PUBLIC router (no JWT) since share links are token-authenticated. Share links have expiry (1-30 days, default 7) and can be revoked. All export/share actions are audited via `AccessAuditLog`.

20. **Report composition uses cached snapshot** — `report_composition_service.py` reads from `RunIntelligenceSnapshot.payload` (cached JSON), not from individual tables. This avoids expensive re-queries and ensures the report reflects the same data the user saw on the intelligence page. If no snapshot exists, a `ValueError` is raised.

21. **Ownership resolution hierarchy** — `ServiceOwnershipRule` rules are evaluated highest-priority-first using glob matching (`fnmatch`). Fallback chain: rules → `Project.component_owner_map` (legacy) → `TestCase.owner` (Allure label) → "Unassigned". For clusters, majority voting across member tests determines the owning team. The `ownership` router is project-scoped (`/api/v1/projects/{project_id}/ownership/...`).

22. **Integration health probes** — The Celery beat task `run_integration_health_probes` runs every 15 minutes, probing 9 providers (Jira, Splunk, GitHub, OCP, Slack, Teams, SMTP, Ollama, ChromaDB) concurrently. Probes check auth validity, response latency, and payload correctness. Results persist to `integration_probe_results` (history) and upsert `integration_health_checks` (latest). The `integration_health_gauge` Prometheus metric is populated on each run. Alerts log at WARNING when `consecutive_failures >= 3`. Disabled integrations return `skipped` status.

23. **Unified audit dashboard and redaction** — `audit_dashboard_service.py` queries across all 5 audit tables (AccessAuditLog, SettingsAuditLog, TestCaseAuditLog, IdentityEvent, plus report events). Sensitive values are redacted by key name pattern (password, token, secret, etc.) and value content pattern (bearer, authorization). Tenant isolation enforced via `get_accessible_project_ids()` — non-admin users only see their projects' events. CSV export applies redaction before download. The `TenantMetricSnapshot` table stores per-project observability metrics.

24. **Performance budgets and load testing** — `performance_budgets.py` defines codified latency (p50/p95/p99) and throughput budgets for 11 operations and 3 scale scenarios (small_team/mid_enterprise/large_enterprise). `scripts/load_test_harness.py` generates synthetic test data and benchmarks API endpoints against budgets. Config settings `SEARCH_INDEX_BATCH_SIZE`, `SEARCH_INDEX_INCREMENTAL_LIMIT`, `SEARCH_QUERY_TIMEOUT_MS`, `SEARCH_MAX_RESULTS` are tunable via environment variables.

25. **AI evaluation dashboards** — `ai_eval_service.py` computes macro-average precision/recall/F1/accuracy from labeled datasets. Datasets can be auto-generated from human feedback (AIFeedback records with `correct`/`incorrect` ratings). Drift detection compares current vs previous evaluation window accuracy. The `AIEvalRun` table persists every evaluation with metrics. The dashboard at `/settings/ai-eval` shows agreement rate, drift direction, recent eval runs, and model version history.

26. **Digest subscriptions and saved views** — `DigestSubscription` stores user-level scheduled delivery config (DAILY or WEEKLY via email/slack/teams). The Celery beat task `dispatch_scheduled_digests` runs daily at 07:00 UTC, queries subscriptions where `next_delivery_at <= now`, generates content via `digest_content_service.generate_digest()`, and delivers via the notification email service. `SavedView` stores per-user filter configs with personal/shared visibility. Both tables are user-owned with project-scoped filtering.

27. **Agent memory persistence** — `agent_memory_service.py` persists agent memory entries to `agent_memory_entries` table (migration 0040). Memory is scoped per project and agent type. The `agent_memory` router provides CRUD endpoints.

28. **Defect approval workflow** — Migration 0041 adds defect approval workflow columns. `defect_commander.py` agent orchestrates promotion from failure cluster → defect candidate → approved defect. The `defect_promotion_service.py` handles the state machine.

29. **AI evaluation baselines** — Migration 0042 adds baseline comparison for AI evaluation. `eval_gate_service.py` compares current eval metrics against baselines to decide if model quality is acceptable.

30. **Agent stage observability** — Migration 0043 adds stage-level observability for agent pipelines. `pipeline_event_log.py` records per-stage timing, tokens used, and errors.

---

## Sidebar Navigation Structure

```
[Logo / TestLookup v0.0.1]
─────────────────────────────
  DASHBOARD
Overview                (primary)
Getting Started
Value Metrics
─────────────────────────────
  TESTING
Runs                    (primary)
Live
Coverage
Failures
Trends
Defects
Search
Test Cases
─────────────────────────────
  AI INTELLIGENCE
Intelligence Hub        (primary)
AI Pipeline
Deep Analysis
Release Gate
Flaky Coach
Chat
─────────────────────────────
  MANAGEMENT            (QA_LEAD+ only)
Projects                (primary)
Releases
Policies
Ownership
Users
─────────────────────────────
Settings                (footer, QA_LEAD+ only)
```

---

## User Management

### Auth endpoints (public — no JWT required)

| Method | Path | Description |
|--------|------|-------------|
| POST | /api/v1/auth/register | Self-service registration → VIEWER role, must_change_password=True |
| POST | /api/v1/auth/login | Standard login |
| POST | /api/v1/auth/first-time-reset | Forced reset on first login (JWT required, no old password needed) |
| POST | /api/v1/auth/dev-login | Dev-only bypass login (APP_ENV=development only) |

### Self-registration flow
1. User fills in the Register form on the login page (email, username, full name, password)
2. Account is created with `role=VIEWER` (read-only) and `must_change_password=True`
3. User logs in; `ProtectedRoute` detects `must_change_password=True` and redirects to `/reset-password`
4. User sets a permanent password via `POST /auth/first-time-reset` (no old password required)
5. `must_change_password` is cleared; user proceeds to the dashboard
6. An admin can promote the user's role via the User Management page

### Endpoints (all require JWT)

| Method | Path | Min Role | Description |
|--------|------|----------|-------------|
| GET | /api/v1/users | QA_LEAD | List all users |
| POST | /api/v1/users | ADMIN | Create user directly (returns one-time temp password) |
| POST | /api/v1/users/invite | ADMIN | Invite user by email (returns invitation link) |
| PATCH | /api/v1/users/{id}/role | ADMIN | Update user role |
| PATCH | /api/v1/users/{id}/status | ADMIN | Activate / deactivate user |
| GET | /api/v1/keys | QA_ENGINEER | List own API keys |
| POST | /api/v1/keys | QA_ENGINEER | Generate API key (raw key shown once) |
| DELETE | /api/v1/keys/{id} | QA_ENGINEER | Revoke API key |

### Admin direct user creation
`POST /api/v1/users` creates a user immediately with a randomly generated temporary password (`secrets.token_urlsafe(12)`). The password is returned in the response body **once** — the admin must copy and share it with the user, who should change it via Settings > Change Password.

---

## Adding New Features

### New API endpoint
1. Define Pydantic schemas in `backend/app/models/schemas.py`
2. Create router in `backend/app/routers/<feature>.py`
3. Register in `backend/app/bootstrap.py` under `PROTECTED_ROUTERS` (or `PUBLIC_ROUTERS`)
4. Add service logic in `backend/app/services/<feature>.py`
5. Write tests in `backend/tests/test_<feature>.py`
6. Add frontend API service in `frontend/src/services/<feature>Service.ts`
7. Create SWR hook in `frontend/src/hooks/use<Feature>.ts`
8. Build page in `frontend/src/pages/<Feature>Page.tsx`
9. Add route in `frontend/src/App.tsx`
10. Add nav entry to appropriate sidebar group in `frontend/src/components/layout/Sidebar.tsx`

### New DB table
1. Add ORM model to `backend/app/models/postgres.py`
2. Create migration `backend/migrations/versions/<next_num>_<name>.py` (next is 0045)
3. Migrations run automatically on next container start

### New LangChain agent tool
- Add tool file under `backend/app/tools/`
- Register in `backend/app/services/agent.py`

### New deep pipeline agent
- Subclass `BaseAgent` in `backend/app/agents/`
- Add stage to `WorkflowState` in `agents/state.py`
- Wire as a node in `_build_deep_graph()` in `agents/workflow.py`

---

## Observability

### Starting the monitoring stack
```bash
docker compose -f docker-compose.yml -f docker-compose.monitoring.yml up -d
```

### URLs
| Service | URL |
|---------|-----|
| Jaeger (distributed traces) | http://localhost:16686 |
| Prometheus | http://localhost:9090 |
| Grafana | http://localhost:3001 (admin / GF_SECURITY_ADMIN_PASSWORD from .env) |
| Backend /metrics | http://localhost:8000/metrics |
| /health/live | http://localhost:8000/health/live |
| /health/ready | http://localhost:8000/health/ready |
| /health/details | http://localhost:8000/health/details |

### Frontend observability
- `ErrorBoundary.tsx` catches React render errors and POSTs to `/api/v1/observability/frontend`
- `useWebVitals.ts` reports CLS, FID, LCP, FCP, TTFB, INP
- `errorReporting.ts` installs `window.onerror` + `unhandledrejection` handlers

---

## Live Streaming Architecture

```
Client SDK (testlookup_reporter.py)
  → batch events every 100ms (up to 50/call)
  → POST /api/v1/stream/events/batch  (X-Session-Token auth, O(1) Redis GET)
      ↓ Redis pipeline (all XADDs in one round-trip)
Redis Streams: testlookup:stream:live_events
      ↓ LiveEventStreamConsumer
   WebSocket /ws/live/{project_id}  +  SSE GET /api/v1/stream/sse/{project_id}
      ↓ Dashboard viewers
On run complete → run_agent_pipeline Celery task → PostgreSQL persistence
```

---

## CI/CD

### GitHub Actions (`.github/workflows/ci.yml`)
- **Triggers:** Push to `main`/`develop`, PRs to `main`
- **backend-test:** ruff → mypy → pytest (with postgres + redis services) + codecov upload
- **frontend-test:** eslint → tsc → vitest → build
- **mcp-test:** syntax check + module imports validation
- **build-images:** (main branch only) Docker Buildx → push to GHCR (`ghcr.io/<org>/<repo>`) with SHA + latest tags
- **deploy-dev:** (after build) kubectl apply via kubeconfig secret, wait for rollout, run migrations

### Codacy Security Scanning (`.github/workflows/codacy.yml`)
- Runs bandit on backend/ Python code → SARIF → GitHub Code Scanning

### Dependabot (`.github/dependabot.yml`)
- Weekly updates for pip (backend), npm (frontend), github-actions

### Jenkins Pipeline (`Jenkinsfile`)
- Parameterized: RUN_TESTS, BUILD_IMAGES, PUSH_IMAGES, DEPLOY_TO_VM, DEPLOY_PROFILE (standard|async)
- Stages: Checkout → Env prep → Validate → Test (backend/frontend/MCP) → Build → Push → Deploy to GCP VM (SSH)
- Post: always cleanup (docker compose down, cleanWs)

---

## Kubernetes Deployment

Uses **Kustomize** with base + overlays pattern:

```bash
make k8s-deploy-dev       # 1 replica, debug logging
make k8s-deploy-staging   # 2 replicas, info logging
make k8s-deploy-prod      # 3 replicas backend, 2 frontend, error logging
make k8s-deploy-openshift # OpenShift Routes (no Ingress)
make k8s-status           # Show pods, services, ingress
```

Namespace: `testlookup`

### Queue-specific workers (K8s)
- `testlookup-worker-critical` — critical queue, concurrency=2
- `testlookup-worker-ingestion` — ingestion queue, concurrency=4, grace=120s
- `testlookup-worker-ai` — ai_analysis queue
- `testlookup-worker-default` — default queue
- `testlookup-beat` — Celery beat scheduler (1 replica only)

### HorizontalPodAutoscalers
- Backend: 2-8 replicas (70% CPU, 80% memory)
- Frontend: 1-4 replicas (70% CPU)
- Worker-critical: 1-4 replicas (60% CPU, 300s scale-down)
- Worker-ingestion: 1-6 replicas (70% CPU)
- Worker-ai: 1-4 replicas (60% CPU, 600s scale-down for long AI pipelines)
- Worker-default: 1-3 replicas (70% CPU)
- MCP: 1-3 replicas (70% CPU)

### Security
- Non-root containers (runAsUser=1000)
- PodDisruptionBudgets for graceful drains
- RBAC: ServiceAccount + Role + RoleBinding for pod inspection
- Rolling updates: maxUnavailable=0, maxSurge=1
- Topology spread: hostname + zone aware

---

## Docker Compose Services (14 total)

| Service | Image | Port(s) | Purpose |
|---------|-------|---------|---------|
| postgres | postgres:16-alpine | 5433→5432 | Structured data |
| mongo | mongo:7 | 27017 | Logs/artifacts |
| redis | redis:7-alpine | 6379 | Broker + streams (512MB, LRU) |
| minio | minio/minio | 9000, 9001 | Object storage |
| minio-setup | minio/mc | — | One-shot bucket init |
| ollama | ollama/ollama | 11434 | Local LLM (optional GPU) |
| chromadb | chromadb/chroma | 8001→8000 | Vector store |
| backend | ./backend | 8000 | FastAPI app |
| worker | ./backend | — | Celery workers (4 queues) |
| beat | ./backend | — | Celery beat scheduler |
| flower | mher/flower | 5555 | Worker monitoring |
| frontend | ./frontend | 3000 | React SPA |
| seed-init | ./backend | — | One-shot seed (runs once) |
| mcp | ./mcp | 8002 | MCP protocol server |

Resource limits configured per service. Backend: 2 CPU / 2GB. Worker: 2 CPU / 3GB. PostgreSQL/MongoDB: 2 CPU / 4GB.

---

## AI Agent Tools (11 total)

The LangChain ReAct agent (`backend/app/services/agent.py`) has 5 standard tools. The deep pipeline adds 6 specialist tools.

### Standard tools (5)

| Tool | Purpose |
|------|---------|
| `fetch_stacktrace` | Retrieve full stack trace from MongoDB |
| `fetch_rest_payload` | Get request/response payloads |
| `query_splunk` | Search Splunk logs for time-window around test execution |
| `check_flakiness` | Query PostgreSQL for historical flakiness rate |
| `analyze_ocp` | Fetch OpenShift pod events for infra context |

### Deep investigation tools (6)

| Tool | Purpose |
|------|---------|
| `embed_and_cluster` | ChromaDB + Jaccard semantic failure clustering |
| `reconstruct_distributed_trace` | Multi-service Splunk log correlation |
| `detect_log_anomaly` | ERROR/WARN rate vs 7-day baseline |
| `validate_api_contract` | OpenAPI schema drift from MongoDB REST payloads |
| `fetch_build_changes` | GitHub API commits between builds |
| `fetch_app_metrics` | Prometheus range query — CPU, memory, error rate, P99 |

---

## MCP Server

The `mcp/` directory contains a Model Context Protocol server that exposes TestLookup to Codex Desktop, IDEs, and CI pipelines.

### Running Locally (Codex Desktop — stdio)

```bash
make mcp-install      # pip install -r mcp/requirements.txt
make mcp-start        # python mcp/server.py --transport stdio
```

### Running as SSE Service (CI / web clients)

```bash
make mcp-sse           # python mcp/server.py --transport sse --port 8002
make mcp-sse-docker    # docker compose up -d mcp  (port 8002)
```

SSE endpoint: `http://localhost:8002/sse`

### Available Tools (20)

| Group | Tools |
|-------|-------|
| Auth | `login`, `health_check` |
| Projects | `list_projects`, `get_project`, `create_project` |
| Runs | `list_test_runs`, `get_run_details`, `list_test_cases`, `get_test_case` |
| Metrics | `get_dashboard_metrics`, `get_test_trends` |
| Analytics | `get_flaky_tests`, `get_failure_categories`, `get_top_failing_tests`, `get_coverage_report`, `get_defects`, `get_ai_analysis_summary` |
| Analysis | `trigger_ai_analysis`, `search_tests` |
| Release | `check_release_readiness` |

### Available Prompts (6)

| Prompt | Purpose |
|--------|---------|
| `investigate_failure` | Full root-cause investigation for a failing test |
| `release_readiness_report` | Executive go/no-go report for a release |
| `weekly_quality_digest` | Weekly quality summary for team sharing |
| `flakiness_investigation` | Deep-dive on flaky tests with remediation plan |
| `defect_triage_session` | Structured defect triage with prioritisation |
| `suite_health_check` | Health report for a specific test suite |

---

## Documentation

| File | Purpose |
|------|---------|
| `README.md` | Overview, features, architecture, quick start |
| `SECURITY.md` | Security policy |
| `SECURITY_UPDATES.md` | Security update procedures |
| `installation.md` | GCP VM deployment guide |
| `deploymentsteps.md` | Detailed deployment procedures |
| `deployment_and_testing_strategy.md` | Testing & deployment strategy |
| `docs/DEVELOPMENT.md` | Developer workflow, iterative phases |
| `docs/JENKINS_PIPELINE.md` | Jenkins pipeline documentation |
| `docs/LAUNCH_SIGNOFF_MATRIX.md` | Launch sign-off matrix |
| `docs/LAUNCH_READINESS_CHECKLIST.md` | Pre-launch checklist |
| `docs/MULTI_CLOUD_DEPLOYMENT_STRATEGY.md` | Multi-cloud strategy |
| `docs/local_dev_deployment.md` | Local deployment guide |
| `docs/cloud-run-cloud-sql.md` | Cloud Run + Cloud SQL deployment |
| `docs/RUNSCOPE_AI_ENGINEERING_BLUEPRINT.md` | Architecture blueprint |
| `docs/RUNSCOPE_AI_QUARTERLY_ROADMAP.md` | Quarterly roadmap |
| `docs/RUNSCOPE_AI_ROADMAP_ONE_PAGER.md` | Roadmap one-pager |
| `docs/RUNSCOPE_AI_FEATURE_INVENTORY.md` | Feature inventory |
| `docs/RUNSCOPE_AI_PRODUCT_REQUIREMENTS_DOCUMENT.md` | PRD |
| `docs/RUNSCOPE_AI_PERSONAS_JTBD_AND_USER_JOURNEYS.md` | User personas & journeys |
| `docs/RUNSCOPE_AI_USER_STORIES_AND_ACCEPTANCE_CRITERIA.md` | User stories |
| `docs/RUNSCOPE_AI_NON_FUNCTIONAL_REQUIREMENTS.md` | NFR document |
| `docs/RUNSCOPE_AI_REQUIREMENTS_TRACEABILITY_MATRIX.md` | Requirements traceability |
| `docs/RUNSCOPE_AI_RENAME_AND_TRANSITION_PLAN.md` | Rename/migration plan |
| `docs/RUNSCOPE_AI_DOCS_INDEX.md` | Documentation index |
| `docs/deep-research-report.md` | Research findings |
| `.env.example` | Environment variable reference (~165 vars) |
| `Makefile` | All developer commands (40+ targets) |
