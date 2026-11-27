# Monitor a TestLookup installation

Use this guide to check a self-hosted installation, choose a monitoring profile, and follow an incident from an application symptom to the relevant signal.

This guide describes the implementation on the repository's `main` branch. Use the documentation and manifests for the image revision you run. The new profiles and release gates require a build that contains them, even when an earlier published image has the same version number. Check the serving revision with `/health/version`.

## Check the running application

You need network access to the backend's health endpoints. For a local Compose install with the API port published on loopback, run these commands in a shell from the host. For a shared installation, use your approved HTTPS route or an authorized tunnel to the backend.

```sh
api_url=http://127.0.0.1:8000
curl --silent --show-error --fail "$api_url/health/live"
curl --silent --show-error --fail "$api_url/health/version"
curl --silent --show-error --fail "$api_url/health/ready"
```

Verify each response contains a JSON object. A frontend may return an HTML page with HTTP 200 when the health path is unmapped. Use the backend route in that case.

| Endpoint | What to verify |
|---|---|
| `/health/live` | The API process responds. |
| `/health/version` | Compare the reported build revision with the image or rollout you intended to run. Source builds can report an unknown revision when build metadata was omitted. |
| `/health/ready` | The API can reach its critical PostgreSQL, MongoDB, and Valkey or Redis dependencies. HTTP 503 signals failed readiness. |
| `/health/details` | Use a signed-in session or an accepted API key. Inspect per-dependency status fields because HTTP 200 can include a degraded component. |

In the web app, the **Help** menu shows the frontend build. Check both builds if the UI and API appear to disagree. For a rollout, use the release verifier to compare the manifest with the running image digests for all expected roles. [Rollout evidence](../docs/operations/release-assurance.md#verify-a-kubernetes-rollout).

## Choose a monitoring profile

These choices layer onto a working installation. Keep dashboards, scrape targets, telemetry ingestion, and trace search on operator-accessible paths. The installation owner provides credentials, storage, capacity, and an approved alert destination.

| Goal | Profile and expected result |
|---|---|
| Check core health and follow one request | Health endpoints and container logs. API and MCP diagnostics can carry request IDs for correlation. [Application telemetry](../architecture/OBSERVABILITY.md). |
| Monitor a Compose installation | The monitoring overlay provisions Prometheus, Grafana dashboards, and baseline alert rules. Add authenticated API scraping when required by the site. [Compose monitoring](../docs/operations/deploy-compose.md#monitoring-add-on). |
| Monitor Kubernetes, K3s, or OpenShift | Site-managed Prometheus Operator selects the supplied PodMonitors and rules. Confirm selectors, workload roles, credential access, and the installed profile scope. [Kubernetes monitoring](../docs/operations/deploy-kubernetes.md#monitoring-podmonitor-and-prometheusrule). |
| Keep operational logs and traces searchable | The optional Compose profile adds an OpenTelemetry Collector, Loki, and persistent Jaeger. Kubernetes, K3s, and OpenShift client overlays use mutual TLS with a gateway you operate. [Durable telemetry](../docs/operations/durable-telemetry.md). |
| Operate without internet access | Stage the optional telemetry bundle and its external, pinned Grafana and Loki prerequisites on a connected host. Import the bundle and verify the profile with outbound access denied. [Air-gapped telemetry](../docs/operations/durable-telemetry.md#offline-compose). |

For paging and recovery messages, enable the optional Alertmanager integration with authorized HTTPS receivers. Set up the independent watchdog receiver on a separate failure path so it can detect a stale heartbeat during a monitoring outage. Scope host and PostgreSQL exporter metrics to the installation and use the documented private mutual TLS configuration. [Alert routing and verification](../docs/operations/alert-routing.md). [Infrastructure exporters](../docs/operations/infrastructure-exporters.md).

## Verify your profile

1. In Prometheus, compare the expected role inventory with the actual targets. Check scrape health and each required collector's last-success timestamp. A successful scrape shows that the listener answered. A fresh collection timestamp shows that the underlying read succeeded. [Telemetry contract](../docs/operations/telemetry-contract.md).
2. Use a disposable project or deployment to process a known test report. Follow the original upload identity to its terminal status, confirm the test results, and inspect the relevant worker's attempts and queue depth. When the workload exercises an enabled child investigation, confirm progress on `agent_children` as well. [Upload recovery](../docs/operations/upload-client-recovery.md). [Queue monitoring](../docs/operations/celery-queue-monitoring.md).
3. In a durable profile, generate a known API request, a scheduled Celery task, and an MCP tool call. Look them up in Loki using the documented hashed identifiers. When the request or task has a sampled trace, use its trace ID to open it in Jaeger. Check Collector queue capacity and delivery failures. [Search an incident](../docs/operations/durable-telemetry.md#search-an-incident).
4. Verify an alert and its recovery through the authorized test receiver in a disposable deployment. Check that the independent receiver detects missing and stale watchdog data. Record the tested release, environment, scope, and observation window for your team. [Alert delivery verification](../docs/operations/alert-routing.md#disposable-delivery-verification). [Release signal gate](../docs/operations/release-signal-gate.md).

The supplied checks establish reference behavior. Verify delivery, storage, access, sizing, and recovery on your own infrastructure before using the signals for production paging. [Full deployment verification checklist](../docs/operations/observability-checklist.md). [Telemetry capacity](../docs/operations/telemetry-capacity.md). [Release rehearsal](../docs/operations/release-assurance.md#rehearse-the-release-on-a-disposable-candidate).

## Find the right evidence

| When you see | Start here |
|---|---|
| An API or MCP error | Record the time, serving revision, and request ID. Search the relevant service's operational logs. Use a trace ID in the record to look for a matching retained trace. [Correlation and logging](../docs/operations/durable-telemetry.md#search-an-incident). |
| A stalled ingest or child investigation | Check fresh broker-pending depth, worker wait, recent attempts, and the current worker readiness probe. Use durable receipts or workflow outcomes to determine whether the operation completed. [Queue monitoring](../docs/operations/celery-queue-monitoring.md). [Worker readiness](../docs/operations/worker-readiness.md). |
| **Upload confirmation pending**, or HTTP 503 with a receipt | Use **Check status** in the open upload form or follow the original task and run IDs in the CLI diagnostic. Preserve the same idempotency key and original upload data for an intentional recovery. [Upload caller recovery](../docs/operations/upload-client-recovery.md). [Upload publication and operator recovery](../docs/operations/upload-dispatch-recovery.md). |
| A missing digest or a missed nightly slot | Check durable attempt and completion observations. Use the feature and schedule state to distinguish disabled work, a missed run, generation, delivery, and an unresolved outcome. [Weekly digests](../docs/operations/weekly-digest-observability.md). [Scheduled maintenance](../docs/operations/scheduled-maintenance.md). |
| A delay in an agent timeline | Check delivery status and backlog in the project-scoped event-log health API. Authorized users can requeue a failed event for delivery. The underlying pipeline run keeps its own lifecycle. [Pipeline event delivery](../docs/operations/pipeline-event-delivery.md). |
| An alert or missing metrics | Check discovery and credentials first, then resource scope, collection freshness, and the signal that the rule uses. An empty queue with a fresh observation is valid. Missing or stale observations mean the queue depth is unknown. [Discovery failures](../docs/operations/telemetry-contract.md). [Dependency metrics](../docs/operations/dependency-metrics.md). [Database pools](../docs/operations/db-pool-metrics.md). |
| A change in API availability or response times | Review eligible attempts, coverage, and scope. Adopt and baseline the optional objectives before enabling their notifications. Use independent reachability checks for outages outside the API measurement boundary. [API objectives](../docs/operations/api-objectives.md). [Journey measurements](../docs/operations/journey-slis.md). |
| An audit or retention request | Use application audit records and approved storage. The optional archive custody process seals exports for independent verification. Identity retention and holds have their own authorization and operator procedures. [Audit archives](../docs/operations/audit-archive-custody.md). [Identity event retention](../docs/operations/identity-event-retention.md). |

The optional operational export sends selected and sanitized API, Celery, and MCP signals to destinations you configure. Metrics use bounded label sets. The central log projection uses bounded fields and hashed identifiers for request and task lookup. Choose privacy, access, and retention settings for your environment. [Data and privacy](../DATA_AND_PRIVACY.md). [Telemetry capacity](../docs/operations/telemetry-capacity.md). [Metric cardinality](../docs/operations/metric-cardinality.md).

## Related documentation

- [Operator troubleshooting](troubleshooting.md)
- [Observability architecture](../architecture/OBSERVABILITY.md)
- [Settings and defaults](../docs/reference/configuration.md)
- [Release assurance and rollout evidence](../docs/operations/release-assurance.md)
- [Observability delivery history](../docs/maintainers/observability-delivery.md)
