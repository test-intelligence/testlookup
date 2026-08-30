# TestLookup Architecture

> **Detailed engineering docs live in [`architecture/`](./architecture/):**
> [system & runtime architecture with diagrams](./architecture/README.md) ·
> [database & schema design (ER diagrams + full reference)](./architecture/DATABASE_SCHEMA.md) ·
> [developer guide (conventions, gates, bug classes)](./architecture/DEVELOPER_GUIDE.md).
> This page is the short overview.

## System overview

```
        Browser            CLI (Typer)         AI assistant / IDE / CI
           |                   |                          |
      React SPA                |                     MCP Server
     (port 3000)               |              (port 8002 SSE · or stdio)
           |                   |                          |
           +-------------------+--------------------------+
                               |
                       FastAPI Backend
                        (port 8000)
                               |
        +----------+----------+----------+----------+-----------+
        |          |          |          |          |           |
   PostgreSQL   MongoDB     Redis      MinIO     ChromaDB    Prometheus
     (5433)      (v7)      (6379)   (9000/9001) (8001, opt)  / Jaeger / Grafana
        |                     |
        |              Celery Workers + Beat
        |    queues: critical > ingestion (+ per-shard) > ai_analysis > default
        |                     |
        |                  Ollama
        |              (optional, 11434)
```

> Ports above are the Docker Compose host mappings. PostgreSQL is published on
> **5433** (container 5432) to avoid colliding with a local Postgres; ChromaDB on
> **8001** (container 8000); MinIO exposes **9000** (S3 API) and **9001** (console).
> Under high ingestion volume the single `ingestion` queue fans out into
> per-shard queues (`ingestion.shard.<i>`) for fairness — see the ingestion
> redesign notes.

## Components

| Component | Technology | Purpose |
|-----------|-----------|---------|
| **Backend** | FastAPI + SQLAlchemy (async) + Motor (MongoDB) | REST API, auth, business logic, ingestion orchestration |
| **Frontend** | React 18 + Vite + TypeScript + Tailwind + Zustand + SWR | Dashboard, analytics widgets, admin pages |
| **Workers** | Celery + Redis broker | AI triage, quality gates, fine-tuning, webhook delivery, nightly maintenance |
| **AI engine** | LangChain ReAct + LangGraph + scikit-learn HistGradientBoosting + rules engine | Multi-mode analysis: rules / ML / LLM / auto fallback |
| **MCP server** | Python (stdio + SSE transports) | 48 tools, 9 resources, 6 prompts for AI assistant integration |
| **CLI** | Typer + Rich + httpx | 11 command groups, multi-profile auth |
| **PostgreSQL** | v16 | Primary relational store (runs, tests, projects, users, configs, audit) |
| **MongoDB** | v7 | Pipeline event logs, workflow state, immutable audit streams |
| **Redis** | v7 | Celery broker, feature flag cache, session tokens, live event streams |
| **MinIO** | S3-compatible | Object store for compliance packs, reports, RAG documents |
| **ChromaDB** | Vector store (optional) | Semantic search embeddings, RAG chunk retrieval |
| **Ollama** | Local LLM runtime (optional) | Offline AI-assisted triage (qwen2.5, llama3, mistral, etc.) |

## Analysis engine modes

The analysis engine is pluggable -- four modes, configurable per-project via Settings or the `ANALYSIS_MODE` env var.

| Mode | Engine | Typical latency | Offline? |
|------|--------|----------------|----------|
| `rules` | Pattern matching (14 patterns + historical flakiness + regression + suite-level) | sub-millisecond | Yes |
| `ml` | scikit-learn HistGradientBoosting (31-feature vector) | ~2ms per test | Yes |
| `llm` | LangChain ReAct agent with 5 investigation tools via Ollama | ~300ms per test | Yes (local) |
| `auto` | Smart fallback: ML first, LLM on low confidence, rules as last resort | varies | Yes |

All dispatch goes through `services/analysis_router.py`. The router handles mode selection, fallback, and decision traceability -- see CLAUDE.md for the decision-trail contract.

## Data flow

```
Test runner (pytest / JUnit / Allure / Cypress / Playwright)
    |
    v
POST /api/v1/ingest (JSON batch) or POST /api/v1/ingest/file (file upload)
    |
    v
Ingestion pipeline (services/ingestion_pipeline.py):
    create_run_from_payload() --> ingest_test_results() --> finalize_run()
    |
    v
Post-ingestion orchestration (each step runs in its own session):
    suite_sync --> auto_tagging --> release_linking --> notifications --> AI analysis (Celery)
    |
    v
AI analysis (Celery worker):
    analysis_router.classify_test() per failing test
    |    \--> rules_engine  (if mode=rules)
    |    \--> ml_classifier (if mode=ml, fallback to rules if no trained model)
    |    \--> agent.run_triage_agent() (if mode=llm, fallback to rules on timeout)
    |
    v
Results:
    PostgreSQL (AIAnalysis rows, AgentStageResult, decision_log)
    MongoDB (pipeline_event_log)
    Run Intelligence (cached snapshot)
    Optional: GitHub Checks post, webhook fan-out, release gate scoring
```

## Deployment matrix

| Platform | Status | Notes |
|----------|--------|-------|
| Docker Compose (local) | Supported | Primary dev path. `make dev` (core) / `make dev-llm` (full) |
| Kubernetes (Kustomize) | Supported | Base + overlays under `k8s/`. Tested on k3s and EKS. |
| Minikube | Supported | Works with the k8s base overlay. Resource-constrained -- use core mode. |
| Cloud Run / ECS | Experimental | Dockerfiles are multi-stage and cloud-friendly but no CI/CD template ships yet. |
| OpenShift | Experimental | k8s overlay exists under `k8s/overlays/openshift/` but not tested in production. |
| Bare metal (no container) | Not supported | Dependencies are too numerous for a manual install. Use Docker Compose. |

## Project structure (key directories)

```
backend/
  app/
    main.py + bootstrap.py        -- app factory and router registration
    core/                         -- config, security (JWT), deps (role guards), logging, tracing, metrics
    db/                           -- async clients: postgres, mongo, minio, redis
    models/                       -- SQLAlchemy ORM (postgres.py) + Pydantic v2 (schemas.py)
    routers/                      -- thin HTTP routers (~71)
    services/                     -- business logic (~139 modules: ingestion, analysis, flags, ...)
    agents/                       -- LangGraph multi-agent pipelines (~15 agents)
    tools/                        -- LangChain agent tools
    worker/                       -- Celery app + tasks
  migrations/versions/            -- Alembic versions (0001 through 0092)
  tests/                          -- pytest (unit + integration + architectural ratchets)

frontend/
  src/
    pages/                        -- route-level components (~65)
    components/                   -- shared UI (analytics widgets, RAG, layout)
    services/                     -- API service layer (single Axios base in api.ts)
    hooks/                        -- SWR data-fetching hooks
    store/                        -- Zustand client state: authStore, projectStore,
                                     themeStore, timeWindowStore (server data uses SWR)
  tests/e2e/                      -- Playwright specs

cli/                              -- Typer + Rich CLI (11 command groups)
mcp/                              -- MCP server (stdio + SSE transports)
client/                           -- Python + Java client SDKs
k8s/                              -- Kustomize base + overlays
infra/                            -- monitoring (Prometheus rules, Grafana)
```

## Further reading

- [README.md](README.md) -- product overview and quick start
- [README_FULL.md](README_FULL.md) -- full feature documentation (SDK setup, ingestion options, CLI reference, etc.)
- [CONTRIBUTING.md](CONTRIBUTING.md) -- development setup and PR process
