# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

# TestLookup

## Project Overview

**TestLookup** is a 360° AI-powered software testing intelligence platform. It ingests test results from 50+ frameworks, uses a LangChain ReAct agent (via Ollama locally or cloud LLMs) to correlate failures, and pushes structured root-cause analysis to Jira. It includes a deep multi-agent investigation network, RAG-powered test case generation, continuous fine-tuning pipeline, real-time live streaming, full observability stack, PII redaction, user/API-key management, CLI tool, and an MCP server for AI assistant integration.

- Local-LLM capable (air-gapped via Ollama)
- Multi-framework ingestion (Allure, TestNG, JUnit, etc.)
- OpenShift/Kubernetes native (Kustomize)
- CLI tool (Typer + Rich) with multi-profile auth
- RAG knowledge-grounded test case generation

**Subdirectory guides** (auto-loaded by Claude Code when working in those dirs):
- `backend/CLAUDE.md` — Backend code patterns, adding endpoints/agents/tools, test patterns
- `frontend/CLAUDE.md` — Frontend code patterns, adding pages/hooks/services, styling conventions

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
Celery Workers (background AI triage, quality gates, fine-tuning)
      ↓
MCP Server (mcp:8002) — AI assistant integration (stdio + SSE)
```

**Backend:** FastAPI + SQLAlchemy (async) + Motor (MongoDB) + Celery
**Frontend:** React 18 + Vite + TypeScript + Tailwind CSS + Zustand + SWR
**AI Layer:** LangChain ReAct agent + LangGraph multi-agent pipelines (standard + deep) + ML classifier (scikit-learn) + rules engine
**Databases:** PostgreSQL 16 (structured), MongoDB 7 (logs/artifacts), Redis 7 (broker + streams), MinIO (S3 object store), ChromaDB (vectors)
**Observability:** OpenTelemetry → Jaeger, Prometheus, Grafana

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Backend runtime | Python 3.11+ |
| Backend framework | FastAPI 0.115.5 |
| ORM | SQLAlchemy 2.0 (async) + asyncpg |
| NoSQL | Motor 3.7 (MongoDB) |
| Object storage | aioboto3 (MinIO/S3) |
| Background jobs | Celery 5.4 + Flower |
| AI/LLM | LangChain 0.3.9 + LangGraph 0.2 |
| ML classifier | scikit-learn HistGradientBoosting (LLM-free mode) |
| Local LLM | Ollama (qwen2.5, llama3, mistral) |
| Vector store | ChromaDB 0.5 |
| DB migrations | Alembic 1.14 |
| Frontend framework | React 18.3 |
| Build tool | Vite 6 |
| Language | TypeScript 5.6 |
| State management | Zustand 5 |
| Data fetching | SWR 2.2 + Axios |
| UI components | Radix UI + Recharts + D3 |
| Styling | Tailwind CSS 3.4 |
| Linting (BE) | ruff + mypy |
| Linting (FE) | ESLint + Prettier |
| Testing (BE) | pytest + pytest-asyncio |
| Testing (FE) | Vitest + Playwright |
| Tracing | OpenTelemetry → Jaeger |
| Metrics | Prometheus + prometheus-fastapi-instrumentator |
| Dashboards | Grafana |
| CLI | Typer + Rich + httpx |
| Email | aiosmtplib (async SMTP) |

---

## Common Commands

All commands are via `make` (see `Makefile` for full list):

```bash
make dev                  # Start core stack WITHOUT local LLM (Ollama/ChromaDB excluded)
                          # Auto-creates .env from .env.example if missing
                          # Migrations run automatically at container start (with retry)
                          # Seed data runs automatically via seed-init service
                          # AI falls back to rules/ML engine when LLM is absent
make dev-llm              # Start full stack WITH local LLM (Ollama + ChromaDB)
                          # Use this when you need LLM-powered analysis
make dev-setup            # First-time setup with LLM: make dev-llm + pull-llm
make seed-data            # Re-run seed (idempotent — safe at any time)
make seed-data-reset      # Wipe and regenerate seed data
make dev-logs-seed        # Watch seed-init output
make stop                 # Stop all services
make clean                # Stop + remove all volumes (destructive)
make migrate              # Run pending Alembic migrations (manual fallback)
make migrate-create MSG="name"  # Auto-generate new migration
make migrate-down         # Rollback last migration
make pull-llm             # Download Ollama models (qwen2.5:7b + nomic-embed-text)
make simulate-upload      # Send a sample test run to the API
make build-java-sdk       # Build Java SDK fat JAR locally (requires Maven + JDK 11+)
make build-java-sdk-docker # Build Java SDK fat JAR via Docker (no local Maven needed)
make test-backend         # pytest tests/ -v
make test-backend-cov     # pytest with HTML coverage report
make test-frontend        # vitest
make test-e2e             # playwright
make test-agent           # AI agent unit tests (mocked tools)
make lint                 # ruff check + eslint
make format               # ruff format + prettier
make type-check           # mypy + tsc

# Run a single backend test (inside container):
docker compose exec backend pytest tests/test_agent.py::test_name -v

# Run a single backend test (without Docker, from backend/ with venv):
cd backend && pytest tests/test_agent.py::test_name -v
make build                # Build production Docker images
make k8s-deploy-dev       # kubectl apply -k k8s/overlays/dev
make k8s-deploy-staging
make k8s-deploy-prod
make shell-backend        # bash in backend container
make shell-db             # psql in postgres container
make mcp-install          # pip install -r mcp/requirements.txt
make mcp-start            # python mcp/server.py --transport stdio
make mcp-sse              # python mcp/server.py --transport sse --port 8002
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
| Jaeger UI | http://localhost:16686 (monitoring overlay) |
| Prometheus | http://localhost:9090 (monitoring overlay) |
| Grafana | http://localhost:3001 — admin / GF_SECURITY_ADMIN_PASSWORD from .env (monitoring overlay) |

---

## Project Structure (Key Directories)

- `backend/app/` — FastAPI application
  - `main.py` + `bootstrap.py` — app factory and router registration
  - `core/` — config (`Pydantic BaseSettings`), security (JWT), deps (role guards), logging, tracing, metrics
  - `db/` — async clients: `postgres.py` (SQLAlchemy), `mongo.py` (Motor), `minio.py` (aioboto3), `redis_client.py`
  - `models/postgres.py` — all SQLAlchemy ORM models; `models/schemas.py` — Pydantic v2 schemas
  - `routers/` — thin HTTP routers (registered in `bootstrap.py` as `PROTECTED_ROUTERS` or `PUBLIC_ROUTERS`)
  - `services/` — business logic; `services/training/` — fine-tuning pipeline; `services/ml/` — ML classifier + feature extraction + training
  - `services/ingestion_pipeline.py` — shared ingestion pipeline (run creation, test case upsert, post-ingestion orchestration)
  - `services/analysis_router.py` — central dispatcher (LLM/ML/Rules mode selection)
  - `services/rules_engine.py` — enhanced pattern matching + template summaries
  - `services/connectors/` — pluggable knowledge source connectors (Jira, Confluence, URL, document)
  - `services/knowledge_source_service.py` — knowledge source CRUD + governance
  - `services/knowledge_sync_service.py` — sync pipeline + MinIO storage + freshness
  - `services/knowledge_chunking_service.py` — text segmentation + embedding + ChromaDB indexing
  - `services/rag_generation_service.py` — LLM-grounded test case generation with citations
  - `services/rag_retrieval_service.py` — vector search via ChromaDB
  - `services/rag_review_service.py` — batch review + case acceptance/rejection
  - `services/privacy_service.py` — PII/secrets redaction for persistence, logging, LLM, reports
  - `services/redaction_service.py` — pattern-based + key-based sensitive data scrubbing
  - `services/auto_tagging_service.py` — automatic test case/run/suite tagging
  - `services/global_search_service.py` — multi-entity search across 6 entity types
  - `services/suite_sync_service.py` — suite membership traceability + change detection
  - `services/email_service.py` — async SMTP delivery + HTML templates
  - `services/ai_config_resolver.py` — single source of truth for AI config (DB → secrets → env)
  - `agents/` — LangGraph multi-agent pipelines (`workflow.py` builds standard + deep graphs)
  - `tools/` — 11 LangChain agent tools
  - `streams/` — Redis Streams producer/consumer + circuit breaker
  - `worker/` — Celery app, tasks, training tasks
- `backend/models/` — trained ML model artifacts (.joblib)
- `backend/migrations/` — Alembic versions (0001-0055)
- `backend/tests/` — pytest suite
- `frontend/src/` — React 18 + TypeScript SPA
  - `pages/`, `components/`, `services/` (Axios API clients), `hooks/` (SWR wrappers), `store/` (Zustand)
  - `components/analytics/` — customizable analytics widget system (AnalyticsGrid, WidgetPicker, widgetRegistry)
  - `components/rag/` — RAG generation components (KnowledgeSourcePicker, GenerationReviewPanel, CitationDrawer)
  - `pages/settings/ProfilePage.tsx` — user profile + avatar color + password change
  - `pages/settings/SeedDataPage.tsx` — dev-only seed data management UI
  - `pages/test-management/KnowledgeGenerationTab.tsx` — RAG test generation interface
  - `hooks/useAnalyticsView.ts` — analytics widget state + saved views
  - `hooks/useGenerationBatch.ts` — SWR hooks for RAG data
  - `hooks/useTableSort.ts` — sortable table header state
  - `config/refreshIntervals.ts` — standardized SWR polling intervals
- `cli/` — TestLookup CLI tool (Typer + Rich + httpx)
  - `testlookup_cli/app.py` — root app with 11 command groups
  - `testlookup_cli/client.py` — async HTTP client (JWT + API key auth)
  - `testlookup_cli/config.py` — multi-profile config (~/.config/testlookup/profiles.json)
  - `testlookup_cli/commands/upload.py` — file and directory upload commands
  - `testlookup_cli/output.py` — Rich table, JSON, YAML output modes
- `mcp/` — MCP Server (24 tools, 10 resources, 6 prompts)
- `postman/` — Postman API collection + environment for RAG workflow testing
- `client/testlookup_reporter.py` — Python client SDK + pytest plugin + ConfigLoader
- `client/testlookup.yaml.example` — SDK configuration file template
- `client/java/` — Java client SDK (Maven, fat JAR via shade plugin)
  - `src/main/java/io/testlookup/TestLookupReporter.java` — core reporter (builder pattern, batch flush, session management)
  - `src/main/java/io/testlookup/ConfigLoader.java` — unified config (YAML file + env vars + system props)
  - `src/main/java/io/testlookup/junit5/TestLookupExtension.java` — JUnit 5 auto-discovery extension
  - `src/main/java/io/testlookup/testng/TestLookupListener.java` — TestNG auto-discovery listener
  - `src/main/resources/META-INF/services/` — ServiceLoader descriptors for auto-registration
- `UserGuides/TESTLOOKUP_USER_GUIDE.md` — comprehensive end-user guide
- `k8s/` — Kustomize base + overlays (dev/staging/prod/openshift)
- `infra/monitoring/` — Prometheus, Grafana, alerting rules
- `scripts/` — setup and utility scripts

---

## Environment Configuration

Copy `.env.example` to `.env` and configure:

| Variable | Description |
|----------|-------------|
| `APP_ENV` | dev / staging / prod |
| `LLM_PROVIDER` | ollama \| openai \| gemini \| lmstudio \| vllm |
| `LLM_MODEL` | Model name (e.g., qwen2.5:7b, gpt-4o) |
| `AI_OFFLINE_MODE` | true = Ollama only, no internet calls |
| `STORAGE_BACKEND` | minio \| local (required — no default) |
| `POSTGRES_*` | PostgreSQL connection settings |
| `MONGO_*` | MongoDB connection settings |
| `REDIS_*` | Redis broker settings |
| `MINIO_*` | Object storage settings |
| `CHROMA_*` | Vector store settings |
| `JIRA_*` | Jira integration (optional) |
| `SPLUNK_*` | Splunk log query (optional) |
| `JWT_SECRET_KEY` | Must be a strong random value in prod |
| `ANALYSIS_MODE` | auto \| llm \| ml \| rules — which engine classifies test failures |
| `ML_MODEL_DIR` | Path to trained ML model artifacts (default: models/) |
| `ML_MIN_TRAINING_SAMPLES` | Minimum labeled samples before ML mode activates (default: 200) |
| `ML_ACCURACY_THRESHOLD` | Minimum accuracy to deploy a new model (default: 0.80) |
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
| `KNOWLEDGE_RAG_ENABLED` | true \| false — enables RAG knowledge-grounded test generation |
| `KNOWLEDGE_SYNC_TIMEOUT_SECONDS` | Max sync duration per source (default: 60) |
| `KNOWLEDGE_MAX_SOURCES_PER_PROJECT` | Quota per project (default: 100) |
| `KNOWLEDGE_DOCS_BUCKET` | MinIO bucket for knowledge documents (default: knowledge-docs) |
| `KNOWLEDGE_STALE_THRESHOLD_JIRA_HOURS` | Staleness threshold for Jira sources (default: 24) |
| `KNOWLEDGE_STALE_THRESHOLD_URL_HOURS` | Staleness threshold for URL sources (default: 168) |
| `KNOWLEDGE_CHUNK_TARGET_TOKENS` | Ideal chunk size for embeddings (default: 400) |
| `KNOWLEDGE_CHUNK_MAX_TOKENS` | Hard limit on chunk size (default: 800) |
| `KNOWLEDGE_CHUNK_OVERLAP_TOKENS` | Sliding window overlap (default: 50) |
| `CONFLUENCE_ENABLED` | true \| false — enables Confluence connector for RAG |
| `CONFLUENCE_DOMAIN` | Confluence domain (e.g., yourcompany.atlassian.net) |
| `CONFLUENCE_EMAIL` | Confluence auth email |
| `CONFLUENCE_API_TOKEN` | Confluence API token |
| `SMTP_ENABLED` | true \| false — enables email notifications |
| `SMTP_HOST` | SMTP server hostname |
| `SMTP_PORT` | SMTP server port |
| `SMTP_USER` | SMTP username |
| `SMTP_PASSWORD` | SMTP password |
| `SMTP_FROM` | From address for notification emails |
| `SMTP_TLS` | true \| false — enable TLS for SMTP |

---

## Database Migrations

Migrations run **automatically** at container startup (`alembic upgrade head` is prepended to the uvicorn CMD in both `Dockerfile` and `docker-compose.yml`). No manual step is required after `make dev`.

Current migrations:

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
| ... | (0013–0030 various enhancements) |
| 0031 | SSO/SAML/SCIM (sso_configurations, federated_identities, scim_tokens, identity_events) |
| 0032 | Release gate policies (release_gate_policies + release_decisions policy columns) |
| 0033 | Report share links (report_share_links for time-limited shareable reports) |
| 0034 | Service ownership rules (service_ownership_rules for component/team routing) |
| 0035 | Saved views + digest subscriptions (saved_views, digest_subscriptions) |
| 0036 | Integration probe results (integration_probe_results + health check columns) |
| 0037 | Tenant metric snapshots (tenant_metric_snapshots for project-scoped observability) |
| 0038 | AI evaluation datasets and runs (ai_eval_datasets, ai_eval_runs) |
| ... | (0039–0045 various enhancements) |
| 0046 | Performance composite indexes (ix_test_runs_project_status_created) |
| 0047 | Extend digest subscriptions (scope_type, scope_value, trigger_filter; PER_RUN/PER_RELEASE/PER_SUITE schedules) |
| 0048 | Suite membership traceability (suite_memberships, suite_membership_events) |
| 0049 | Saved view page field (adds `page` column for analytics widget configs) |
| 0050 | Tags on plans/runs/suites (JSON `tags` column on test_plans, test_runs, suite_memberships) |
| 0051 | Performance status indexes (ix_test_cases_status_only, ix_test_runs_status_only, ix_sm_project_suite_status) |
| 0052 | User avatar color (avatar_color column on users) |
| 0053 | Knowledge source registry (knowledge_sources table) |
| 0054 | Knowledge chunks and sync events (knowledge_chunks, knowledge_sync_events) |
| 0055 | RAG generation lineage (generation_batches, generation_case_sources, requirement_coverage; RAG columns on managed_test_cases) |
| 0056 | API key project scope (adds nullable `project_id` FK + index to `api_keys`) |

---

## Coding Conventions

### Backend

- **Pydantic v2 only.** Use `@field_validator(..., mode="before")` + `@classmethod` for validators. Use `model_config = SettingsConfigDict(...)` in Settings. Never use deprecated v1 `@validator` or `class Config`.
- **Async everywhere.** All DB calls, HTTP calls, and service methods must be `async def`. SQLAlchemy sessions use `async with AsyncSession` from `backend/app/db/postgres.py`.
- **No SQL INTERVAL string literals with parameters.** PostgreSQL cannot bind params inside string literals like `INTERVAL ':days days'`. Always compute `period_start` in Python (`datetime.now(timezone.utc) - timedelta(days=days)`) and pass as a bound param.
- **Router pattern:** Thin routers — business logic belongs in `services/`, not routers. Routers only handle HTTP concerns (status codes, request parsing, dependency injection).
- **Celery tasks** in `worker/tasks.py` are fire-and-forget — they accept simple serializable args (IDs, dicts), not ORM objects.
- **AgentExecutor** must include `max_execution_time=settings.AI_TIMEOUT_SECONDS` to prevent runaway LLM calls.
- **Role-based access:** Use `require_role(UserRole.X)` as a dependency. Role hierarchy: VIEWER < TESTER < QA_ENGINEER < QA_LEAD < ADMIN.
- **API key storage:** Keys are stored as SHA-256 hashes. The raw key is only returned once at creation. `key_hint` = `raw_key[:8] + "..."` (max 11 chars; column is `String(12)`). Keys can be project-scoped (nullable `project_id` FK) — ADMIN-only creation. Use `get_api_key_context` dependency when project scope enforcement is needed.
- **Structured logging:** Use `structlog.get_logger(__name__)` — never `print()` or raw `logging.getLogger()`.
- **Analysis mode dispatch:** All test classification must go through `services/analysis_router.py`, never call `run_triage_agent()` or `RulesEngine` directly from routers. The router reads `ANALYSIS_MODE` and dispatches to LLM/ML/Rules.
- **ML feature extraction must be deterministic.** Same inputs → same feature vector. No randomness in preprocessing. Feature names in `ml/feature_extractor.py:FEATURE_NAMES` must match training order.
- **ML inference budget:** <5ms per test. If the ML model takes longer, the analysis router falls back to rules.
- **PII redaction:** All data must pass through `services/privacy_service.py` before persistence (`sanitize_for_persistence()`), logging (`sanitize_for_logging()`), LLM calls (`sanitize_for_llm()`), or report rendering (`sanitize_for_report()`). Redaction placeholder is always `[REDACTED]`.
- **Tag normalization:** Use `services/tag_utils.py:normalize_tag()` for all tag operations. System tags (13 reserved names) cannot be used as custom tags — validate with `validate_custom_tags()`.
- **Knowledge source connectors** are pluggable via `services/connectors/registry.py`. New connectors must implement the `BaseConnector` interface in `services/connectors/base.py`.
- **RAG feature gating:** Check `KNOWLEDGE_RAG_ENABLED` via the feature gate (Redis → DB → env var fallback chain). Never bypass the gate.
- **Dual authentication:** `get_current_user_or_api_key()` in `core/deps.py` tries JWT first, then API key (SHA-256 hash lookup). Use this dependency for endpoints that accept both auth methods.
- **Email notifications:** Use `services/email_service.py` for async SMTP delivery. Templates in `services/email_templates.py`. Never send emails synchronously in request handlers — dispatch via Celery tasks.
- **AI config resolution:** Use `services/ai_config_resolver.py` for LLM configuration. Resolution precedence: DB overrides → secret-backed API keys → environment defaults. Results are cached in Redis (60s TTL).
- **Ingestion goes through `ingestion_pipeline.py`.** New ingestion paths (routers, Celery tasks, SDK handlers) must use `create_run_from_payload()` → `ingest_test_results()` → `finalize_run()` from `services/ingestion_pipeline.py`. Never duplicate the post-ingestion orchestration (tagging, suite sync, notifications, AI analysis).
- **Client SDK config resolution:** Both Python and Java SDKs use a `ConfigLoader` with the same precedence: constructor args > env vars > `testlookup.yaml` > defaults. New SDKs must follow this pattern and support the same env var names (`TESTLOOKUP_URL`, `TESTLOOKUP_API_KEY`, etc.).

### Frontend

- **SWR for all data fetching.** Add hooks in `hooks/` that wrap `useSWR`; pages import hooks, not raw service calls directly.
- **Zustand for global state.** Only project selection and project list live in `projectStore.ts`. Auth state lives in `authStore.ts`. Per-page state stays local.
- **`api.ts` is the Axios base.** All service files import from `services/api.ts`. Never create a second Axios instance.
- **TestCase breadcrumbs** use `runId?.slice(0,8)` — the `build_number` field lives on `TestRun`, not `TestCase`.
- **TypeScript strict mode is on** — avoid `any`; use `unknown` + type guards when necessary.
- **Sidebar navigation** has three sections in order: main nav (top), AI Agents (middle), Management (bottom — Projects, Releases, Users). Settings is in the footer.
- **Analytics widget system:** Use `useAnalyticsView` hook + `AnalyticsGrid` + `AnalyticsWidget` components for customizable dashboard pages. Widget templates defined in `components/analytics/widgetRegistry.ts` (30+ templates). Max 12 widgets per page.
- **CSS theming:** Two dark themes ("midnight" GitHub-inspired, "classic" deep navy). Use CSS custom properties: `var(--color-bg)`, `var(--color-accent)`, `var(--color-border)`, etc. Never hardcode colors.
- **Sortable tables:** Use `useTableSort` hook + `SortableHeader` component for sortable table columns.
- **Client-side PII sanitization:** `utils/errorReporting.ts` redacts emails, phone numbers, Bearer tokens, API keys, and file paths before sending error reports to the backend.
- **Dev-only pages:** Pages like `SeedDataPage.tsx` check `APP_ENV=development` and render nothing in staging/production.

---

## Known Pitfalls

These bugs have been encountered and fixed — avoid reintroducing them:

1. **SQL INTERVAL parameterization** — `INTERVAL ':days days'` does NOT work in PostgreSQL. Use Python `timedelta` instead. See `services/metrics_service.py` and `routers/search.py`.

2. **Pydantic v1 syntax** — `@validator` and `class Config` are removed in Pydantic v2. All validators in `core/config.py` use `@field_validator`.

3. **Missing `.env`** — `make dev` uses a Make file-target (`.env:`) that copies `.env.example` → `.env` only when `.env` is absent. This works on all platforms without shell tests. If you bypass Make, run `cp .env.example .env` manually. `STORAGE_BACKEND` defaults to `minio` in `config.py` but must be in `.env` so Docker Compose substitution works.

4. **Seed script location** — `make seed-data` runs `python /app/scripts/seed_dev_data.py` inside the backend container. The volume mount `./backend:/app` means the script must live at `backend/scripts/seed_dev_data.py` — NOT `scripts/seed_dev_data.py` (repo root). The seed also runs automatically via the `seed-init` Docker Compose service on every `make dev`.

6. **AgentExecutor timeout** — Without `max_execution_time`, a slow Ollama model will hang the request indefinitely.

7. **TestCase `build_number`** — This field is on `TestRun`, not `TestCase`. Don't reference `tc.build_number`.

8. **API key `key_hint` column width** — The `api_keys.key_hint` column is `String(12)`. The key hint must be built as `raw_key[:8] + "..."` (11 chars). Using `[:10]` produces 13 chars and causes a PostgreSQL string overflow error.

9. **Migration retry loop** — The backend startup command now retries `alembic upgrade head` every 5 seconds until it succeeds. If you see "relation does not exist" errors, watch `make logs` — the retry will resolve it once PostgreSQL finishes init. Run `make migrate` if you need to force it immediately.

10. **UserRole stored as String(20)** — The `role` columns in `project_members`, `api_keys`, and `user_invitations` are `String(20)` (not a native PostgreSQL enum), so `UserRole(str, Enum)` values serialize/deserialize transparently.

11. **All-Projects mode** — Several pages support an `ALL_PROJECTS_ID = "all"` sentinel value from `projectStore`. Backend optional `project_id` query params must be `Optional[uuid.UUID] = None` and the `None` path must omit the WHERE filter entirely. Never pass the literal string `"all"` to the backend as a UUID.

12. **Live session token auth** — The `/stream/events/batch` hot path uses `X-Session-Token` header (Redis O(1) lookup), not JWT. Do not accidentally require JWT middleware on that endpoint.

13. **Dev auto-login endpoint** — `POST /api/v1/auth/dev-login` returns 404 unless `APP_ENV=development` AND `DEV_AUTO_LOGIN_ENABLED=true`. It lives in the public `auth` router so it is reachable before authentication. Never gate it behind JWT middleware.

14. **Self-registration role + must_change_password** — `POST /api/v1/auth/register` always creates users with `role=QA_ENGINEER` and `must_change_password=True`. The `POST /api/v1/auth/first-time-reset` endpoint (requires JWT, no current password) clears this flag. `ProtectedRoute` redirects any authenticated user with `must_change_password=True` to `/reset-password`. Migration `0014` adds the `must_change_password` column to the `users` table.

15. **SSO enforcement + admin fallback** — When `SSOEnforcementMode.SSO_REQUIRED` is active, only ADMIN users can use password login (if `SSO_ADMIN_FALLBACK_ENABLED=true`). Non-admin password login is blocked with 403. The SSO router (`/api/v1/sso/*`) and SCIM router (`/api/v1/scim/v2/*`) are registered as PUBLIC routers (no JWT required); SCIM uses its own bearer token auth, and SSO admin endpoints require `require_role(UserRole.ADMIN)` internally.

16. **SCIM token `token_hint` column width** — Same pattern as API key: `raw_token[:8] + "..."` = 11 chars, column is `String(12)`. The SCIM token prefix is `scim_` so hints look like `scim_abc...`.

17. **SSO certificate in responses** — Never expose the raw IdP X.509 certificate in API responses. `SSOConfigResponse` uses `idp_certificate_fingerprint` (SHA-256 hex digest) instead. The raw certificate is only accepted on create/update.

18. **Release gate policy backward compatibility** — When no `ReleaseGatePolicy` is configured, the system falls back to hardcoded thresholds from `config.py` (GO < 20, NO_GO >= 55, pass_rate_minimum = 90%). Policy precedence is: project-specific → system default (project_id IS NULL) → hardcoded. The `criticality_service.py` functions `compute_composite()` and `score_to_recommendation()` accept optional keyword-only policy parameters; existing callers using positional args are unaffected.

19. **Policy dimension weights sum** — The `dimension_weights` in a `PolicyDocument` must sum to 1.0 (within ±0.01 tolerance). The router validates this on create/update. The frontend also validates client-side with a live sum indicator.

20. **Report share link tokens** — Share tokens are generated with `secrets.token_urlsafe(48)` (64-char base64). The `shared_reports.router` is registered as a PUBLIC router (no JWT) since share links are token-authenticated. Share links have expiry (1-30 days, default 7) and can be revoked. All export/share actions are audited via `AccessAuditLog`.

21. **Report composition uses cached snapshot** — `report_composition_service.py` reads from `RunIntelligenceSnapshot.payload` (cached JSON), not from individual tables. This avoids expensive re-queries and ensures the report reflects the same data the user saw on the intelligence page. If no snapshot exists, a `ValueError` is raised.

22. **Ownership resolution hierarchy** — `ServiceOwnershipRule` rules are evaluated highest-priority-first using glob matching (`fnmatch`). Fallback chain: rules → `Project.component_owner_map` (legacy) → `TestCase.owner` (Allure label) → "Unassigned". For clusters, majority voting across member tests determines the owning team. The `ownership` router is project-scoped (`/api/v1/projects/{project_id}/ownership/...`).

23. **Integration health probes** — The Celery beat task `run_integration_health_probes` runs every 15 minutes, probing 9 providers (Jira, Splunk, GitHub, OCP, Slack, Teams, SMTP, Ollama, ChromaDB) concurrently. Probes check auth validity, response latency, and payload correctness. Results persist to `integration_probe_results` (history) and upsert `integration_health_checks` (latest). The `integration_health_gauge` Prometheus metric is populated on each run. Alerts log at WARNING when `consecutive_failures >= 3`. Disabled integrations return `skipped` status.

24. **Unified audit dashboard and redaction** — `audit_dashboard_service.py` queries across all 5 audit tables (AccessAuditLog, SettingsAuditLog, TestCaseAuditLog, IdentityEvent, plus report events). Sensitive values are redacted by key name pattern (password, token, secret, etc.) and value content pattern (bearer, authorization). Tenant isolation enforced via `get_accessible_project_ids()` — non-admin users only see their projects' events. CSV export applies redaction before download. The `TenantMetricSnapshot` table stores per-project observability metrics.

25. **Performance budgets and load testing** — `performance_budgets.py` defines codified latency (p50/p95/p99) and throughput budgets for 11 operations and 3 scale scenarios (small_team/mid_enterprise/large_enterprise). `scripts/load_test_harness.py` generates synthetic test data and benchmarks API endpoints against budgets. Config settings `SEARCH_INDEX_BATCH_SIZE`, `SEARCH_INDEX_INCREMENTAL_LIMIT`, `SEARCH_QUERY_TIMEOUT_MS`, `SEARCH_MAX_RESULTS` are tunable via environment variables.

26. **AI evaluation dashboards** — `ai_eval_service.py` computes macro-average precision/recall/F1/accuracy from labeled datasets. Datasets can be auto-generated from human feedback (AIFeedback records with `correct`/`incorrect` ratings). Drift detection compares current vs previous evaluation window accuracy. The `AIEvalRun` table persists every evaluation with metrics. The dashboard at `/settings/ai-eval` shows agreement rate, drift direction, recent eval runs, and model version history.

27. **Digest subscriptions and saved views** — `DigestSubscription` stores user-level scheduled delivery config (DAILY or WEEKLY via email/slack/teams). The Celery beat task `dispatch_scheduled_digests` runs daily at 07:00 UTC, queries subscriptions where `next_delivery_at <= now`, generates content via `digest_content_service.generate_digest()`, and delivers via the notification email service. `SavedView` stores per-user filter configs with personal/shared visibility. Both tables are user-owned with project-scoped filtering.

28. **ANALYSIS_MODE must go through analysis_router** — Never call `run_triage_agent()` directly from new code. Use `services/analysis_router.classify_test()` which reads the configured mode and dispatches to LLM, ML, or Rules engine. The mode is persisted in the `app_settings` table (key `ai_config.analysis_mode`) and cached in Redis (`config:analysis_mode`).

29. **ML model cold start** — When `ANALYSIS_MODE=ml` but no trained model exists, `MLClassifier.is_available()` returns False and the analysis_router falls back to the rules engine. The UI shows a "Not Trained" badge. Minimum 200 labeled samples needed before training activates.

30. **Zustand object selectors cause infinite loops** — Never use `useAuthStore(s => ({ key1: s.x, key2: s.y }))` — the inline object creates a new reference every render, causing Zustand (v5, `Object.is` equality) to re-render infinitely. Use individual primitive selectors: `useAuthStore(s => s.x)`.

31. **PII redaction depth limit** — `redact_dict()` has a max recursion depth of 10 levels. Deeply nested structures beyond 10 levels will not be redacted. The redaction placeholder is always `[REDACTED]` — never customize it.

32. **System tags are reserved** — The 13 system tags (passed, failed, skipped, broken, flaky, regression, duplicate, all_passed, has_failures, has_skips, flaky_content, regression_detected, needs_review) cannot be used as custom tags. `validate_custom_tags()` in `tag_utils.py` enforces this.

33. **Suite membership sync ordering** — `suite_sync_service.py` must run after ingestion aggregates are computed. The sync detects additions, deletions, modifications, and restorations; deleted tests go to `<suite>-deleted` bucket with `needs_review` tag.

34. **Knowledge source deduplication** — `knowledge_sources` has a UNIQUE constraint on `(project_id, canonical_url)`. Attempting to register the same URL twice for a project will fail. Content change detection uses SHA-256 hashing (`content_hash` column).

35. **RAG chunk vector_id uniqueness** — `knowledge_chunks.vector_id` is UNIQUE. It references the corresponding ChromaDB document. When re-syncing, old chunks must be deactivated (`is_active=false`) before creating new ones.

36. **Generation case source staleness** — `generation_case_sources.source_content_hash_at_generation` captures the source hash at generation time. If the source is re-synced and the hash changes, `is_stale` is set to true and the linked test case's `is_stale` flag is also set.

37. **Dev-only seed endpoints** — The seed router (`/api/v1/dev/seed`) uses `_require_dev()` to return 404 in non-development environments. Never remove this guard.

38. **CLI profile storage** — CLI profiles are stored at `~/.config/testlookup/profiles.json`. Environment variables `TESTLOOKUP_URL` and `TESTLOOKUP_API_KEY` serve as fallbacks when no profile is configured.

39. **Email template event types** — `email_templates.py` supports 6 event types: `run_failed`, `run_passed`, `high_failure_rate`, `ai_analysis_complete`, `quality_gate_failed`, `flaky_test_detected`. Adding new event types requires both a template and a Celery task dispatcher.

40. **Analytics widget migration** — `useAnalyticsView` hook supports both legacy widget-ID arrays (v1) and new `VisualizationInstance` arrays (v2). Legacy format is auto-migrated on load via `migrateWidgetIds()`. Max 12 widgets per page.

41. **Ingestion file size limit** — `POST /api/v1/ingest/file` rejects uploads > 50 MB with 413. The limit is hardcoded in `routers/ingest.py:MAX_FILE_SIZE`. Large test suites should use JSON batch ingestion or split files.

42. **Ingestion format auto-detection** — The `_detect_format()` function in `routers/ingest.py` inspects the first 2 KB of content. If a file has ambiguous markers (e.g., both `<testsuite>` and TestNG attributes), it may misdetect. Use `format=testng` explicitly for TestNG XML.

43. **Java SDK fat JAR classifier** — The Maven shade plugin produces `testlookup-reporter-1.0.0-all.jar` (classifier "all"). The SDK download endpoint looks for `*-all.jar` in `client/java/target/`. If missing, it falls back to a ZIP of source. Run `make build-java-sdk` to produce the JAR.

44. **Client config file precedence** — `testlookup.yaml` discovery stops at first file found. If a project-root config exists but is incomplete, it will NOT merge with `~/.testlookup/config.yaml`. Each level within a single file merges (constructor > env > file > defaults), but only one file is loaded.

45. **Java ConfigLoader YAML optional** — If `jackson-dataformat-yaml` is not on the classpath, `ConfigLoader` silently skips YAML parsing and relies solely on env vars and system properties. The fat JAR includes it, but a slim dependency may not.

46. **Project-scoped API keys** — `ApiKey.project_id` is nullable. NULL = user-scoped (inherits user's project permissions). Non-null = project-scoped (restricted to that project). Only ADMIN can create project-scoped keys or keys for other users (`target_user_id`). The `get_api_key_context` dependency in `core/deps.py` returns `(User, project_id | None)` — callers must enforce project scope when `project_id` is non-None. Ingestion and stream session creation endpoints enforce this. The existing `get_current_user_or_api_key` still returns only `User` for backward compatibility.

---

## Analysis Engine Modes

The system supports three test analysis engines, configurable via `ANALYSIS_MODE` env var or **Settings > AI Configuration** (ADMIN only):

| Mode | Engine | Latency/test | Dependencies | Accuracy Target |
|------|--------|-------------|--------------|-----------------|
| `llm` | LangChain ReAct agent + 5 tools | ~300ms | Running LLM (Ollama/OpenAI/Gemini) | 85-95% |
| `ml` | scikit-learn HistGradientBoosting | ~2ms | Trained model (.joblib) | >85% |
| `rules` | Pattern matching + statistics | ~0.2ms | None | 60-75% |
| `auto` | Smart fallback: ML → LLM → Rules | varies | Best available | Highest available |

**Dispatch flow:** `analysis_agent._analyse_one()` → `analysis_router.get_analysis_mode()` → LLM/ML/Rules engine → same `AIAnalysis` output shape.

**Key files:**
- `services/analysis_router.py` — central dispatcher
- `services/rules_engine.py` — enhanced pattern matching + template summaries
- `services/ml/feature_extractor.py` — 31-feature numeric vector
- `services/ml/classifier.py` — HistGradientBoosting wrapper
- `services/ml/summary_generator.py` — template summaries enriched with ML metadata
- `services/ml/trainer.py` — training pipeline (Celery beat, nightly)

---

## Sidebar Navigation Structure

```
[Logo / TestLookup v0.0.1]
─────────────────────────────
Dashboard
Test Runs
Coverage
Failures
Trends
Defects
Search
Test Cases
Live
─────────────────────────────
  AI AGENTS
AI Pipeline
Deep Analysis
Release Gate
Chat
─────────────────────────────
  MANAGEMENT
Projects
Releases
Users
─────────────────────────────
Settings                (footer)
```

---

## User Management

### Auth endpoints (public — no JWT required)

| Method | Path | Description |
|--------|------|-------------|
| POST | /api/v1/auth/register | Self-service registration → QA_ENGINEER role, must_change_password=True |
| POST | /api/v1/auth/login | Standard login |
| POST | /api/v1/auth/first-time-reset | Forced reset on first login (JWT required, no old password needed) |
| POST | /api/v1/auth/dev-login | Dev-only bypass login (APP_ENV=development only) |
| GET  | /api/v1/auth/me | Return own profile (JWT required) |
| PATCH | /api/v1/auth/me | Self-service profile update: full_name, avatar_color (JWT required, all roles) |
| POST | /api/v1/auth/change-password | Change own password (JWT required, requires current password) |

### Self-registration flow
1. User fills in the Register form on the login page (email, username, full name, password)
2. Account is created with `role=QA_ENGINEER` and `must_change_password=True`
3. User logs in; `ProtectedRoute` detects `must_change_password=True` and redirects to `/reset-password`
4. User sets a permanent password via `POST /auth/first-time-reset` (no old password required)
5. `must_change_password` is cleared; user proceeds to the dashboard
6. An admin can adjust the user's role via the User Management page

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

### Full-stack feature checklist
1. Pydantic schemas → `backend/app/models/schemas.py`
2. ORM model → `backend/app/models/postgres.py`
3. Migration → `make migrate-create MSG="add_feature_table"`
4. Service → `backend/app/services/<feature>.py`
5. Router → `backend/app/routers/<feature>.py`
6. Register router → `backend/app/bootstrap.py` (`PROTECTED_ROUTERS` or `PUBLIC_ROUTERS`)
7. Tests → `backend/tests/test_<feature>.py`
8. Frontend types → `frontend/src/types/<feature>.ts`
9. API service → `frontend/src/services/<feature>Service.ts`
10. SWR hook → `frontend/src/hooks/use<Feature>.ts`
11. Page → `frontend/src/pages/<Feature>Page.tsx`
12. Route → `frontend/src/App.tsx` (lazy import)
13. Sidebar entry → `frontend/src/components/layout/Sidebar.tsx`

See `backend/CLAUDE.md` and `frontend/CLAUDE.md` for exact code templates and patterns.

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

**GitHub Actions** (`.github/workflows/ci.yml`):
- **Triggers:** Push to `main`/`develop`, PRs to `main`
- **backend-test:** ruff → mypy → pytest (with postgres + redis services)
- **frontend-test:** eslint → tsc → vitest
- **build:** Multi-stage Docker build → push to GHCR (`ghcr.io/<org>/<repo>`)
- **deploy:** `kubectl apply -k k8s/overlays/prod` (optional, on main)

---

## Kubernetes Deployment

Uses **Kustomize** with base + overlays pattern:

```bash
make k8s-deploy-dev       # 1 replica, debug logging
make k8s-deploy-staging   # 2 replicas, info logging
make k8s-deploy-prod      # 3 replicas backend, 2 frontend, error logging
make k8s-status           # Show pods, services, ingress
```

Namespace: `testlookup`

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

The `mcp/` directory contains a Model Context Protocol server that exposes TestLookup to Claude Desktop, IDEs, and CI pipelines.

### Running Locally (Claude Desktop — stdio)

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

### Available Tools (24)

| Group | Tools |
|-------|-------|
| Auth | `login`, `health_check` |
| Projects | `list_projects`, `get_project`, `create_project` |
| Runs | `list_test_runs`, `get_run_details`, `list_test_cases`, `get_test_case` |
| Metrics | `get_dashboard_metrics`, `get_test_trends` |
| Analytics | `get_flaky_tests`, `get_failure_categories`, `get_top_failing_tests`, `get_coverage_report`, `get_defects`, `get_ai_analysis_summary` |
| Analysis | `trigger_ai_analysis`, `search_tests` |
| Intelligence | `get_run_intelligence`, `refresh_intelligence`, `get_run_summary` |
| Deep Investigation | `trigger_deep_analysis`, `get_pipeline_status`, `get_failure_clusters`, `get_deep_findings` |
| Search | `global_search` |
| Reports | `create_share_link` |
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

## CLI Tool

The `cli/` directory contains a full command-line interface built with Typer + Rich, enabling terminal-based interaction with TestLookup.

### Installation

```bash
cd cli && pip install -e .
```

### Command Groups (11)

| Command | Purpose |
|---------|---------|
| `auth` | Login/logout/whoami |
| `keys` | API key management (create/list/revoke) |
| `health` | System connectivity check |
| `projects` | Project listing and metadata |
| `runs` | Test run inspection |
| `tests` | Individual test case queries |
| `search` | Global entity search |
| `intelligence` | Run intelligence and AI analysis |
| `deep` | Deep investigation pipeline (start/status/clusters/findings) |
| `reports` | PDF export and share link generation |
| `upload` | Upload test result files/directories (JUnit/TestNG XML, Allure JSON) |

### Authentication

- **JWT**: `testlookup auth login` saves tokens to profile
- **API Key**: `testlookup --api-key <key>` or `TESTLOOKUP_API_KEY` env var
- **Multi-profile**: Profiles stored at `~/.config/testlookup/profiles.json`

### Output Formats

All commands support `--output table|json|yaml` (default: table with Rich formatting).

---

## RAG Knowledge Generation

The RAG (Retrieval-Augmented Generation) system enables knowledge-grounded test case generation from external sources.

### Architecture

```
Knowledge Sources (Jira, Confluence, URLs, Documents)
  → Connectors (fetch content)
  → MinIO (store raw content)
  → Chunking Service (segment + embed)
  → ChromaDB (vector store)
  → Retrieval (semantic search)
  → Generation (LLM + citations)
  → Review (accept/reject)
```

### API Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/v1/knowledge-sources` | List sources |
| POST | `/api/v1/knowledge-sources` | Create source (Jira, Confluence, URL, document) |
| GET | `/api/v1/knowledge-sources/{id}` | Get source details |
| PATCH | `/api/v1/knowledge-sources/{id}` | Update source |
| DELETE | `/api/v1/knowledge-sources/{id}` | Delete/archive source |
| POST | `/api/v1/knowledge-sources/{id}/sync` | Trigger manual sync |
| GET | `/api/v1/knowledge-sources/{id}/sync-history` | Sync audit trail |
| GET | `/api/v1/knowledge-sources/{id}/freshness` | Staleness status |
| POST | `/api/v1/knowledge-sources/test-connector` | Test connector connectivity |
| POST | `/api/v1/test-management/cases/rag-retrieve` | Vector search chunks |
| POST | `/api/v1/test-management/cases/rag-generate` | Generate test cases with citations |
| GET | `/api/v1/test-management/batches/{id}/coverage` | Requirement coverage |
| GET | `/api/v1/test-management/batches/{id}` | Batch summary |
| POST | `/api/v1/test-management/batches/{id}/accept` | Accept multiple cases |
| POST | `/api/v1/test-management/batches/{id}/cases/{cid}/accept` | Accept single case |
| POST | `/api/v1/test-management/batches/{id}/cases/{cid}/reject` | Reject case |
| GET | `/api/v1/test-management/rag-status` | RAG feature status + counts |

### Feature Gating

RAG is controlled by `KNOWLEDGE_RAG_ENABLED` with fallback chain: Redis cache → DB (`app_settings`) → env var.

---

## Global Search

Multi-entity search across 6 entity types with keyword, semantic, and hybrid modes.

### Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/v1/search` | Global search (q, project_id, status, days, search_type, page, size) |
| GET | `/api/v1/search/index-status` | ChromaDB collection health |
| POST | `/api/v1/search/reindex` | Manual reindex trigger via Celery |
| GET | `/api/v1/search/similar/{test_case_id}` | Find historically similar failures |

### Entity Types

test_case, test_run, suite, defect, flaky_test, release

---

## Email Notifications

Async email delivery via aiosmtplib with HTML templates for 6 event types.

### Event Types

`run_failed`, `run_passed`, `high_failure_rate`, `ai_analysis_complete`, `quality_gate_failed`, `flaky_test_detected`

### Digest Subscriptions

`DigestSubscription` supports DAILY, WEEKLY, PER_RUN, PER_RELEASE, PER_SUITE schedules. Celery beat dispatches at 07:00 UTC daily. Subscriptions have scope_type/scope_value for filtering.

---

## Suite Membership Traceability

`suite_sync_service.py` tracks test membership in suites across runs:

- Detects additions, deletions, modifications, restorations
- Deleted tests moved to `<suite>-deleted` bucket with `needs_review` tag
- Full change history in `suite_membership_events` table
- Runs after ingestion aggregates are computed

---

## Analytics Widget System

Customizable dashboard visualizations via `components/analytics/`:

- **widgetRegistry.ts** — 30+ widget templates across 5 page categories (Dashboard, Trends, Coverage, Defects, Failures)
- **useAnalyticsView** hook — manages widget instances, saved views, dirty state
- **WidgetPicker** — catalog browser with enable/disable toggles (max 12 per page)
- **VisualizationConfigModal** — per-instance config (title, chart type, metric variant)
- Saved views persist to server per-page, per-project with localStorage fallback

---

## Seed Data Management (Dev Only)

### API Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/v1/dev/seed/status` | Check if seed data is loaded |
| POST | `/api/v1/dev/seed` | Load seed data (idempotent) |
| POST | `/api/v1/dev/seed/reset` | Wipe and regenerate seed data |
| DELETE | `/api/v1/dev/seed` | Delete seed data without re-seeding |

All endpoints return 404 in non-development environments. Requires ADMIN role.

### Frontend

Settings > Seed Data page (`SeedDataPage.tsx`) — interactive UI with Load/Reset/Delete buttons and collapsible log output.

---

## Ingestion API

Unified test data ingestion endpoints consolidating JSON batch and file upload paths. Both return 202 Accepted with async processing via Celery.

### Endpoints

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| POST | `/api/v1/ingest` | JWT or API key | JSON batch of test results from SDKs or scripts |
| POST | `/api/v1/ingest/file` | JWT or API key | File upload (JUnit XML, TestNG XML, Allure JSON) |

### JSON batch (`POST /api/v1/ingest`)

Accepts `IngestPayload` with `project_id`, `build_number`, and `results[]`. Returns `run_id` and `task_id`.

### File upload (`POST /api/v1/ingest/file`)

Multipart form with: `file`, `project_id`, `build_number`, `branch` (opt), `commit_hash` (opt), `release_name` (opt), `format` (auto|junit|testng|allure).

- Max file size: 50 MB
- Format auto-detection: `.json` → allure, `<testng-results` → testng, `<testsuite` → junit, fallback → junit

### Post-ingestion pipeline (`services/ingestion_pipeline.py`)

After parsing, the pipeline: creates/upserts TestRun → upserts TestCase rows → updates run aggregates → syncs suite membership → auto-tags → links release → enqueues notifications → triggers AI analysis.

### CLI upload

```bash
testlookup upload file results.xml -p <project-id> -b build-42
testlookup upload dir ./target/surefire-reports -p <project-id> -b build-42
```

---

## Client SDK Configuration

All client SDKs (Python, Java) share a unified configuration model via `testlookup.yaml`.

### Config file discovery (first found wins)

1. `./testlookup.yaml` — project root (commit to repo, no secrets)
2. `./.testlookup/config.yaml` — hidden dir (add to `.gitignore`)
3. `~/.testlookup/config.yaml` — user home (personal defaults)

### Precedence (highest wins)

Constructor args > Environment variables > Config file > Built-in defaults

### Environment variables

| Variable | Maps to |
|----------|---------|
| `TESTLOOKUP_URL` | `server.url` |
| `TESTLOOKUP_TOKEN` | `auth.token` (JWT) |
| `TESTLOOKUP_API_KEY` | `auth.api_key` |
| `TESTLOOKUP_PROJECT_ID` | `project.id` |
| `TESTLOOKUP_BUILD` | `ci.build_number` |
| `TESTLOOKUP_BRANCH` | `ci.branch` |
| `TESTLOOKUP_COMMIT` | `ci.commit_hash` |
| `TESTLOOKUP_UPLOAD_MODE` | `upload.mode` (live\|offline) |

### Java system properties

`-Dtestlookup.url`, `-Dtestlookup.token`, `-Dtestlookup.apiKey`, `-Dtestlookup.projectId`, `-Dtestlookup.build`, `-Dtestlookup.branch`, `-Dtestlookup.commit`

### Python SDK usage

```python
from testlookup_reporter import TestLookupReporter

# Config auto-resolved from testlookup.yaml / env vars
reporter = TestLookupReporter()

# Or explicit overrides
reporter = TestLookupReporter(url="http://localhost:8000", api_key="tl_...")
```

### Java SDK usage

```java
// Config auto-resolved from testlookup.yaml / env vars / system props
TestLookupReporter reporter = new TestLookupReporter.Builder().build();

// Or explicit overrides
TestLookupReporter reporter = new TestLookupReporter.Builder()
    .url("http://localhost:8000")
    .apiKey("tl_...")
    .projectId("uuid")
    .build();
```

### Java auto-discovery (zero-config)

JUnit 5 and TestNG listeners are auto-registered via `META-INF/services/` ServiceLoader descriptors. Add the fat JAR to the classpath and configure via env vars — no code changes needed.

- **JUnit 5:** `org.junit.jupiter.api.extension.Extension` → `TestLookupExtension`
- **TestNG:** `org.testng.ITestNGListener` → `TestLookupListener`

**TestNG suite parameter configuration (simplest approach):**
```xml
<suite name="My Suite">
  <parameter name="testlookup.url" value="http://localhost:8000"/>
  <parameter name="testlookup.apiKey" value="qai_..."/>
  <parameter name="testlookup.projectId" value="your-project-uuid"/>
  <listeners>
    <listener class-name="io.testlookup.testng.TestLookupListener"/>
  </listeners>
</suite>
```

Suite parameters take highest precedence over system properties, env vars, and `testlookup.yaml`.

### Building the Java fat JAR

```bash
make build-java-sdk             # Local Maven + JDK 11+
make build-java-sdk-docker      # Docker-based (no local Maven needed)
# Output: client/java/target/testlookup-reporter-1.0.0-all.jar
```

Jackson is relocated to `ai.testlookup.shaded.jackson` to avoid classpath conflicts.

### Config file template

See `client/testlookup.yaml.example` for the full annotated configuration template.

---


## Documentation

| File | Purpose |
|------|---------|
| `README.md` | Overview, features, architecture, quick start |
| `UserGuides/TESTLOOKUP_USER_GUIDE.md` | Comprehensive end-user guide |
| `installation.md` | GCP VM deployment guide |
| `docs/DEVELOPMENT.md` | Developer workflow, iterative phases |
| `docs/cloud-run-cloud-sql.md` | Cloud Run + Cloud SQL deployment |
| `postman/README.md` | API collection guide with RAG workflow |
| `.env.example` | Environment variable reference |
| `Makefile` | All developer commands |