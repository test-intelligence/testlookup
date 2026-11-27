# Observability documentation backlog

This backlog tracks publication of the observability and release assurance work merged into TestLookup `main` at `051936fbdaf0d8c036c84e9a36d7e281dd6f4423`. Each story has a repository owner and an acceptance check. The implementation ledger records engineering verification and remaining deployment acceptance. [Implementation ledger](observability-delivery.md) · [Merged pull request](https://github.com/test-intelligence/testlookup/pull/497).

## Editorial approach

Give evaluators a capability summary, users a task-oriented guide, and operators a deployment and verification procedure. Use the existing architecture explanations, configuration reference, and component runbooks as the sources for individual details. [Diátaxis](https://diataxis.fr/) · [Google procedure guidance](https://developers.google.com/style/procedures).

Review documentation in Git with exact source revisions, a check for each story, and separate product and website deliveries. Use descriptive links, stable headings, accessible layouts, and command examples with prerequisites and a verifiable outcome. [Write the Docs](https://www.writethedocs.org/guide/docs-as-code/) · [Google accessibility guidance](https://developers.google.com/style/accessibility).

Use the signal names metrics, logs, and traces as documented upstream. Explain alert evaluation, delivery, and independent notification-path checks in terms of what an operator can observe. [OpenTelemetry signals](https://opentelemetry.io/docs/concepts/signals/) · [Prometheus alerting practices](https://prometheus.io/docs/practices/alerting/).

## Epics and stories

| Epic | Outcome |
| --- | --- |
| DOC-E01 · Product capabilities and architecture | Readers can find what shipped in the merged source and understand how signals and operational evidence move through TestLookup. |
| DOC-E02 · Deployment, operation, and evidence | Operators can choose a profile, verify its behavior, and respond to failure using the existing runbooks. |
| DOC-E03 · Website discovery and versioned documentation | Evaluators can find observability from the product site and reach guidance tied to an exact source snapshot. |
| DOC-E04 · Independent review and delivery | Claims, commands, links, and deployed behavior have recorded evidence for both repositories. |

| Story | Owner | Implementation scope | Acceptance | Status |
| --- | --- | --- | --- | --- |
| DOC-E01-S01 · Establish the version baseline | Product docs | PR #497 and all eight OBS epics | Record the merged revision, published release boundary, and remaining site-specific acceptance. | Verified |
| DOC-E01-S02 · Find the right guide | Product docs | OBS-E01–E08 | README, documentation home, and user guide lead to a single observability entry point. | Verified |
| DOC-E01-S03 · Explain the signal paths | Product docs | OBS-E01, E02, E04, E05, E08 | Architecture describes request, worker, MCP, monitoring, and durable evidence paths using current contracts. | Verified |
| DOC-E02-S01 · Choose and enable a profile | Product docs | OBS-E02, E03, E04, E06 | Compose, Kubernetes, K3s, OpenShift, managed, and offline guides explain prerequisites and link to the adoption checks. | Verified |
| DOC-E02-S02 · Verify operation and triage | Product docs | OBS-E01, E02, E03, E04, E06, E08 | Operators can verify readiness and signal freshness, link logs and traces, and follow alert and upload runbooks. | Verified |
| DOC-E02-S03 · Document custody and proof | Product docs | OBS-E05, E07, E08 | Explain upload recovery, pipeline event replay, identity retention, archive custody, and independent release gates with scope and limits. | Verified |
| DOC-E03-S01 · Explain the capability on the website | Website | OBS-E01–E08 | Product and self-hosting pages describe supported signals, profiles and operational outcomes and link to the versioned guide. | In progress |
| DOC-E03-S02 · Publish versioned technical guidance | Website | OBS-E01–E08 | Website docs expose discoverable observability guidance with source provenance and the intended release scope. | In progress |
| DOC-E03-S03 · Verify generated and rendered output | Website | Website integration | Run applicable content, build, links, search, and browser checks for the new guidance and navigation. | In progress |
| DOC-E04-S01 · Review claims against code and behavior | Independent review | OBS-E01–E08 | Review each changed surface, then trace links, commands, metrics, trust boundaries, and release status across repositories. | In progress |
| DOC-E04-S02 · Record verification and ship both branches | Repository maintainer | Product and website delivery | Record test evidence and limits, sign off and push both reviewed branches, and confirm the remote commit IDs. | In progress |

## Implementation coverage

The [product observability guide](../../user-guide/observability.md) connects each epic to the relevant task or reference. The [production observability checklist](../operations/observability-checklist.md) connects the deployment, verification, and response steps.

| Implementation epic | Documentation and verification anchors |
| --- | --- |
| OBS-E01 · Correlated telemetry | [Worker tracing](../operations/worker-tracing.md), [Collector](../operations/telemetry-collector.md), [architecture](../../architecture/OBSERVABILITY.md) |
| OBS-E02 · Metric collection | [Queue monitoring](../operations/celery-queue-monitoring.md), [dependency metrics](../operations/dependency-metrics.md), [SQLAlchemy pools](../operations/db-pool-metrics.md), [infrastructure exporters](../operations/infrastructure-exporters.md) |
| OBS-E03 · Alerting and schedules | [Expected telemetry](../operations/telemetry-contract.md), [scheduled maintenance](../operations/scheduled-maintenance.md), [digest delivery](../operations/weekly-digest-observability.md), [routing and watchdog](../operations/alert-routing.md) |
| OBS-E04 · Durable logs and traces | [Durable telemetry profiles](../operations/durable-telemetry.md), [log storage](../operations/log-storage.md), [Jaeger storage](../operations/persistent-jaeger.md), [offline install](../../user-guide/air-gapped-install.md) |
| OBS-E05 · Operational and audit evidence | [Pipeline event delivery](../operations/pipeline-event-delivery.md), [archive custody](../operations/audit-archive-custody.md), [identity-event retention](../operations/identity-event-retention.md) |
| OBS-E06 · Objectives and capacity | [Journey indicators](../operations/journey-slis.md), [API objectives](../operations/api-objectives.md), [metric cardinality](../operations/metric-cardinality.md), [telemetry capacity](../operations/telemetry-capacity.md) |
| OBS-E07 · DevSecOps and rollout | [Release assurance](../operations/release-assurance.md), [infrastructure policy](../operations/infrastructure-policy.md), [disposable signal proof](../operations/disposable-signal-proof.md), [security control acceptance](../operations/security-control-acceptance.md) |
| OBS-E08 · Ingestion and worker lifecycle | [Client upload recovery](../operations/upload-client-recovery.md), [dispatch recovery](../operations/upload-dispatch-recovery.md), [worker readiness](../operations/worker-readiness.md) |

## Findings by surface

| Severity | Surface | Finding and correction | Evidence |
| --- | --- | --- | --- |
| P1 | Release and website versioning | The published `v0.1.0` release predates these features. Describe the new capabilities against the merged source revision and preserve the site's `next` scope. | [Published release](https://github.com/test-intelligence/testlookup/releases/tag/v0.1.0) · [Merged source](https://github.com/test-intelligence/testlookup/tree/051936fbdaf0d8c036c84e9a36d7e281dd6f4423). |
| P1 | Health verification | A frontend fallback can answer HTTP 200 with HTML. The checklist verifies the API's JSON readiness and build fields. | [Health router](../../backend/app/routers/health.py#L267) · [Production checks](../operations/observability-checklist.md#verify-the-deployed-signals). |
| P1 | In-app architecture and upload help | The older architecture misstated metrics access and queue coverage. The updated help describes private scraping, the child lane, and the existing **Check status** flow. | [Architecture help](../../frontend/src/content/guide/architecture.md) · [Upload help](../../frontend/src/content/guide/ingestion.md). |
| P2 | Product and operator navigation | The old entry points gave readers few ways to find the merged monitoring contracts. The product guide now leads to deployment selection, triage and specialist runbooks. | [Product guide](../../user-guide/observability.md) · [Deployment guide](../operations/deployment.md#observability-and-troubleshooting). |
| P2 | Correlation and privacy | Earlier descriptions omitted the durable signal paths or described an installation as having no telemetry. The architecture and privacy pages now describe opt-in export, sanitization, and site ownership. | [Signal architecture](../../architecture/OBSERVABILITY.md) · [Data and privacy](../../DATA_AND_PRIVACY.md). |
| P2 | Reporting and ingestion reference | The earlier website pin described a fixed pass-rate basis and Redis-only upload status. The source review verified that the basis follows measured totals and that new uploads use durable receipts. | [Reporting behavior](../pipelines/reporting.md#aggregation-and-evidence) · [Ingestion behavior](../pipelines/ingestion.md#failure-status-and-observability). |

## Integration review and verification

Keep the implementation evidence from the final PR CI run alongside documentation checks and any newly repeated runtime checks. Record which revision and deploy profile each check covered. Site gateway admission, data retention under load, disaster recovery, and external receiver response require the site owner's validation. [Final PR CI run](https://github.com/test-intelligence/testlookup/actions/runs/46043399242) · [Environment acceptance](README.md#environment-acceptance-checklist).

Independent verification traced the merged application routes and UI labels to the guides, the Compose and Kustomize profiles to their prerequisites, and release behavior to immutable image evidence. The public demo responded to a read-only application endpoint. Its health route returned an HTML fallback, so the site's deployed revision could not be confirmed. The local evidence applies to the merged source and the disposable checks below.

| Evidence | Result |
| --- | --- |
| Backend behavior | 190 focused tests passed for correlation, lifecycle, queue freshness, event delivery, scheduling, recovery, and identity retention. |
| Merged source on GitHub | Backend Test & Lint, PostgreSQL/Mongo integration, Frontend Test & Lint, and exact-SHA secret-scan authorization passed for `051936fb`. [Main CI run](https://github.com/test-intelligence/testlookup/actions/runs/46049516946). |
| Reference deployment rendering | Source and release Compose profiles rendered. Three Kubernetes telemetry overlays rendered and preserved applicable image, service-account, and proxy contracts. |
| Native monitoring | 49 alert routing and exporter checks passed with pinned Prometheus and Alertmanager. All 15 Prometheus rule fixtures passed. |
| Native signal storage | 10 Collector, Loki, and Jaeger tests passed, including correlated log and trace readback and queue persistence. Static Grafana provisioning checks passed. The optional native Grafana query test needed a pinned runtime outside this check. |
| MCP | 23 tests passed, including a native stdio subprocess and sanitized telemetry checks. |
| Report and API reference | 61 reporting and release decision tests passed. The website validator passed 113 checks, including exact agreement between OpenAPI and the 233 contract gaps in the generated domain pages. |
| Product and in-app documentation | Generated references matched. The local guide checks passed, including 77 frontend tests and all 53 Mermaid blocks. |
| Website source custody | Independent checks matched all 149 pinned file digests to the merged Git objects. The source audit found 35 added API operations and no operation removals. |
