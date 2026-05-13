# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

# TestLookup

## Project Overview

**TestLookup** is a 360° AI-powered software testing intelligence platform. It ingests test results from 50+ frameworks, uses a LangChain ReAct agent (via Ollama locally or cloud LLMs) to correlate failures, and pushes structured root-cause analysis to Jira. Includes deep multi-agent investigation, RAG test case generation, fine-tuning pipeline, live streaming, observability, PII redaction, user/API-key management, CLI, and MCP server.

**Subdirectory guides** (auto-loaded by Claude Code):
- `backend/CLAUDE.md` — Backend patterns, adding endpoints/agents/tools, test patterns
- `frontend/CLAUDE.md` — Frontend patterns, adding pages/hooks/services

**Work tracking:**
- `docs/PROGRESS.md` — What's been shipped, migration ledger, current build state
- `docs/BACKLOG.md` — Ordered next-task list, tech debt, risks, cursor for where to pick up
- `docs/features/FEATURE_FLAG_INVENTORY.md` — Live feature-flag inventory (defaults, owners, graduation criteria)
- Start any resumed session by reading PROGRESS + BACKLOG.

---

## Architecture

```
React SPA (frontend:3000)
      ↓
FastAPI Backend (backend:8000)
      ↓
PostgreSQL  MongoDB  Redis  MinIO  ChromaDB  Ollama
      ↓
Celery Workers (AI triage, quality gates, fine-tuning)
      ↓
MCP Server (mcp:8002)
```

**Backend:** FastAPI + SQLAlchemy (async) + Motor (MongoDB) + Celery
**Frontend:** React 18 + Vite + TypeScript + Tailwind + Zustand + SWR
**AI:** LangChain ReAct + LangGraph + sklearn HistGradientBoosting + rules engine
**Databases:** PostgreSQL 16, MongoDB 7, Redis 7, MinIO, ChromaDB
**Observability:** OpenTelemetry → Jaeger, Prometheus, Grafana

---

## Common Commands

```bash
make dev                  # Start core stack (no LLM). Auto-creates .env, runs migrations + seed
make dev-llm              # Full stack WITH Ollama + ChromaDB
make dev-lite             # Minimal stack for low-resource machines (no Ollama/ChromaDB)
make dev-setup            # First-time full setup: start stack + pull recommended LLM models
make demo                 # Core stack + pre-loaded sample data
make seed-data            # Re-run seed (idempotent)
make seed-data-reset      # Wipe seed data and regenerate from scratch
make simulate-upload      # Simulate a single Jenkins test run upload to MinIO
make create-admin         # Create initial admin user (Docker Compose)
make stop                 # Stop all services
make clean                # Stop + remove volumes (destructive)
make migrate              # Run pending migrations (manual fallback)
make migrate-create MSG="name"  # Auto-generate new migration
make migrate-down         # Rollback last migration
make migrate-status       # Show migration status
make test-backend         # pytest tests/ -v
make test-frontend        # vitest
make test-e2e             # playwright
make benchmark            # Classification accuracy + throughput benchmarks
make lint                 # ruff + eslint
make format               # ruff format + prettier
make type-check           # mypy + tsc
make shell-backend        # bash in backend container
make build-java-sdk       # Build Java SDK fat JAR
# Kubernetes deploys: make k8s-deploy-{dev,staging,prod,openshift,homelab} — see k8s/ overlays

# Single backend test:
docker compose exec backend pytest tests/test_agent.py::test_name -v
```

**Local environment:**
- Backend Python 3.11+. For non-Docker pytest: `cd backend && pip install -r requirements-dev.txt`.
- On Windows, run `make` targets from Git Bash or WSL — PowerShell has no `make`.

### Service URLs

| Service | URL |
|---------|-----|
| Dashboard | http://localhost:3000 |
| API + Swagger | http://localhost:8000/docs |
| MinIO Console | http://localhost:9001 |
| Flower | http://localhost:5555 |
| MCP SSE | http://localhost:8002/sse |
| PostgreSQL | localhost:5433 (remapped from 5432) |
| Ollama | http://localhost:11434 |

---

## Project Structure (Key Directories)

- `backend/app/`
  - `main.py` + `bootstrap.py` — app factory and router registration
  - `core/` — config, security (JWT), deps (role guards), logging, tracing, metrics
  - `db/` — async clients: `postgres.py`, `mongo.py`, `minio.py`, `redis_client.py`
  - `models/postgres.py` — SQLAlchemy ORM; `models/schemas.py` — Pydantic v2
  - `routers/` — thin HTTP routers (registered in `bootstrap.py`)
  - `services/` — business logic
    - `ingestion_pipeline.py` — shared ingestion (run creation, upsert, orchestration)
    - `analysis_router.py` — central LLM/ML/Rules dispatcher
    - `rules_engine.py`, `ml/` — ML classifier + feature extraction
    - `privacy_service.py`, `redaction_service.py` — PII scrubbing
    - `connectors/`, `knowledge_*_service.py`, `rag_*_service.py` — RAG
    - `ai_config_resolver.py` — single source of truth for AI config
  - `agents/` — LangGraph multi-agent pipelines
  - `tools/` — 11 LangChain agent tools
  - `middleware/` — request middleware (PII redaction, request ID, rate limiting)
  - `streams/` — Redis Streams producer/consumer
  - `worker/` — Celery app + tasks
- `backend/migrations/` — Alembic versions (use `git log` for full history)
- `frontend/src/`
  - `pages/`, `components/`, `services/`, `hooks/`, `store/`
  - `components/analytics/` — customizable widget system
  - `components/rag/` — RAG generation components
- `cli/testlookup_cli/` — Typer + Rich + httpx CLI (commands under `commands/`)
- `mcp/` — MCP Server (stdio + SSE)
- `client/` — Python + Java client SDKs
- `k8s/` — Kustomize base + overlays
- `AGENTS.md` (repo root) — detailed agent prompt/workflow specifications. Reference-only, not auto-loaded.

---

## Key Environment Variables

See `.env.example` for full list. Important ones:

| Variable | Description |
|----------|-------------|
| `APP_ENV` | dev / staging / prod |
| `LLM_PROVIDER` | ollama \| openai \| gemini \| lmstudio \| vllm |
| `LLM_MODEL` | e.g., qwen2.5:7b |
| `AI_OFFLINE_MODE` | true = Ollama only |
| `STORAGE_BACKEND` | minio \| local (required) |
| `ANALYSIS_MODE` | auto \| llm \| ml \| rules |
| `JWT_SECRET_KEY` | Strong random in prod |
| `DEV_AUTO_LOGIN_ENABLED` | dev-only bypass |
| `SSO_ENABLED`, `SCIM_ENABLED` | SSO/SAML/SCIM |
| `KNOWLEDGE_RAG_ENABLED` | RAG feature gate |
| `SMTP_*` | Email notifications |

---

## Database Migrations

Migrations run **automatically** at container startup (`alembic upgrade head` prepended to uvicorn CMD). The startup command retries every 5s. For full migration history, use `git log backend/migrations/versions/`.

---

## Coding Conventions

Stack-specific rules live in `backend/CLAUDE.md` and `frontend/CLAUDE.md`. The cross-cutting ones every contributor needs:

- **Role hierarchy:** VIEWER < TESTER < QA_ENGINEER < QA_LEAD < ADMIN. Use `require_role(UserRole.X)` on backend, `usePermissions()` on frontend.
- **PII redaction at every boundary.** All data flows through `services/privacy_service.py` before persistence / logging / LLM prompts / report rendering. Placeholder is always `[REDACTED]`. Frontend `utils/errorReporting.ts` mirrors this for error reports.
- **Feature flags gate every new capability.** Backend: `services/feature_flags.is_enabled(key, project_id, user)`. Resolution chain: in-proc cache (30s) → Redis (30s) → Postgres `feature_flags` → env fallback. CRUD writes to `settings_audit_log`.
- **Offline mode is a hard gate above feature flags.** When `AI_OFFLINE_MODE=true`, every outbound integration (GitHub Checks, webhooks, hosted LLM, hosted Ragas) must short-circuit before any network call. Flags alone are not sufficient.
- **Ingestion goes through one pipeline.** `create_run_from_payload()` → `ingest_test_results()` → `finalize_run()` in `services/ingestion_pipeline.py`. Never duplicate post-ingestion orchestration in a new path.
- **Analysis dispatch goes through one router.** `services/analysis_router.classify_test()`. Never call `run_triage_agent()` or `RulesEngine` directly — the router handles mode resolution and fallback.
- **AI config has one resolver.** `services/ai_config_resolver.py` — precedence: DB → secrets → env, cached 60s in Redis.
- **All-Projects sentinel.** `ALL_PROJECTS_ID = "all"` on the frontend; backend `project_id` params are `Optional[uuid.UUID] = None`. Never send the literal string `"all"` to the backend.
- **Client SDK config precedence:** constructor args > env vars > `testlookup.properties` (preferred) / `testlookup.yaml` (legacy) > defaults. Canonical key prefix is `testlookup.*` (e.g. `testlookup.endpoint`, `testlookup.api.key`, `testlookup.project`, `testlookup.launch`) — analogous to ReportPortal's `rp.*`. `launch_name` is the human-readable label shown in Live Execution and Runs; falls back to `build_number` when unset. See `docs/integration/` for per-framework guides.

---

## Known Pitfalls

Cross-cutting gotchas — backend-internal and frontend-internal pitfalls live in their respective subdirectory CLAUDE.md files.

1. **All-Projects mode** — `ALL_PROJECTS_ID = "all"` is a frontend sentinel. Backend `project_id` params must be `Optional[uuid.UUID] = None`; the `None` path omits the WHERE filter. Never pass the literal string `"all"` as a UUID.
2. **Live session token auth** — `/stream/events/batch` uses the `X-Session-Token` header (Redis O(1) lookup), not JWT.
3. **Dev auto-login** — `POST /api/v1/auth/dev-login` returns 404 unless `APP_ENV=development` AND `DEV_AUTO_LOGIN_ENABLED=true`. Lives in the public `auth` router.
4. **Self-registration** — `/auth/register` creates a `QA_ENGINEER` with `must_change_password=True`. `/auth/first-time-reset` clears the flag. `ProtectedRoute` redirects to `/reset-password` while it is set.
5. **SSO enforcement** — When `SSO_REQUIRED`, only ADMIN can use password login (and only if `SSO_ADMIN_FALLBACK_ENABLED=true`). SSO and SCIM routers are PUBLIC — they own their auth.
6. **SSO cert** — Never expose raw IdP X.509 in responses. Use `idp_certificate_fingerprint` (SHA-256).
7. **Report share links** — `secrets.token_urlsafe(48)`. `shared_reports.router` is PUBLIC (token-authenticated). Expiry 1–30 days.
8. **System tags reserved** — 13 names (passed, failed, skipped, broken, flaky, regression, duplicate, all_passed, has_failures, has_skips, flaky_content, regression_detected, needs_review) cannot be used as custom tags.
9. **Ingestion file size limit** — 50 MB hard cap (rejects 413). `MAX_FILE_SIZE` in `routers/ingest.py`.
10. **Ingestion format auto-detection** — inspects first 2 KB. Pass `format=testng` explicitly for ambiguous TestNG XML.
11. **Project-scoped API keys** — `ApiKey.project_id` is nullable: NULL = user-scoped, non-NULL = project-scoped. Only ADMIN can create project-scoped keys or keys for other users. Use the `get_api_key_context` dependency to enforce scope.
12. **Release gate policy fallback** — When no `ReleaseGatePolicy` row exists, falls back to hardcoded thresholds in `config.py`. Precedence: project → system default → hardcoded. `dimension_weights` must sum to 1.0 (±0.01).
13. **Report composition reads from cache** — `RunIntelligenceSnapshot.payload`, not individual tables. Stale snapshots will show stale reports.
14. **Suite membership sync** — must run after ingestion aggregates are computed. Tests removed from a suite move to `<suite>-deleted` bucket with `needs_review`.

---

## Analysis Engine Modes

`ANALYSIS_MODE` env var or Settings > AI Configuration (ADMIN):

| Mode | Engine | Latency | Accuracy |
|------|--------|---------|----------|
| `llm` | LangChain ReAct + 5 tools | ~300ms | 85-95% |
| `ml` | sklearn HistGradientBoosting | ~2ms | >85% |
| `rules` | Pattern matching | ~0.2ms | 60-75% |
| `auto` | Smart fallback ML→LLM→Rules | varies | best available |

Dispatch: `analysis_agent._analyse_one()` → `analysis_router.get_analysis_mode()` → engine. Mode persisted in `app_settings` (key `ai_config.analysis_mode`), cached in Redis.

**ML cold start:** When `ANALYSIS_MODE=ml` but no trained model, falls back to rules. Minimum 200 labeled samples for training.

---

## Feature Flag Inventory

All Tier 0-2 features ship behind feature flags and honour `AI_OFFLINE_MODE`. The live inventory (with defaults, owners, graduation criteria) is `docs/features/FEATURE_FLAG_INVENTORY.md`. Resolution and CRUD live in `services/feature_flags.py` and `routers/feature_flags.py`.

Celery beat additions for these features: `nightly-flaky-quarantine-maintenance` (04:00 UTC), `nightly-perf-baseline-refresh` (04:30 UTC), `monday-weekly-retro-digests` (Mon 07:05 UTC). See `backend/app/worker/celery_app.py`.

---

## Adding New Features

### Full-stack feature checklist
1. Pydantic schemas → `backend/app/models/schemas.py`
2. ORM model → `backend/app/models/postgres.py`
3. Migration → `make migrate-create MSG="add_feature_table"`
4. Service → `backend/app/services/<feature>.py`
5. Router → `backend/app/routers/<feature>.py`
6. Register router → `backend/app/bootstrap.py`
7. Tests → `backend/tests/test_<feature>.py`
8. Frontend types → `frontend/src/types/<feature>.ts`
9. API service → `frontend/src/services/<feature>Service.ts`
10. SWR hook → `frontend/src/hooks/use<Feature>.ts`
11. Page → `frontend/src/pages/<Feature>Page.tsx`
12. Route → `frontend/src/App.tsx` (lazy import)
13. Sidebar entry → `frontend/src/components/layout/Sidebar.tsx`

See `backend/CLAUDE.md` and `frontend/CLAUDE.md` for exact templates.

---

## Ingestion API

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/api/v1/ingest` | JSON batch from SDKs |
| POST | `/api/v1/ingest/file` | File upload (JUnit/TestNG XML, Allure JSON) |

Both return 202 Accepted with async Celery processing. File upload max 50 MB. Format auto-detection: `.json` → allure, `<testng-results` → testng, `<testsuite` → junit.

After parsing, `ingestion_pipeline.py`: creates TestRun → upserts TestCase → updates aggregates → syncs suite membership → auto-tags → links release → enqueues notifications → triggers AI analysis.

---

## Live Streaming Architecture

```
Client SDK → batch events every 100ms
  → POST /api/v1/stream/events/batch (X-Session-Token, O(1) Redis)
  → Redis Streams: testlookup:stream:live_events
  → LiveEventStreamConsumer
  → WebSocket /ws/live/{project_id} + SSE /api/v1/stream/sse/{project_id}
On run complete → run_agent_pipeline Celery task → PostgreSQL
```

---

## RAG Knowledge Generation

Knowledge sources (Jira, Confluence, URLs, Documents) → Connectors → MinIO (raw) → Chunking → ChromaDB (vectors) → Retrieval → Generation (LLM + citations) → Review.

Feature gated by `KNOWLEDGE_RAG_ENABLED` (Redis → DB → env fallback).

Key endpoints under `/api/v1/knowledge-sources/*` and `/api/v1/test-management/*`. Connectors are pluggable via `services/connectors/registry.py` — new connectors implement `BaseConnector`.

---

## MCP Server & CLI

- **MCP** (`mcp/`): 48 tools across 17 modules, 9 resources, 6 prompts. `make mcp-start` (stdio) or `make mcp-sse` (SSE on :8002). Tool registration is in `mcp/server.py`; counts auto-derive from `@mcp.tool` / `@mcp.resource` / `@mcp.prompt` decorators.
- **CLI** (`cli/testlookup_cli/`): 11 command groups (auth, keys, projects, runs, tests, search, intelligence, deep, reports, upload, health). Profiles at `~/.config/testlookup/profiles.json`. Env fallbacks: `TESTLOOKUP_URL`, `TESTLOOKUP_API_KEY`.
