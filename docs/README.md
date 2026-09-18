# TestLookup documentation

This is the source-aligned developer handoff for `main` commit `44be1f2023d50bbbd9554bc91ce668c5c0789db5`, inspected on 2026-09-18. Start with the [handoff checklist](handoff/README.md). The runtime code and migrations take precedence over older design proposals or historical deployment evidence.

## Reading paths

| Goal | Read |
|---|---|
| Understand users and outcomes | [Product](product/overview.md), [journeys](product/workflows.md), [behavior matrix](product/behavior.md) |
| Change a feature safely | [Architecture](architecture/overview.md), [module map](architecture/codebase-map.md), [design trade-offs](architecture/design-decisions.md) |
| Build a client | [API](api/README.md), [endpoint index](reference/api-index.md), [schemas](reference/schemas.md), [integrations](architecture/integrations.md) |
| Understand persistence | [Data architecture](architecture/data-model.md), [all tables](reference/data-dictionary.md), [Python contracts](reference/python-contracts.md), [migrations](reference/migrations.md) |
| Follow one result through the system | [Ingestion](pipelines/ingestion.md), [test runs](pipelines/test-runs.md), [AI](pipelines/ai.md), [reporting](pipelines/reporting.md) |
| Run and support it | [Development](operations/development.md), [deployment](operations/deployment.md), [testing](operations/testing.md), [security](architecture/security.md), [troubleshooting](handoff/limitations.md) |
| Maintain or publish docs | [Maintenance](handoff/maintenance.md), [wiki](wiki/README.md), [verification](handoff/verification.md), [glossary](handoff/glossary.md) |

## Directory structure

```text
docs/
  README.md
  product/           overview, journeys, behavior and error expectations
  architecture/      system, module/data/integration maps, design decisions, security
  pipelines/         ingestion, test-run, AI and reporting flows
  api/               auth, errors, examples, REST and streaming usage
  operations/        local setup, deployment, testing and observability
  handoff/           onboarding checklist, limits, glossary, maintenance, verification
  reference/         generated API domain pages, OpenAPI, schemas, models and inventory
  reviews/           dated per-file and cross-file documentation audit
  wiki/              wiki home and reproducible export instructions
```

Existing `docs/DevOps`, `docs/operations`, `architecture`, `user-guide`, `UserGuides`, and dated QA/research materials are preserved. The new `docs/operations` pages complement existing runbooks. For engineering deep dives, see [architecture/README](../architecture/README.md); for historical proposals, check their dates and implementation evidence before treating them as current behavior.

## Evidence and completeness

The generated reference enumerates every OpenAPI operation and schema, all registered SQLAlchemy tables and their fields/constraints, application Python field declarations, settings, migrations, MCP registrations, CLI commands and frontend routes. The [source inventory](reference/source-inventory.csv) records source/configuration/test files, imports and symbols. This is structural coverage of the repository; it is not a claim of an exhaustive security audit of every code path.

Narrative pages trace the principal workflows through routes, services, stores, workers and clients. [API contract gaps](reference/api-contract-gaps.md) explicitly list responses without a complete structured schema; their handler links remain the authority for fields not expressed in OpenAPI. [Verification](handoff/verification.md) records the actual validation performed. The independent [documentation review](reviews/2026-09-18-documentation-review-summary.md) records corrections and confirmed application gaps with source evidence.
