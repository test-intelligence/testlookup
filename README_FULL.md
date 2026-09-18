# Full product and engineering documentation

The full walkthrough now lives in the modular [documentation suite](docs/README.md). This entrypoint is retained so existing links continue to work without maintaining a second feature matrix that can drift from the code.

| Need | Guide |
|---|---|
| Product, features and personas | [Product overview](docs/product/overview.md) |
| End-to-end user journeys | [Workflows](docs/product/workflows.md) |
| Behaviors, states and errors | [Behavior matrix](docs/product/behavior.md) |
| Architecture and dependencies | [Overview](docs/architecture/overview.md), [codebase map](docs/architecture/codebase-map.md) |
| Framework ingestion | [Ingestion pipeline](docs/pipelines/ingestion.md) |
| AI and review | [AI pipeline](docs/pipelines/ai.md), [reporting](docs/pipelines/reporting.md) |
| API and all schemas | [API guide](docs/api/README.md), [endpoint index](docs/reference/api-index.md) |
| SDK, CLI, MCP and connectors | [Integrations](docs/architecture/integrations.md) |
| Local and production setup | [Development](docs/operations/development.md), [deployment](docs/operations/deployment.md) |
| Testing and handoff | [Testing](docs/operations/testing.md), [developer handoff](docs/handoff/README.md) |

Feature availability depends on configuration, project policy, credentials, model resources and feature flags. SSO/SCIM, project-scoped API keys and report sharing are implemented in the documented source snapshot. Review enforcement and offline egress have distinct defaults and boundaries; see [security](docs/architecture/security.md). Consult [verification](docs/handoff/verification.md) for what was checked rather than treating this page as a production readiness certificate.
