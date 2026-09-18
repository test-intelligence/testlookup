# TestLookup architecture

The current [architecture overview](docs/architecture/overview.md) describes the API/worker processes, stores, live streaming, agent runtime and reliability boundaries at the documented main baseline. Start there for system and component diagrams.

| Topic | Reference |
|---|---|
| System and component architecture | [Architecture overview](docs/architecture/overview.md) |
| Repository/module/dependency map | [Codebase map](docs/architecture/codebase-map.md) |
| Relational and document data | [Data architecture](docs/architecture/data-model.md), [complete dictionary](docs/reference/data-dictionary.md) |
| Ingestion and live data flow | [Ingestion](docs/pipelines/ingestion.md), [test-run lifecycle](docs/pipelines/test-runs.md) |
| AI orchestration and retries | [AI pipeline](docs/pipelines/ai.md) |
| Reports, summaries and release policy | [Reporting](docs/pipelines/reporting.md) |
| Design rationale and alternatives | [Trade-offs](docs/architecture/design-decisions.md) |
| Authentication, review and egress | [Security](docs/architecture/security.md) |
| SDK/CLI/MCP and external connectors | [Integrations](docs/architecture/integrations.md) |
| Compose, Kubernetes, homelab and air-gap | [Deployment](docs/operations/deployment.md) |

The implementation uses React 19/TypeScript, FastAPI/Pydantic/SQLAlchemy, Celery/Redis, PostgreSQL, MongoDB and object storage, with optional model/vector services. The generated [client inventory](docs/reference/client-surfaces.md) tracks MCP/CLI registrations; the [configuration reference](docs/reference/configuration.md) tracks source defaults. Model latency and deployment capacity require measurements on the target workload.

Existing [architecture deep dives](architecture/README.md) remain useful for specialized topics and historical decisions. Their dated observations must be checked against current code and the [handoff verification scope](docs/handoff/verification.md).
