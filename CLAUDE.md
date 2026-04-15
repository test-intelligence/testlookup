# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

# TestLookup

## Project Overview

**TestLookup** is a 360° AI-powered software testing intelligence platform. It ingests test results from 50+ frameworks, uses a LangChain ReAct agent (via Ollama locally or cloud LLMs) to correlate failures, and pushes structured root-cause analysis to Jira. Includes deep multi-agent investigation, RAG test case generation, fine-tuning pipeline, live streaming, observability, PII redaction, user/API-key management, CLI, and MCP server.

**Subdirectory guides** (auto-loaded by Claude Code):
- `backend/CLAUDE.md` — Backend patterns, adding endpoints/agents/tools, test patterns
- `frontend/CLAUDE.md` — Frontend patterns, adding pages/hooks/services

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
make seed-data            # Re-run seed (idempotent)
make stop                 # Stop all services
make clean                # Stop + remove volumes (destructive)
make migrate              # Run pending migrations (manual fallback)
make migrate-create MSG="name"  # Auto-generate new migration
make test-backend         # pytest tests/ -v
make test-frontend        # vitest
make test-e2e             # playwright
make lint                 # ruff + eslint
make format               # ruff format + prettier
make type-check           # mypy + tsc
make shell-backend        # bash in backend container
make build-java-sdk       # Build Java SDK fat JAR

# Single backend test:
docker compose exec backend pytest tests/test_agent.py::test_name -v
```

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
  - `streams/` — Redis Streams producer/consumer
  - `worker/` — Celery app + tasks
- `backend/migrations/` — Alembic versions (use `git log` for full history)
- `frontend/src/`
  - `pages/`, `components/`, `services/`, `hooks/`, `store/`
  - `components/analytics/` — customizable widget system
  - `components/rag/` — RAG generation components
- `cli/` — Typer + Rich + httpx CLI
- `mcp/` — MCP Server (stdio + SSE)
- `client/` — Python + Java client SDKs
- `k8s/` — Kustomize base + overlays

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

### Backend

- **Pydantic v2 only.** Use `@field_validator(..., mode="before")` + `@classmethod`. Never v1 `@validator` or `class Config`.
- **Async everywhere.** All DB/HTTP/service methods `async def`. Use `async with AsyncSession` from `db/postgres.py`.
- **No SQL INTERVAL string literals with params.** Compute `period_start` in Python with `timedelta` and pass as bound param.
- **Thin routers.** Business logic belongs in `services/`.
- **Celery tasks** accept serializable args (IDs, dicts), never ORM objects.
- **AgentExecutor** must include `max_execution_time=settings.AI_TIMEOUT_SECONDS`.
- **Role hierarchy:** VIEWER < TESTER < QA_ENGINEER < QA_LEAD < ADMIN. Use `require_role(UserRole.X)`.
- **API key storage:** SHA-256 hashes. `key_hint` = `raw_key[:8] + "..."` (11 chars; column is `String(12)`). Nullable `project_id` FK for project-scoped keys. Use `get_api_key_context` dep when project scope enforcement needed.
- **Structured logging:** `structlog.get_logger(__name__)` — never `print()`.
- **Analysis dispatch:** All classification via `services/analysis_router.py`. Never call `run_triage_agent()` or `RulesEngine` directly.
- **ML feature extraction deterministic.** Same inputs → same vector. Names in `ml/feature_extractor.py:FEATURE_NAMES` must match training order. Inference budget <5ms.
- **PII redaction:** All data through `privacy_service.py` before persistence/logging/LLM/report rendering. Placeholder always `[REDACTED]`.
- **Tag normalization:** `services/tag_utils.py:normalize_tag()`. 13 system tags are reserved.
- **RAG feature gating:** Check `KNOWLEDGE_RAG_ENABLED` via fallback chain (Redis → DB → env).
- **Dual auth:** `get_current_user_or_api_key()` in `core/deps.py` — JWT first, then API key.
- **Email:** Use `services/email_service.py` async. Dispatch via Celery — never sync in request handlers.
- **AI config:** `services/ai_config_resolver.py`. Precedence: DB → secrets → env. Cached in Redis (60s TTL).
- **Ingestion:** New paths must use `create_run_from_payload()` → `ingest_test_results()` → `finalize_run()` from `ingestion_pipeline.py`. Never duplicate post-ingestion orchestration.
- **Feature flags:** Every new capability ships behind a flag. Check via `services/feature_flags.is_enabled(key, project_id, user)`. Resolution: in-proc cache (30s) → Redis (30s) → Postgres `feature_flags` row → env fallback. CRUD is audited in `settings_audit_log`. Never bypass the gate.
- **Offline mode kill switch:** `AI_OFFLINE_MODE=true` is a hard gate above every outbound integration (GitHub Checks, webhooks, hosted LLM, hosted Ragas). Services must check it before any network call; feature flags alone are insufficient.
- **Decision trail:** Aggregated by `services/decision_trail_service.build_trail()` from Postgres (`AgentStageResult.decision_log` JSONB + `AIAnalysis.routing_metadata`) + Mongo (`pipeline_event_log`). Use `BaseAgent.log_decision()` at non-trivial branches only — signal > noise.
- **Webhook signing:** Outbound events via `services/webhook_service.emit_event()`. Delivery worker signs payloads with HMAC-SHA256 using the subscription secret and retries with exponential backoff. Never emit events synchronously in request handlers.
- **Perf baselines:** `PerfBaseline` uses Welford's online algorithm (`sample_count`, `mean_ms`, `m2`, `stddev_ms`, `p95_ms`). Update via `perf_regression_service.update_baseline()` — never recompute from TestCase history at gate time.
- **RAG faithfulness:** `rag_faithfulness_service.evaluate()` is pluggable (Ollama default, Ragas optional). When `AI_OFFLINE_MODE=true` the evaluator falls back to Ollama regardless of config. Low-score cases get `needs_review_reason="faithfulness_below_threshold"`.
- **Client SDK config:** Constructor args > env vars > `testlookup.yaml` > defaults.

### Frontend

- **SWR for all data fetching.** Hooks wrap `useSWR`; pages import hooks.
- **Zustand for global state** — only project selection (`projectStore`) and auth (`authStore`). Per-page state stays local.
- **`services/api.ts`** is the single Axios base. Never create second instance.
- **TypeScript strict.** Avoid `any`; use `unknown` + type guards.
- **Analytics widgets:** `useAnalyticsView` + `AnalyticsGrid` + `AnalyticsWidget`. Max 12 widgets per page.
- **CSS theming:** CSS custom properties (`var(--color-bg)` etc). Never hardcode colors.
- **Client-side PII sanitization:** `utils/errorReporting.ts` redacts before sending error reports.

---

## Known Pitfalls

Critical bugs encountered and fixed — avoid reintroducing:

1. **SQL INTERVAL parameterization** — `INTERVAL ':days days'` does NOT work in PostgreSQL. Use Python `timedelta`.
2. **Pydantic v1 syntax** removed. All validators use `@field_validator`.
3. **AgentExecutor timeout** — without `max_execution_time`, slow Ollama hangs indefinitely.
4. **TestCase `build_number`** — lives on `TestRun`, not `TestCase`.
5. **API key `key_hint` column** is `String(12)`. Must be `raw_key[:8] + "..."` (11 chars). Using `[:10]` overflows.
6. **UserRole stored as String(20)** in `project_members`, `api_keys`, `user_invitations` — not native enum.
7. **All-Projects mode** — `ALL_PROJECTS_ID = "all"` sentinel. Backend `project_id` params must be `Optional[uuid.UUID] = None`; None path omits WHERE filter. Never pass string `"all"` as UUID.
8. **Live session token auth** — `/stream/events/batch` uses `X-Session-Token` header (Redis O(1)), not JWT.
9. **Dev auto-login** — `POST /api/v1/auth/dev-login` returns 404 unless `APP_ENV=development` AND `DEV_AUTO_LOGIN_ENABLED=true`. Lives in public `auth` router.
10. **Self-registration** — `/auth/register` creates `QA_ENGINEER` with `must_change_password=True`. `/auth/first-time-reset` clears flag. `ProtectedRoute` redirects to `/reset-password`.
11. **SSO enforcement** — When `SSO_REQUIRED`, only ADMIN can use password login (if `SSO_ADMIN_FALLBACK_ENABLED=true`). SSO and SCIM routers are PUBLIC (own auth).
12. **SSO cert** — Never expose raw IdP X.509 in responses. Use `idp_certificate_fingerprint` (SHA-256).
13. **Report share links** — `secrets.token_urlsafe(48)`. `shared_reports.router` is PUBLIC (token-authenticated). Expiry 1-30 days.
14. **Zustand object selectors cause infinite loops.** Never return inline object from selector. Use individual primitive selectors.
15. **PII redaction depth limit** — max 10 levels. Placeholder always `[REDACTED]` — never customize.
16. **System tags reserved** — 13 names (passed, failed, skipped, broken, flaky, regression, duplicate, all_passed, has_failures, has_skips, flaky_content, regression_detected, needs_review) cannot be custom tags.
17. **Knowledge source dedup** — UNIQUE on `(project_id, canonical_url)`. Content change via SHA-256.
18. **RAG chunk `vector_id` UNIQUE.** Re-syncing must deactivate old chunks (`is_active=false`) before creating new.
19. **Generation source staleness** — If source hash changes after re-sync, linked cases marked `is_stale`.
20. **Ingestion file size limit** — 50 MB. Rejects 413. Hardcoded `MAX_FILE_SIZE` in `routers/ingest.py`.
21. **Ingestion format auto-detection** — inspects first 2KB. Use `format=testng` explicitly for ambiguous TestNG XML.
22. **Project-scoped API keys** — `ApiKey.project_id` nullable. NULL = user-scoped, non-NULL = project-scoped. Only ADMIN creates project-scoped or keys for other users. Use `get_api_key_context` to enforce.
23. **Release gate policy fallback** — When no `ReleaseGatePolicy`, falls back to hardcoded thresholds in `config.py`. Policy precedence: project → system default → hardcoded. `dimension_weights` must sum to 1.0 (±0.01).
24. **Report composition uses cached snapshot** — reads from `RunIntelligenceSnapshot.payload`, not individual tables.
25. **Suite membership sync** — must run after ingestion aggregates computed. Deleted tests → `<suite>-deleted` bucket with `needs_review`.
26. **Feature flag double-declare** — `FeatureFlag` lives exactly once in `models/postgres.py` (Tier 0A). Never resurrect the legacy `flag_key`/`scope`/`enabled` shape — it conflicts on `__tablename__` and the new schema uses `key`/`enabled_global`/`enabled_projects`/`enabled_roles`/`rollout_percent`.
27. **JSONB columns on new tables** — `feature_flags.enabled_projects`, `feature_flags.enabled_roles`, `ai_analysis.routing_metadata`, `flaky_quarantine_requests.rationale`, `compliance_packs.metadata_snapshot`, `webhook_subscriptions.events`, `webhook_deliveries.event_payload` are all `JSONB` in Postgres. ORM declarations must use `sqlalchemy.dialects.postgresql.JSONB`, not generic `JSON`, so containment operators and GIN indexes work.
28. **Flaky quarantine state machine** — `RE_QUARANTINED` is a distinct terminal state for tests that failed a release recheck. The ingestion enforcement path treats both `QUARANTINED` and `RE_QUARANTINED` as "active" — do not collapse them at the end of `run_recheck_cycle`, or the re-quarantine signal is lost in reports.
29. **Service ownership rule fields** — `ServiceOwnershipRule` columns are `match_pattern` / `service_name` / `team_name` (NOT `glob_pattern` / `owner` / `team`). Filter by `is_active.is_(True)` and order by `priority.desc()`. `team_value_metrics_service` walks these rules — keep it in sync with the ORM field names.
30. **LLM cost budget lookups** — `project_llm_usage` must be indexed on `(project_id)` and `(period_start)`. Budget checks filter by project; missing indexes table-scan at scale. See migration 0070 for the follow-up index.
31. **Weekly retro digest** — uses the same `dispatch_scheduled_digests` task as daily digests, gated by `DigestSchedule.WEEKLY_RETRO` and a Monday-morning Celery beat entry. Narrative is rendered by `retro_digest_service._compose_narrative`; offline mode forces the template path without calling the LLM.

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

## Tier 0-2 Feature Map (migrations 0062–0070)

All features ship behind feature flags and honour `AI_OFFLINE_MODE`. See the
service/router/migration column for the entry points.

| Tier | Feature | Service | Router | Migration | Flag key |
|------|---------|---------|--------|-----------|----------|
| 0A | Feature flag service | `services/feature_flags.py` | `routers/feature_flags.py` | 0062 | — |
| 0B | Decision trail UI | `services/decision_trail_service.py` | `routers/decision_trail.py` | 0061/0062 | `decision_trail_ui` |
| 1-1 | Cypress + Playwright parsers | `services/cypress_parser.py`, `playwright_parser.py` | (ingest) | 0063 | `cypress_ingest`, `playwright_ingest` |
| 1-2 | LLM cost budget | `services/llm_cost_budget.py` | `routers/llm_cost_budget.py` | 0064, 0070 | `llm_cost_budget` |
| 1-3 | Flaky auto-quarantine | `services/flaky_quarantine_service.py` | `routers/flaky_quarantine.py` | 0065 | `flaky_auto_quarantine` |
| 1-4 | Release compliance pack | `services/compliance_pack_service.py` | `routers/compliance_packs.py` | 0066 | `release_compliance_pack` |
| 1-5 | GitHub Checks integration | `services/github_checks_service.py` | `routers/github_integration.py` | 0067 | `github_checks` |
| 2-6 | Outbound webhooks | `services/webhook_service.py` | `routers/webhooks_outbound.py` | 0068 | `outbound_webhooks` |
| 2-7 | MCP tool parity | `mcp/tools/{decision_trail,compliance_pack,quarantine,billing,defects,governance}.py` | — | — | — |
| 2-8 | Two-run compare | `services/run_compare_service.py` | `routers/run_compare.py` | — | — |
| 2-9 | RAG faithfulness guardrails | `services/rag_faithfulness_service.py` | — | 0069 | `rag_faithfulness` |
| 2-10 | Perf regression detection | `services/perf_regression_service.py` | — | 0069 | `perf_regression_detection` |
| 2-11 | Team value metrics split | `services/team_value_metrics_service.py` | (value metrics) | — | — |
| 2-12 | Weekly auto-retro digest | `services/retro_digest_service.py` | (digests) | 0069 | `weekly_retro_digest` |

Celery beat additions: `nightly-flaky-quarantine-maintenance` (04:00 UTC),
`nightly-perf-baseline-refresh` (04:30 UTC), `monday-weekly-retro-digests`
(Mon 07:05 UTC). See `backend/app/worker/celery_app.py`.

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

- **MCP** (`mcp/`): 24 tools, 10 resources, 6 prompts. `make mcp-start` (stdio) or `make mcp-sse` (SSE on :8002).
- **CLI** (`cli/`): 11 command groups (auth, keys, projects, runs, tests, search, intelligence, deep, reports, upload, health). Profiles at `~/.config/testlookup/profiles.json`. Env fallbacks: `TESTLOOKUP_URL`, `TESTLOOKUP_API_KEY`.
