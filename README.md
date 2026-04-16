# TestLookup

**Local-first test failure intelligence for CI pipelines and QA teams.**

Turn raw automated test results into actionable failure intelligence and release-risk signals -- locally, offline, and through API / CLI / UI / MCP.

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-blue)](https://python.org)
[![React 18](https://img.shields.io/badge/React-18-61DAFB)](https://reactjs.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115-009688)](https://fastapi.tiangolo.com)
[![MCP](https://img.shields.io/badge/MCP-Server-blueviolet)](https://modelcontextprotocol.io)

<!-- TODO (OS-3): replace with screenshot/GIF of ingestion → triage → release gate flow -->

## What it does

1. **Ingest test results** from JUnit, pytest, TestNG, Allure, Cypress, Playwright, and more
2. **Cluster failures** and surface regressions vs flaky recurrences vs infra anomalies
3. **Explain likely root causes** using rules, ML classifiers, or a local LLM (Ollama) -- no cloud calls required
4. **Provide release-risk signals** -- GO / CONDITIONAL_GO / NO_GO with reasons and override audit trail
5. **Expose everything** through UI, REST API, CLI, and [MCP server](https://modelcontextprotocol.io) (36 tools, 9 resources, 6 prompt workflows)
6. **Run fully offline** when required -- air-gapped mode blocks all outbound network calls

## Quick Start

Three run modes. Pick the one that fits.

| Mode | Command | What you get | Time |
|------|---------|-------------|------|
| **Core** (no LLM) | `make dev` | Rules/ML analysis, dashboards, CLI, MCP | ~5 min |
| **Full** (local LLM) | `make dev-llm` then `docker compose exec ollama ollama pull qwen2.5:7b` | Core + AI-assisted triage via Ollama + ChromaDB | ~10 min |
| **Demo** | `make demo` | Core + pre-loaded sample data (coming in v0.1.0) | ~3 min |

```bash
git clone https://github.com/anandtopu/testlookup.git
cd testlookup
cp .env.example .env     # edit secrets before starting
make dev                 # or: make dev-llm for full mode
```

Dashboard: http://localhost:3000 | API docs: http://localhost:8000/docs | MCP SSE: http://localhost:8002/sse

Prerequisites: Docker + Compose v2. Core mode: 4 GB RAM / 2 vCPU. Full mode: 8 GB / 4 vCPU.

## Feature matrix

Every feature is labelled **Core** (on by default in OSS), **Experimental** (in-repo but flag-off, may change), or **Enterprise** (future / commercial).

### Core

| Feature | Description |
|---------|-------------|
| Multi-framework ingestion | JUnit XML, TestNG, Allure JSON, Cypress, Playwright, pytest, Robot Framework, Cucumber |
| Analysis modes | Rules (pattern match, ~0.2ms) / ML (HistGradientBoosting, ~2ms) / LLM (Ollama ReAct, ~300ms) / Auto (smart fallback) |
| Run Intelligence | Single-pane summary: failure clusters, regression diff, risk score, role actions |
| Release gate | GO / CONDITIONAL_GO / NO_GO with explainable reasons and QA Lead override audit |
| Failure clustering | Regression Watchman: new_regression / known_flaky_recurrence / environmental_anomaly |
| Jira integration | Auto-promote failure clusters to Jira with 7-dimension severity scoring + duplicate dedup |
| Decision trail | Per-run "why did the AI do that" drawer with per-stage filter and full-text search |
| Two-run compare | Side-by-side diff with classification (new failures, regressions, duration spikes, renamed tests) |
| Flaky quarantine | Detection, QA Lead approval, active quarantine, nightly recheck, release/re-quarantine state machine |
| Perf regression | Per-test duration baselines (Welford algorithm) with 3-sigma spike detection at release-gate time |
| CLI | 11 command groups, multi-profile auth, table/JSON/YAML output |
| MCP server | 36 tools, 9 resources, 6 prompts -- query test health from IDE or CI agents ([reference](mcp/README.md)) |
| Dashboards | 30+ customizable analytics widgets, drag-and-drop layout |
| Live streaming | Real-time WebSocket dashboard during test execution via Redis Streams |
| User management | RBAC (VIEWER / TESTER / QA_ENGINEER / QA_LEAD / ADMIN), JWT + API key auth |
| PII redaction | Auto-scrub at all system boundaries (persistence, logging, LLM prompts, reports) |
| Email notifications | Async SMTP with daily/weekly digest subscriptions |
| Observability | OpenTelemetry traces (Jaeger), Prometheus metrics, Grafana dashboards, deep health checks |
| Feature flags | Per-project / per-role / rollout-percent gates with audit history |
| Global search | Multi-entity keyword search across runs, tests, suites, defects |

### Experimental (flag-off by default)

| Feature | Flag key | Description |
|---------|----------|-------------|
| Deep investigation | -- | Multi-agent LangGraph pipeline (semantic clustering, distributed traces, log anomaly, API contract validation) |
| RAG test generation | `knowledge_rag` | Knowledge-grounded test case generation from Jira, Confluence, URLs, documents |
| RAG faithfulness | `rag_faithfulness` | Pluggable Ollama/Ragas evaluator gates auto-accept on citation quality |
| LLM cost budget | `llm_cost_budget` | Per-project quota with auto-downgrade to ML/rules when budget is exhausted |
| GitHub Checks | `github_checks` | Post a check run to the commit SHA on every ingested run |
| Outbound webhooks | `outbound_webhooks` | HMAC-signed event fan-out with retry + DLQ + replay |
| Compliance pack | `release_compliance_pack` | One-click audit ZIP for a release decision (SOX/HIPAA/SOC 2) |
| Weekly retro digest | `weekly_retro_digest` | Monday-morning automated retrospective per project |
| Team value metrics | -- | Ownership-rule-aware team attribution |
| Continuous fine-tuning | -- | Self-improving models trained on verified failure data |
| Semantic search | -- | ChromaDB-backed hybrid keyword + vector search |

### Enterprise (future)

| Feature | Notes |
|---------|-------|
| SSO / SAML / SCIM | Enterprise identity federation |
| Cloud LLM routing | OpenAI / Gemini with cost controls (requires `AI_OFFLINE_MODE=false`) |
| Multi-cloud K8s overlays | Staging / production Kustomize overlays |
| Project-scoped API keys | Admin-only creation for CI service accounts |
| Report share links | Public token-authenticated PDF/evidence downloads |

See `docs/features/FEATURE_FLAG_INVENTORY.md` for the full flag inventory with defaults, owners, and graduation criteria.

## Architecture

```
React SPA (port 3000)  -->  FastAPI backend (port 8000)  -->  PostgreSQL + MongoDB + Redis + MinIO
                                    |
                              Celery workers  -->  Ollama (optional) + ChromaDB (optional)
                                    |
                              MCP server (port 8002)
```

For the full architecture diagram, component descriptions, and deployment matrix, see [ARCHITECTURE.md](ARCHITECTURE.md).

## Documentation

| Document | Description |
|----------|-------------|
| [ARCHITECTURE.md](ARCHITECTURE.md) | System architecture, component diagram, deployment matrix |
| [CONTRIBUTING.md](CONTRIBUTING.md) | Development setup, code style, PR process, DCO |
| [ROADMAP.md](ROADMAP.md) | What's in progress, planned, and on hold |
| [SECURITY.md](SECURITY.md) | Vulnerability reporting and disclosure policy |
| [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) | Contributor Covenant v2.1 |
| [GETTING_STARTED.md](GETTING_STARTED.md) | Step-by-step walkthrough: clone to first failure clustered in under 15 minutes |
| [THREAT_MODEL.md](THREAT_MODEL.md) | Data flow, offline guarantees, auth boundaries, PII redaction scope |
| [benchmarks/](benchmarks/) | Classification accuracy + throughput benchmarks with methodology (`make benchmark`) |
| [README_FULL.md](README_FULL.md) | Full feature documentation (SDK setup, ingestion options, MCP config, CLI reference, etc.) |

## License

Apache 2.0 -- see [LICENSE](LICENSE).
