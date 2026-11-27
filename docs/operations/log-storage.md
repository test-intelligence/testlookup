# Persistent application logs and trace correlation

The opt-in [durable telemetry profile](durable-telemetry.md) sends application
OTLP logs through the collector to a private, single-process Loki backend.
It does not mount the Docker socket, container log directories or host files.
Only explicitly configured application exporters enter this pipeline; logs
from other containers, host services and early process startup are not claimed
as collected. The application's bounded local/container log rotation remains
a separate troubleshooting fallback.

The pinned reference uses Loki 3.7.10, verified against the upstream release
and multi-platform image manifest in
[`image-lock.json`](../../infra/monitoring/loki/image-lock.json).
It is a small-install reference with persistent storage, not an HA service or
a compliance retention system. Larger deployments can point the same OTLP
exporter and Grafana datasource at the site's authenticated managed Loki.

## Storage and trust boundary

[`loki.yml`](../../infra/monitoring/loki/loki.yml) stores chunks, the TSDB v13
index, index cache, WAL and compactor retention markers below `/loki`.
The profile mounts `loki-data` there and initializes ownership for the pinned
image's non-root UID `10001`. Keep one writer and one compactor for this
volume. Do not share it between Loki replicas. WAL recovery preserves
acknowledged fixture logs across a killed Loki process; this does not prove
durability across disk corruption, node loss or a failed volume.

Compactor retention is enabled for seven days (`168h`), using a 24-hour TSDB
index period and a two-hour asynchronous deletion delay. The compactor's
working directory is persistent so deletion markers survive process restarts.
Query lookback is also seven days. User-requested deletion APIs are disabled.
Retention is asynchronous and is not an exact deletion deadline or a disk
quota. Measure daily volume, leave WAL/index/compaction headroom, set a
site-specific volume budget and alert before the filesystem fills. Back up
the volume using a consistent snapshot while the service is stopped if
incident-log recovery is required; this reference does not configure backups,
legal holds, immutable audit storage or automatic disk expansion.

Loki's `auth_enabled: false` selects a single tenant; it does **not** add
authentication. HTTP `3100` is reachable only on the dedicated internal
storage network, from Grafana and the collector, with no published host port.
The gRPC listener binds to `127.0.0.1`. Do not add public ingress to this
reference endpoint. A shared/untrusted network needs a separately reviewed
TLS/authentication boundary and tenant/query authorization. Grafana access
controls govern its authenticated query path; everyone with access to this
single datasource sees this tenant's operational logs.

The configuration bounds ingestion to 1 MiB/s with a 2 MiB burst, 500 active
streams, 64 KiB log lines and 16 KiB/32 entries of structured metadata per
record. Lines above the limit are rejected, not partially truncated. Queries
are limited to 1,000 log entries, 30 seconds and two concurrent queriers.
These settings bound specific operations; they do not guarantee total memory
or disk use. Observe collector queue/drop/retry and Loki rejection metrics
when tuning the profile. The collector's persistent queue and application
export buffers have their own limits and failure windows.

## Labels, metadata and safe queries

Only these resource attributes become indexed stream labels:

| OTLP resource attribute | Loki label |
|---|---|
| `service.name` | `service_name` |
| `service.namespace` | `service_namespace` |
| `deployment.environment.name` | `deployment_environment_name` |

The reference application service and namespace are `testlookup`. Environment
and application role must come from deployment configuration, not a caller.
Bounded roles such as `api`, `worker`, `worker-children`, `beat` and `mcp` stay
metadata (`testlookup_service_role`), as do per-instance identity and version.
Trace IDs, span IDs, request IDs, task IDs and correlation IDs never become
indexed labels. Loki stores native trace/span IDs and allowlisted OTLP record
attributes as structured metadata. The sanitized JSON body redundantly
contains trace and diagnostic IDs when available, supporting text inspection
and Grafana's derived-field link. Missing trace context is valid for records
outside a sampled/current span.

The reference Jaeger trace retention is shorter than Loki log retention.
A valid log link can therefore refer to a trace that was not sampled or has
already expired; a missing trace is not proof that the stored log is invalid.

The collector restricts source identity and attribute intake; it does not
make arbitrary message bodies safe. Preserve the application's redaction
boundary before export. Request/response bodies, prompts, credentials and
user identifiers must not be introduced as log payloads or labels. Existing
operational logs are not a durable, tamper-evident audit record.

Use a narrow time range and an indexed service/environment selector, then
filter IDs as metadata:

```logql
{service_namespace="testlookup",deployment_environment_name="production"} | request_id="example-request"
{service_namespace="testlookup"} | trace_id="0123456789abcdef0123456789abcdef"
{service_namespace="testlookup"} | task_id="example-task"
{service_namespace="testlookup"} | testlookup_service_role="worker"
```

Do not put the request or trace ID inside the braces: those are not indexed
stream labels. Use `| json` only when inspecting a field that exists solely
inside the body, and avoid an unnecessarily broad body scan.

## Grafana provisioning and links

Mount the complete opt-in
[`telemetry-datasources.yml`](../../infra/monitoring/grafana/telemetry-datasources.yml)
over `/etc/grafana/provisioning/datasources/prometheus.yml`. It preserves the
Prometheus datasource and existing Jaeger UID `jaeger`, then adds Loki UID
`loki` at `http://loki:3100`. Do not add a second datasource provisioning file
that repeats the Jaeger UID. The source is deliberately outside the base
provisioning directory so the basic monitoring profile does not load it.

Jaeger's `tracesToLogsV2` query selects native `trace_id` metadata within the
`testlookup` namespace, widening the span window by two minutes on each side.
The Loki derived field extracts a 32-character trace ID from the sanitized
JSON body and opens the existing Jaeger datasource. The provisioning file
escapes Grafana macros with `$$`; stored datasource settings must retain
`${__span.traceId}` and `${__value.raw}` after provisioning.

The pinned Grafana 13.3.1 Jaeger datasource needs
`GF_FEATURE_TOGGLES_ENABLE=jaegerEnableGrpcEndpoint` for Jaeger 2.23's HTTP v3
health/service/operation/trace-ID path. The flag's name does not mean the
query URL should use a gRPC port: it remains `http://jaeger:16686`.
Full Grafana Jaeger search still uses an unavailable legacy endpoint in this
combination; use direct trace-ID correlation or the native Jaeger UI for
search. This reference does not provide a compatibility shim or claim full
Grafana trace-search support.

## Disposable verification

Static tests run with:

```sh
python -m pytest scripts/monitoring/tests/test_loki_reference.py -q
```

Set `LOKI_TEST_BINARY` to a checksum-verified executable from the recorded
release to execute actual OTLP ingestion, metadata-only ID filtering, exact
three-label index checks and forced-kill/WAL recovery. Also set
`GRAFANA_TEST_HOME` to the checksum-verified Grafana release recorded in the
lock file to execute actual datasource provisioning, macro escaping, health
and Grafana `/api/ds/query` against Loki. `GRAFANA_TEST_BINARY` can override
the executable path. Set `JAEGER_TEST_BINARY` as well for the joint case to
start the pinned Jaeger fixture, emit the same synthetic trace/log ID, and
verify Grafana health and direct trace-ID readback. Every listener is loopback
and every data path is temporary; these tests do not contact an incident
receiver or change a deployment.

Skipping the runtime tests is not ingestion or restart evidence. The
reference tests do not wait seven days for compactor deletion, prove full
filesystem/node failure recovery, exercise Grafana UI clicks, or validate
site authorization and production capacity. Installation acceptance must
also verify actual application-to-collector export, queue recovery and
sanitized source-to-query correlation in the selected profile.

Primary references: [Loki OTLP mapping](https://grafana.com/docs/loki/latest/send-data/otel/),
[TSDB storage](https://grafana.com/docs/loki/latest/operations/storage/tsdb/),
[compactor retention](https://grafana.com/docs/loki/latest/operations/storage/retention/),
[Loki datasource](https://grafana.com/docs/grafana/latest/datasources/loki/configure/),
[Jaeger datasource](https://grafana.com/docs/grafana/latest/datasources/jaeger/configure/),
and [the pinned Loki release](https://github.com/grafana/loki/releases/tag/v3.7.10).
