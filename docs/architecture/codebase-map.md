# Codebase, component and dependency map

[Documentation home](../README.md)

The machine-readable [source inventory](../reference/source-inventory.csv) records every tracked source/configuration/test file in supported source formats, line counts, Python module purpose, imports and top-level symbols. It includes legacy and test material; an entry is not proof that a module is on a live request path. [Reference counts](../reference/inventory-counts.json).

## Repository map

| Directory/files | Purpose and reading order |
|---|---|
| `backend/app/main.py`, `bootstrap.py` | Startup, middleware, router registration and health/telemetry wiring |
| `backend/app/core` | Settings, authentication, role/resource guards, release predicates, security, logging and metrics |
| `backend/app/routers` | HTTP/WebSocket contracts; start with the API index for a feature's mounted paths |
| `backend/app/models` | ORM, public schemas, enums, AI input/output and evidence contracts, serializers |
| `backend/app/db` | SQL/Mongo/Redis clients, object storage and capacity gates |
| `backend/app/services` | Domain orchestration; suffixes such as `_service` do not imply independent deployments |
| `backend/app/agents`, `tools` | Graph nodes, Investigator/Fixer logic, evidence and tool adapters |
| `backend/app/streams` | Live consumer, fan-out and streaming protocol support |
| `backend/app/worker` | Celery task entrypoints, schedules, queue routing, async-loop lifecycle, training tasks |
| `backend/migrations` | Alembic evolution and data backfills; authoritative for database upgrades |
| `backend/models`, `backend/scripts` | Model artifacts/configuration and backend maintenance utilities |
| `backend/tests` | Unit, architectural, regression and integration tests |
| `frontend/src/pages`, `components` | Routed feature screens and reusable UI |
| `frontend/src/services`, `hooks`, `store`, `types`, `utils`, `config` | Transport/contracts, polling/state, permissions, shared presentation behavior |
| `frontend/tests` | Browser workflows and exploratory probes; inspect config for live vs mock target |
| `cli/testlookup_cli` | Typer command groups, profiles, transport and output |
| `client` | Python reporter, JS/Java/Go integrations, CI context and examples |
| `mcp` | FastMCP adapter, API client, auth verifier, tools/resources/prompts and tests |
| `scripts` | Setup, seed, release/backup operations, quality ratchets and documentation generators |
| `docker-compose*.yml`, `Makefile`, `install.sh` | Local/release/VM/air-gap startup and common operator tasks |
| `k8s`, `homelabsetup`, `openshiftsetup` | Kubernetes base/overlays, K3s and OpenShift deployment workflows |
| `infra`, `deploy` | Cloud/monitoring assets, image manifest and cutover runbooks |
| `.github/workflows`, `Jenkinsfile`, `jenkins` | CI, release, infrastructure and deployment automation |
| `benchmarks`, `samples`, `postman` | Performance/evaluation fixtures, report examples and API collections |
| `architecture`, `docs`, `user-guide`, `UserGuides` | Architecture deep dives, handoff, operator and end-user documentation |
| `qa`, `research`, `design_handoff_ui_improvements` | Dated verification evidence, proposals and UI design artifacts |

## Domain/service map

| Domain | Main services/entrypoints | State/consumers |
|---|---|---|
| Ingestion | `ingestion`, `ingestion_pipeline`, format parsers, `safe_archive`, `upload_status` | TestRun/TestCase/history, Mongo evidence, upload Redis records |
| Live | `stream_service`, `ws_event_ingest`, `live_session_drainer`, `live_run_recovery_service` | Redis stream/session → persistent run → UI fan-out |
| Intelligence | `analysis_router`, `rules_engine`, `ml`, `training`, `run_intelligence_service` | AIAnalysis, summaries, pipeline provenance |
| Agent governance | `agent_catalog`, `agent_config_resolver`, `agent_planner`, `workflow_definition_service`, `action_policy` | Config versions, invocation/pipeline/stage/action ledgers |
| Reliability | `pipeline_lease`, `retry_policy`, `pipeline_retry_config`, `run_downstream_outbox`, tombstones | Durable status, retries, deduplication and reconciliation |
| Evidence/reports | `evidence_*`, `decision_report_*`, `report_composition_service`, `summary_report_service` | Immutable evidence references, report attempts, HTML/PDF/share/export |
| Reviews | `review_request_service`, `review_envelope`, `review_supervisor`, `report_distribution_policy` | Review identity, accepted/rejected/superseded state, distribution audit |
| Release | `release_*`, `policy_evaluator_service`, `policy_resolution`, `run_diff_service` | Associations, attribution, phases, rollups, append-only decisions |
| Test management | `test_management_*`, `test_case_lifecycle_service`, `suite_*`, `duplicate_detection_service` | Managed cases, plans, strategies, canonical identity and reviews |
| Flaky/performance | `flaky_*`, `quarantine_*`, `perf_regression_service`, `performance_budgets` | Baselines, quarantine state, signals and coaching |
| Identity/settings | `mfa_service`, `sso_service`, `scim_service`, `refresh_token_service`, `feature_flags`, `secret_service` | Users, tokens, membership, project policies, audit |
| External integrations | `connectors`, `jira_client`, GitHub/GitLab services, `notification`, `webhook_service` | Remote APIs, attempts/delivery audit and retry |
| RAG/search | `knowledge_*`, `rag_*`, `search_*`, `semantic_search` | Source documents/chunks, vector index, generated drafts and provenance |
| Administration | `retention_service`, `run_deletion_service`, `deletion_job_service`, `storage_accounting_service` | Purge policy, tombstones, storage lifecycle and operational reports |

## Dependency directions

HTTP handlers depend on core guards, schemas, ORM and services. Services use storage clients and other domain services; graph nodes invoke those services/tools through runtime policies. Celery tasks invoke the same shared code. Frontend/CLI/MCP use REST contracts; SDKs also produce live/batch events. Database migrations depend on schema history, not API consumers.

Runtime dependencies are pinned/listed in [backend requirements](../../backend/requirements.txt), [frontend package/lock](../../frontend/package.json), [CLI package](../../cli/pyproject.toml), [client package](../../client/pyproject.toml) and [MCP requirements](../../mcp/requirements.txt). The frontend includes React Router, Axios, SWR, Zustand, Tailwind/Radix, charts and Markdown rendering. Backend services use FastAPI/Pydantic/SQLAlchemy, Celery/Redis, Motor, object-storage clients, LangChain/LangGraph, optional Chroma and export libraries. Read the files rather than copying version claims from old badges.

For a change: locate the route → input schema → service → model/migration → worker or external side effect → serializer → frontend/client consumer → behavioral tests. The [integration audit](../reviews/2026-09-18-integration-checks.md) follows this method for the principal flows.
