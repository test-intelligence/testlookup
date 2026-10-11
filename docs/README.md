# TestLookup documentation

Start with the [README](../README.md) for what TestLookup is, and [GETTING_STARTED.md](../GETTING_STARTED.md) for a first run on your machine. This page indexes the rest by audience. Where a narrative page and the code disagree, the code and migrations win; please report the difference.

## Users: QA engineers, leads and developers

| Goal | Read |
|---|---|
| Learn the product | The in-app guide at `/docs` on your instance (source: `frontend/src/content/guide/`), [product overview](product/overview.md), [user journeys](product/workflows.md), [behavior and states](product/behavior.md) |
| Get results in | [Getting results in](../user-guide/getting-results-in.md), [ingestion pipeline](pipelines/ingestion.md) |
| Triage failures and flaky tests | [Triaging failures](../user-guide/triaging-failures.md), [flaky tests](../user-guide/flaky-tests.md), [working with runs](../user-guide/working-with-runs.md) |
| Decide releases | [Release gates](../user-guide/release-gates.md), [reporting pipeline](pipelines/reporting.md) |
| Use AI features | [AI features](../user-guide/ai-features.md), [AI pipeline](pipelines/ai.md) |
| Automate from CI, scripts or AI assistants | [CLI, SDKs and MCP](../user-guide/cli-sdk-mcp.md), [API guide](api/README.md), [all endpoints](reference/api-index.md) |

## Operators: install, configure and run it

| Goal | Read |
|---|---|
| Choose a deployment mode | [Deployment guide](operations/deployment.md): mode matrix, secrets, TLS, backups, upgrade order |
| Docker Compose (demo, dev, release images, air-gapped, monitoring) | [Compose](operations/deploy-compose.md) |
| One VM, or Cloud Run | [Single VM](operations/deploy-single-vm.md) |
| Kubernetes | [Kubernetes](operations/deploy-kubernetes.md), [overlay map](../k8s/README.md), [single-node K3s](operations/deploy-k3s.md) |
| OpenShift | [OpenShift and air-gapped OpenShift](operations/deploy-openshift.md) |
| EKS, GKE, AKS | [Managed clouds](operations/deploy-cloud.md) |
| No internet at the site | [Air-gapped install](../user-guide/air-gapped-install.md), [offline model pack](../user-guide/offline-model-pack.md) |
| Size the hardware | [Sizing](../user-guide/sizing.md), [database connection budget](operations/database-connection-budget.md) |
| Configure settings | [Configuration reference](reference/configuration.md) (generated from the settings class), [worker operations](reference/worker-operations.md) |
| Administer, back up, upgrade | [Operator guide](../user-guide/README.md), [administration](../user-guide/administration.md), [UPGRADING.md](../UPGRADING.md) |
| Troubleshoot | [Troubleshooting](../user-guide/troubleshooting.md), [known limitations](handoff/limitations.md) |
| Secure it | [Security architecture](architecture/security.md), [SECURITY.md](../SECURITY.md), [threat model](../THREAT_MODEL.md) |

## Contributors: understand and change the code

| Goal | Read |
|---|---|
| Set up and follow the process | [CONTRIBUTING.md](../CONTRIBUTING.md), [local development](operations/development.md), [testing](operations/testing.md) |
| Understand the system | [ARCHITECTURE.md](../ARCHITECTURE.md), [architecture overview](architecture/overview.md), [codebase map](architecture/codebase-map.md), [design decisions](architecture/design-decisions.md) |
| Persistence | [Data architecture](architecture/data-model.md), [all tables](reference/data-dictionary.md), [Python contracts](reference/python-contracts.md), [migrations](reference/migrations.md) |
| Follow one result through the system | [Ingestion](pipelines/ingestion.md), [test runs](pipelines/test-runs.md), [AI](pipelines/ai.md), [reporting](pipelines/reporting.md) |
| Integrations and clients | [Integration map](architecture/integrations.md), [client surfaces](reference/client-surfaces.md), [schemas](reference/schemas.md), [API contract gaps](reference/api-contract-gaps.md) |
| Subsystem deep dives | [architecture/](../architecture/README.md): database schema, ingestion at scale, release gate, flaky intelligence, AI quality, observability, frontend |
| Maintain these docs | [Documentation maintenance](handoff/maintenance.md), [verification scope](handoff/verification.md), [glossary](handoff/glossary.md), [maintainer checklist](handoff/README.md) |

## How this documentation is organized

```text
README.md, GETTING_STARTED.md, ARCHITECTURE.md   entry points (repository root)
docs/
  product/       overview, journeys, behavior and error expectations
  architecture/  system, module, data and integration maps, design decisions, security
  pipelines/     ingestion, test-run, AI and reporting flows
  api/           authentication, errors, examples, REST and streaming usage
  operations/    deployment modes, configuration, local development, testing
  handoff/       maintainer checklist, limitations, glossary, documentation maintenance
  reference/     generated: OpenAPI, endpoint index, schemas, tables, settings, inventory
user-guide/      operator and administrator guide
architecture/    engineering deep dives
frontend/src/content/guide/   the end-user guide served in the app at /docs
```

Everything under `reference/` is generated from tracked source by `scripts/generate_handoff_reference.py`: every OpenAPI operation and schema, every SQLAlchemy table, settings with their defaults, migrations, MCP registrations, CLI commands and frontend routes. CI checks that it is current (`--check`) and that every local link and heading in these pages resolves (`scripts/check_handoff_docs.py`). Do not edit generated pages by hand; see [documentation maintenance](handoff/maintenance.md).
