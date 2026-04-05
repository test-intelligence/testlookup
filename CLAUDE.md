# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

# TestLookup

## Project Overview

**TestLookup** is a 360° AI-powered software testing intelligence platform. It ingests test results from 50+ frameworks, uses a LangChain ReAct agent (via Ollama locally or cloud LLMs) to correlate failures, and pushes structured root-cause analysis to Jira. It includes a deep multi-agent investigation network, continuous fine-tuning pipeline, real-time live streaming, full observability stack, user/API-key management, and an MCP server for AI assistant integration.

- Local-LLM capable (air-gapped via Ollama)
- Multi-framework ingestion (Allure, TestNG, JUnit, etc.)
- OpenShift/Kubernetes native (Kustomize)

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
**AI Layer:** LangChain ReAct agent + LangGraph multi-agent pipelines (standard + deep)
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

---

## Common Commands

All commands are via `make` (see `Makefile` for full list):

```bash
make dev                  # Start full stack (docker compose up -d --build)
                          # Auto-creates .env from .env.example if missing
                          # Migrations run automatically at container start (with retry)
                          # Seed data runs automatically via seed-init service
make dev-setup            # First-time setup: make dev + pull-llm
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
  - `services/` — business logic; `services/training/` — fine-tuning pipeline
  - `agents/` — LangGraph multi-agent pipelines (`workflow.py` builds standard + deep graphs)
  - `tools/` — 11 LangChain agent tools
  - `streams/` — Redis Streams producer/consumer + circuit breaker
  - `worker/` — Celery app, tasks, training tasks
- `backend/migrations/` — Alembic versions (0001-0038)
- `backend/tests/` — pytest suite
- `frontend/src/` — React 18 + TypeScript SPA
  - `pages/`, `components/`, `services/` (Axios API clients), `hooks/` (SWR wrappers), `store/` (Zustand)
- `mcp/` — MCP Server (20 tools, 10 resources, 6 prompts)
- `client/testlookup_reporter.py` — Python client SDK + pytest plugin
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
- **API key storage:** Keys are stored as SHA-256 hashes. The raw key is only returned once at creation. `key_hint` = `raw_key[:8] + "..."` (max 11 chars; column is `String(12)`).
- **Structured logging:** Use `structlog.get_logger(__name__)` — never `print()` or raw `logging.getLogger()`.

### Frontend

- **SWR for all data fetching.** Add hooks in `hooks/` that wrap `useSWR`; pages import hooks, not raw service calls directly.
- **Zustand for global state.** Only project selection and project list live in `projectStore.ts`. Auth state lives in `authStore.ts`. Per-page state stays local.
- **`api.ts` is the Axios base.** All service files import from `services/api.ts`. Never create a second Axios instance.
- **TestCase breadcrumbs** use `runId?.slice(0,8)` — the `build_number` field lives on `TestRun`, not `TestCase`.
- **TypeScript strict mode is on** — avoid `any`; use `unknown` + type guards when necessary.
- **Sidebar navigation** has three sections in order: main nav (top), AI Agents (middle), Management (bottom — Projects, Releases, Users). Settings is in the footer.

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

14. **Self-registration role + must_change_password** — `POST /api/v1/auth/register` always creates users with `role=VIEWER` and `must_change_password=True`. The `POST /api/v1/auth/first-time-reset` endpoint (requires JWT, no current password) clears this flag. `ProtectedRoute` redirects any authenticated user with `must_change_password=True` to `/reset-password`. Migration `0014` adds the `must_change_password` column to the `users` table.

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
| `installation.md` | GCP VM deployment guide |
| `docs/DEVELOPMENT.md` | Developer workflow, iterative phases |
| `docs/cloud-run-cloud-sql.md` | Cloud Run + Cloud SQL deployment |
| `.env.example` | Environment variable reference |
| `Makefile` | All developer commands |