# Durable logs and traces

The durable profile keeps selected operational logs, API traces and worker execution spans searchable across process and container restarts. Prometheus continues to scrape application metrics directly. The platform operator owns collection, storage capacity, Grafana access, and the alerts for telemetry loss.

| Platform | Collection and storage | Trust and availability |
|---|---|---|
| Source and release Compose | OpenTelemetry Collector, Loki filesystem storage, and Jaeger with Badger. Grafana reuses its existing Prometheus and Jaeger UIDs and adds `loki`. | One installation on one host. The private network peers share a trust boundary and the named volumes have no replication. |
| Kubernetes, K3s and OpenShift | The telemetry client overlays send to the site's authenticated OTLP gateway. The site owns the gateway, persistent queue, native Loki ingestion or equivalent, and a supported Jaeger or managed trace backend. | Workloads use client certificates. The gateway owner configures admission, ingress policy, storage, authorized query access and HA. |
| Managed cloud or existing monitoring | Use the same HTTPS OTLP log and trace export contracts with the site's authenticated endpoint. Keep native Prometheus, managed scraping or an existing compatible metrics stack. | The service owner specifies retention, residency, access, egress and loss monitoring in the incident runbook. |
| Air-gapped Compose | The optional offline bundle includes the redistributable telemetry images and configuration, with pinned Grafana and Loki prerequisites supplied by the operator. | Build on the target architecture and verify the archive and its matching source release before import. Use locally authorized HTTPS receivers for alerting. |

## Enable Compose

Use Docker Compose 2.24.4 or later. Supply the existing monitoring profile's Grafana credentials and expose Grafana through an authorized operator tunnel or authenticated TLS proxy. The additional components publish no host ports.

```sh
docker compose -f docker-compose.release.yml -f docker-compose.monitoring.yml \
  -f docker-compose.telemetry.yml config --quiet
docker compose -f docker-compose.release.yml -f docker-compose.monitoring.yml \
  -f docker-compose.telemetry.yml up -d
```

Use `docker-compose.yml` as the base when building images from source. The [authenticated scrape](deploy-compose.md#authenticate-the-api-scrape) and [alert routing](alert-routing.md) overlays can follow the durable profile. Regenerate an existing alerting configuration from the new Prometheus source so it loads the collector's scrape include. API and workers start independently of all telemetry services. The initialization container prepares the three volume mounts for non-root processes, has no network access, and exits before those processes start.

## Search an incident

In Grafana Explore, select **Loki**. Resource labels are `service_name`, `service_namespace`, and `deployment_environment_name`. Request, task, span and trace IDs stay out of the index. For a request ID the API or MCP service returned, hash its original ASCII spelling with no newline.

```sh
printf '%s' "$REQUEST_ID" | sha256sum
```

```logql
{service_name="testlookup", service_namespace="testlookup"}
  | request_id="<sha256 hex>"
```

Search Celery execution records by the same hash in `celery_task_id`. API and worker records with an active span include its native OTLP trace and span IDs, which can link to Jaeger. The Jaeger datasource uses Grafana's v3 option for lookup by trace ID. Service and operation search uses the private Jaeger v2 UI, available through an authorized path on the storage network. App and operational error logs remain available through each service's stdout. The Compose profile rotates those service logs at three 10 MiB files per container.

## Data and limits

- Central logs include API request and server error events, Celery task outcomes, MCP SSE response metadata and MCP tool completions in both transports. The collector rebuilds these records from fixed event names, hashed IDs and bounded status and duration fields before enqueueing. URLs, payloads, raw exception text, credentials, and product data stay out of the central log projection. Keep business audit and retention evidence in the application stores.
- API, Celery and MCP traces are sanitized before OTLP export. The collector applies a second attribute allowlist and clears trace status messages and event text. Library span context and timing can reveal infrastructure topology. Limit trace access to operators with that responsibility.
- The optional profile enables tracing for both worker deployments, including their prefork children, and standalone Beat. A validated W3C `traceparent` links broker delivery to the producer span. Operational spans contain a fixed operation name and bounded outcome and queue family. Image builds can supply the exact revision and installations can set `OBSERVABILITY_INSTALLATION` for release evidence. Loki stores the validated values as structured metadata. [Celery tracing](worker-tracing.md), [release signal gate](release-signal-gate.md).
- MCP tool calls contribute completion counts and durations to the collector's private Prometheus projection. Use the hashed request ID to find selected MCP log events and their trace IDs. [MCP metrics and scrape contract](telemetry-collector.md#mcp-tool-metrics).
- The app log SDK holds 512 records per process in memory and sends batches of up to 64. The collector has a 32 MiB persistent queue per exporter, a 64 MiB per-database ceiling, and up to 10 minutes of retry per dequeued item. Install a filesystem or volume quota that can accommodate both queues and compaction. A collector acknowledgement means it admitted the batch to its durable queue. The downstream store can still refuse it.
- Reference log search retains 7 days, with asynchronous compaction and deletion. Reference traces retain 72 hours. Badger may preallocate several GiB even with little trace traffic. Reserve separate quota and headroom for each store, measure real intake, and alert on free space and retained volume. Choose retention and backup for the installation's incident and privacy obligations.
- A single node's disk loss can remove both queued and stored telemetry. For HA use a separately operated collector deployment and shared or replicated stores supported by the vendors. Preserve a separate heartbeat and alert delivery path.

## Kubernetes and managed gateways

Render `k8s/overlays/prod-telemetry`, `k8s/overlays/k3s-telemetry`, or `k8s/overlays/openshift-telemetry` with the same image and application configuration as the base installation. Each enables the private OTLP/HTTP address `https://testlookup-otel.testlookup-telemetry.svc:4318`. The service's gateway pods carry the label `app.kubernetes.io/name: testlookup-otel` and accept application traffic on port 4318 from the authorized namespaces and pod roles. The gateway certificate must match that service DNS name.

Install separate namespace-local Secrets `testlookup-otel-api-client`, `testlookup-otel-worker-client`, `testlookup-otel-beat-client`, and `testlookup-otel-mcp-client` with `ca.crt`, `tls.crt`, and `tls.key` keys. The app sends through the OpenTelemetry exporter and reads the mounted trust material from files. The mounts are optional so missing material or a gateway outage leaves the application available. Require client authentication at the gateway and rotate credentials with a controlled workload restart. For a managed gateway, change the client endpoints, egress policy and trust material to the approved site values. Configure the gateway to accept and expose the bounded MCP metrics for a private scrape if MCP monitoring is enabled at the site.

The added egress policy names the gateway and port. Existing application policies may allow broader egress because Kubernetes combines allow rules. Enforce source admission and a dedicated client trust policy on the gateway, including denial without the expected client certificate.

## Offline Compose

Add `--with-telemetry` to the standard [offline bundle](../../user-guide/air-gapped-install.md) build. It records upstream digests alongside local image aliases. The OSS Grafana and Loki images are external prerequisites under TestLookup's redistribution policy. The bundle's `compose/telemetry-prerequisites.list` records each pinned source image and required local alias as tab-separated fields. Stage those two images on a connected host, tag each verified image under its listed alias, and transfer and load them using your approved offline image process. [Grafana Labs licensing](https://grafana.com/licensing/).

After verified import and normal registry setup, layer `docker-compose.monitoring.yml` and `docker-compose.telemetry.yml` after the release and air-gap Compose files. The bundle also includes the optional objectives overlay and its inert adoption example. Run `docker compose ... up --pull never`. Provide environment files, credentials and optional alert receiver trust from local sources. Perform the first run with outbound network access denied and verify no external image, plugin, catalog or notification dependency is needed. [API objectives](api-objectives.md).

## Acceptance and response

1. Confirm the expected Collector role is up in Prometheus. Check `otelcol_exporter_queue_size`, `otelcol_exporter_queue_capacity`, enqueue and send failure counters, receiver refusals and available space. The durable overlay alerts on a queue above 80% for two minutes or recent loss. Verify the separate receiver can report a complete monitoring outage.
2. Generate a synthetic API request, a Beat-scheduled Celery task and an MCP tool call. Read them back by hashed ID and follow the trace across boundaries. Run an MCP tool from each enabled transport, confirm tool completions appear in the private scrape and check that payload and credential markers are absent from telemetry.
3. Break each storage connection in a disposable deployment. Observe queue growth without an application readiness failure. Verify recovery after a collector restart, queue refusal at capacity, and loss metrics when retries expire.
4. Restart each store with its volume retained and verify readback. Check retention and authorized and denied query paths. Check cluster admission, monitor selection and credential rotation independently for K3s and OpenShift.

The reference configuration follows the [OpenTelemetry Collector resiliency guide](https://opentelemetry.io/docs/collector/resiliency/), [Loki native OTLP mapping](https://grafana.com/docs/loki/latest/send-data/otel/), and [Jaeger storage documentation](https://www.jaegertracing.io/docs/2.23/storage/).

Component details and verification commands are in [collector operations](telemetry-collector.md), [log storage](log-storage.md), [persistent Jaeger](persistent-jaeger.md), and [telemetry capacity and sampling](telemetry-capacity.md).
