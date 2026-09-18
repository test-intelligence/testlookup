# Developer handoff package

[Documentation home](../README.md)

Baseline: `44be1f2023d50bbbd9554bc91ce668c5c0789db5` from GitHub `main`, inspected 2026-09-18. Documentation branch: `codex/documentation-handoff`. Source, generated references and validation limits are recorded in [verification](verification.md).

## Onboarding order

1. Read [product overview](../product/overview.md), [user journeys](../product/workflows.md) and [state behavior](../product/behavior.md).
2. Read [architecture](../architecture/overview.md), [module map](../architecture/codebase-map.md), [data model](../architecture/data-model.md) and [design trade-offs](../architecture/design-decisions.md).
3. Trace [ingestion](../pipelines/ingestion.md) → [test runs](../pipelines/test-runs.md) → [AI](../pipelines/ai.md) → [reports](../pipelines/reporting.md).
4. Set up an isolated environment using [local development](../operations/development.md); inspect [security](../architecture/security.md) and [API examples](../api/README.md) before using real data.
5. Run appropriate [tests](../operations/testing.md), then follow [deployment](../operations/deployment.md) for a disposable end-to-end smoke flow.

## Handoff acceptance checklist

| Check | Owner area | Evidence to record |
|---|---|---|
| Reproduce local startup and login/MFA | Platform + frontend | Environment, dependency versions, health/version and auth outcome |
| Submit and observe one passing and one failing report | Backend + QA | Request, accepted IDs, durable rows/status and parsing outcome |
| Exercise duplicate delivery and denied cross-project access | Backend + QA | Stable identity, explicit refusal and no unauthorized writes |
| Follow a pipeline through completion/review | AI + QA lead | Frozen config, stages, provenance and accepted/rejected state |
| Export/share under the intended review policy | Reports + QA | Approved/draft/refused outputs and audit |
| Verify release evidence and denominator | Release + product | Runs/phase/policy snapshot, neutral cases and decision history |
| Confirm all queues, migration head and backup restore | Platform | Queue consumption, schema version and restore drill |
| Regenerate contracts and validate documentation | Maintainer | Clean reference check, valid links/schema/diagrams |

These are operational acceptance tasks for the receiving team; this documentation task did not perform them against a live deployment.

## Where to make changes

API behavior belongs in scoped handlers/services with schema and client updates. State transitions belong in the corresponding state service. AI tools need capability/config/policy/evaluation updates. Durable schema changes need migrations. Deployment changes need topology/image/connection-budget checks. Do not patch only a UI label or response serializer when the persisted authority is wrong.

The [source inventory](../reference/source-inventory.csv), [all API endpoints](../reference/api-index.md), [complete data dictionary](../reference/data-dictionary.md) and [configuration reference](../reference/configuration.md) are lookup tools during implementation. See [known limitations](limitations.md), [glossary](glossary.md), [maintenance](maintenance.md) and the [review action register](../reviews/2026-09-18-action-register.md) for follow-up work.
