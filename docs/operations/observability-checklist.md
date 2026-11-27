# Set up and verify production observability

Use this checklist after the application deployment is healthy. It covers the merged observability source on `main`. Use the matching application images, manifests, and documentation for your release or candidate. [Product observability guide](../../user-guide/observability.md) · [Deployment guide](deployment.md).

## Before you begin

- Record the candidate source SHA and image digests, the installation or namespace, and the application and monitoring owners. Use the same image set across API, frontend, MCP, worker and Beat roles. Record deliberately disabled roles.
- Arrange a private route to Prometheus and any log and trace stores. Use an authorized operator identity, a disposable test project, and an approved test alert destination.
- Define storage retention, access, quota, and response ownership before sending logs or traces. Budget capacity for exporter queues and stored data. [Durable telemetry](durable-telemetry.md) · [Telemetry capacity](telemetry-capacity.md).

## Choose a profile

| Installation | Start here | Optional additions |
| --- | --- | --- |
| Compose or a single VM | Add the reference Prometheus and Grafana profile, and configure private access. [Compose monitoring](deploy-compose.md#monitoring-add-on). | Enable API scrape authentication, durable Loki and Jaeger behind the Collector, Alertmanager delivery, API objectives, or site-owned infrastructure metrics as needed. |
| Kubernetes, K3s, or OpenShift | Configure the Prometheus Operator's rules and PodMonitor selectors for your installation. [Kubernetes monitoring](deploy-kubernetes.md#monitoring-podmonitor-and-prometheusrule). | Add a telemetry client overlay and matching client certificates for the site-operated OTLP gateway. Use the site's Alertmanager and stores. [Gateway configuration](durable-telemetry.md#kubernetes-and-managed-gateways). |
| Air-gapped Compose | Prepare the optional telemetry bundle and transfer the exact images in its prerequisite inventory. [Air-gapped install](../../user-guide/air-gapped-install.md) · [Offline telemetry](durable-telemetry.md#offline-compose). | Supply local credentials, the approved query access path, and an internal receiver. Run the first verification with outbound network access blocked. |
| Existing or managed monitoring | Configure compatible Prometheus scraping and an authenticated OTLP endpoint for logs and traces. [Managed gateway requirements](durable-telemetry.md#kubernetes-and-managed-gateways). | Verify the destination's admission and label mappings, retention, and notification behavior under the site's policies. |

## Verify the deployed signals

1. **Identify the serving revision and API readiness.** Use `curl` and `jq`. Set `TESTLOOKUP_URL` to the approved HTTPS base URL that routes health requests to the API, without a trailing slash. Confirm the JSON `build.revision` matches the intended source SHA and readiness is `ready`. Source builds that omit build provenance can return `unknown`. Use the release manifest to verify every deployed role independently. [Release assurance](release-assurance.md#verify-a-kubernetes-rollout).

   ```sh
   curl --fail --silent --show-error "$TESTLOOKUP_URL/health/version" | jq -er '.build.revision | strings'
   curl --fail --silent --show-error "$TESTLOOKUP_URL/health/ready" | jq -e '.status == "ready"'
   ```

2. **Check required scrape roles and fresh samples.** In the selected Prometheus, compare `testlookup:telemetry_expected_role` with healthy `up` targets for the installation. Check the collector success and freshness samples for the queues, agents, downstream and Redis collectors. Investigate stale, incomplete, or missing series using the target errors and profile inventory. Verify authenticated API scraping from its allowed network. [Required telemetry](telemetry-contract.md) · [Dependency metrics](dependency-metrics.md).
3. **Follow a synthetic job through the application.** Submit a small fixture report to the disposable project. Save the task and run IDs, confirm the run reaches its stored outcome, and check broker backlog and worker progress, including the child worker when the workflow uses it. For scheduled work, check one enabled slot against durable completion. [Upload recovery](upload-dispatch-recovery.md) · [Queue monitoring](celery-queue-monitoring.md) · [Scheduled maintenance](scheduled-maintenance.md).
4. **Read back logs and traces where enabled.** Make an authorized API request that starts an application trace. For a sampled request, use the request ID and the log runbook's hashing instructions to find its projected log record, then open the recorded trace ID. Check a Celery task and one call through each enabled MCP transport. Verify Collector queue, exporter failure, refusal, and store headroom measurements. [Log storage](log-storage.md) · [Worker tracing](worker-tracing.md) · [Collector operations](telemetry-collector.md).
5. **Test alert delivery and resolution.** Use a disposable installation to exercise an approved firing and resolution. Check the separately hosted watchdog receiver can report a missing heartbeat even if the monitoring or notification path stops. Approve an installation scope, objective owner, baseline, and receiver before enabling burn-rate alerts. [Alert routing](alert-routing.md) · [API objectives](api-objectives.md).

Record the exact image or source revision, profile, role, observed outcome, time window, and evidence location. Keep credentials, raw payloads, and sensitive query results out of shared tickets or reports. The [disposable signal gate](disposable-signal-proof.md) describes the automated candidate proof and its cleanup checks. Use additional site checks for production rollout, retention, and recovery.

## Respond to a failed check

| Observation | Start with |
| --- | --- |
| API is ready and jobs remain pending | Verify broker sample freshness, queue subscription, worker generation and progress. Confirm the durable receipt before retrying an ambiguous upload. [Queues](celery-queue-monitoring.md) · [Worker readiness](worker-readiness.md) · [Client recovery](upload-client-recovery.md). |
| Expected target or required series is missing | Check Prometheus selectors, workload role labels, allowed network path and scrape authentication. [Telemetry contract](telemetry-contract.md). |
| A recent trace or log is missing | Check sampling and the Collector's refusal, queue and send metrics, then inspect storage access and retention. Check the relevant application or worker's stdout for local operational diagnostics. [Collector operations](telemetry-collector.md) · [Log storage](log-storage.md). |
| Scheduled work has no confirmed completion | Check whether the feature was active and the slot was due, then check Beat, the subscribed worker and the stored outcome. [Scheduled maintenance](scheduled-maintenance.md) · [Digest delivery](weekly-digest-observability.md). |
| Operational evidence is incomplete | Check the pipeline event outbox and use the authorized retry and custody procedures. [Pipeline event delivery](pipeline-event-delivery.md) · [Audit archive custody](audit-archive-custody.md) · [Identity retention](identity-event-retention.md). |

Use the approved previous image and manifest pair during rollback. For a schema rollback, rehearse the matching backup restore and verify the restored revision and a representative product journey. [Upgrade and recovery](../../UPGRADING.md) · [Release rehearsal](release-assurance.md#rehearse-the-release-on-a-disposable-candidate).
