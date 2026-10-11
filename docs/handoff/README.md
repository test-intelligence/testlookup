# Maintainer guide

[Documentation home](../README.md)

This section is for people who maintain TestLookup or take over an area of it: an onboarding order, an acceptance checklist for a new environment, where changes belong, and how the documentation itself is kept correct. Contribution mechanics (branches, tests, DCO sign-off, review) are in [CONTRIBUTING.md](../../CONTRIBUTING.md).

## Onboarding order

1. Read the [product overview](../product/overview.md), [user journeys](../product/workflows.md) and [state behavior](../product/behavior.md).
2. Read the [architecture overview](../architecture/overview.md), [module map](../architecture/codebase-map.md), [data model](../architecture/data-model.md) and [design trade-offs](../architecture/design-decisions.md).
3. Trace [ingestion](../pipelines/ingestion.md) → [test runs](../pipelines/test-runs.md) → [AI](../pipelines/ai.md) → [reports](../pipelines/reporting.md).
4. Set up an isolated environment with [local development](../operations/development.md); read [security](../architecture/security.md) and the [API examples](../api/README.md) before using real data.
5. Run the relevant [tests](../operations/testing.md), then use the [deployment guide](../operations/deployment.md) for a disposable end-to-end smoke flow.

## Environment acceptance checklist

Use this when you bring up a new environment or take ownership of an existing one.

| Check | Area | Evidence to record |
|---|---|---|
| Reproduce startup and login/MFA | Platform + frontend | Environment, dependency versions, `/health/version` and the auth outcome |
| Submit and observe one passing and one failing report | Backend + QA | Request, accepted IDs, stored rows and status, parsing outcome |
| Exercise duplicate delivery and denied cross-project access | Backend + QA | Stable identity, explicit refusal and no unauthorized writes |
| Follow a pipeline through completion and review | AI + QA lead | Frozen config, stages, provenance and the accepted or rejected state |
| Export or share under the intended review policy | Reports + QA | Approved, draft and refused outputs, and the audit entries |
| Verify release evidence and its denominator | Release + product | Runs, phase, policy snapshot, neutral cases and decision history |
| Confirm every queue has a consumer, the migration head and a backup restore | Platform | Queue consumption, schema version and a restore drill |
| Regenerate contracts and validate the documentation | Maintainer | Clean reference check, valid links, schemas and diagrams |

## Where to make changes

API behavior belongs in scoped handlers and services, with schema and client updates. State transitions belong in the corresponding state service. AI tools need capability, config, policy and evaluation updates. Durable schema changes need migrations. Deployment changes need topology, image-manifest and connection-budget checks. Do not patch only a UI label or a response serializer when the persisted authority is wrong.

The [source inventory](../reference/source-inventory.csv), [endpoint index](../reference/api-index.md), [data dictionary](../reference/data-dictionary.md) and [configuration reference](../reference/configuration.md) are lookup tools while implementing. See also [known limitations](limitations.md), the [glossary](glossary.md), [documentation maintenance](maintenance.md) and the [verification scope](verification.md) of the generated reference.
