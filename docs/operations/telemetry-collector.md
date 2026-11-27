# Private telemetry collector

The optional reference collector accepts the installation's approved
application logs and sanitized traces over private OTLP/HTTP. It forwards
logs to `http://loki:3100/otlp/v1/logs` and traces to
`http://jaeger:4318/v1/traces`. Prometheus continues to pull application and
collector metrics directly; the collector has no metrics ingestion pipeline.

[`config.yml`](../../infra/monitoring/otel-collector/config.yml) is validated
against the official Collector Contrib 0.166.0 distribution. The separate
[`image-lock.json`](../../infra/monitoring/otel-collector/image-lock.json)
records its immutable image index, non-root identity and release archive
checksums. The optional `--with-telemetry` bundle includes this image.

## Transport and process contract

Mount the configuration at `/etc/otelcol-contrib/config.yml` and pass
`--config=/etc/otelcol-contrib/config.yml` to `/otelcol-contrib`. Run as
10001:10001, drop capabilities, disable privilege escalation and make the
root filesystem read-only. Initialize only the dedicated persistent volume
for that identity; do not grant host-wide ownership or mount a Docker socket.
The writable directories are `/var/lib/otelcol/storage` and
`/var/lib/otelcol/compaction`, owned by that identity. Keep both on the same
persistent volume and permit no second collector process to open its files.

Port 4318 accepts OTLP/HTTP only. Ports 13133 (process health) and 8888
(Prometheus self-metrics) are operational APIs. None should have a host port,
public ingress or CORS origin. The Compose trust boundary is dedicated
internal networks: approved application services to the collector, then the
collector to its stores, with separately selected monitoring access. Service
identity filters constrain data; they do not authenticate a hostile client.
Do not attach unrelated containers or tenants to these networks.

HTTP here is confined to that single trusted installation. Before crossing
hosts, clusters or another trust boundary, replace ingress and exporter
transports with authenticated TLS/mTLS, verify the destination CA/name and
mount certificates/keys from the site's secret manager. Configure the
corresponding SDK OTLP certificate/client-identity settings; do not turn off
certificate verification or publish this reference HTTP receiver. Test the
site configuration with the pinned binary and a disposable authenticated
destination before using it. Public or multi-tenant OTLP ingestion requires
a different reviewed authorization/tenant-isolation contract.

## Data allowed into durable storage

Both pipelines require `service.namespace=testlookup` and
`service.name=testlookup`. The reference log roles are `api`, `worker`,
`worker-children`, `beat` and `mcp`; an unsupported service/role is filtered.
Custom service names need an explicit reviewed site filter change.

Logs require the SDK's native event name to be one of `http_request`,
`http_unhandled_error`, `analytics_unhandled_error`, `celery_task_execution`
or `mcp_request`. The collector discards the incoming body and rebuilds JSON
from that approved event name, severity, service, native trace/span IDs,
64-character lower-case SHA-256 request/task identifiers, HTTP status
100–599, duration 0–600000 milliseconds and a closed task-state vocabulary.
Unknown attributes and free text never enter its export queue. Hashes are
correlation pseudonyms, not anonymized data: restrict access and retention.

The application remains the complete trace privacy boundary, including span
names, links, scopes, resources and credentials in custom text. The pinned
SDK export-boundary regression serializes sensitive fake link/tracestate
values and verifies their removal while preserving causal identifiers. The
collector additionally retains a small operational attribute set, removes
status text and free event text, clears top-level tracestate and removes
unapproved resource/scope attributes before queueing. Already-sanitized span
links remain intact. This is not a general scrubber for arbitrary third-party
spans; do not bypass the application's exporter wrapper.

Only the approved event projection is ingested. Ordinary stdout logs remain
available through the platform's existing logging facilities. There is no
filelog receiver, host-log discovery, audit-history ingestion or duplicate
stdout collection in this reference configuration. The SDK event projection
does not imply full API/worker/beat/MCP trace coverage.

## Bounds, durability and loss

Each receiver request is limited to 1 MiB. The memory limiter starts refusing
work at a 320 MiB soft threshold with a 384 MiB hard limit and a one-second
check interval; begin with a 512 MiB container limit and measure the selected
workload before sizing production. It cannot prevent every allocation spike.

Each exporter persists at most 32 MiB of serialized queued telemetry and has
two consumers. Batching happens inside the persistent exporter queue, rather
than in an acknowledged in-memory batch processor: 8 KiB minimum, 64 KiB
maximum and one-second flush interval. A single indivisible record can exceed
the batch target; the receiver limit and destination request limit must also
fit. Rejection is immediate when a queue is full. Export attempts have a
five-second timeout and retries back off from one to ten seconds, with ten
minutes allowed per retry sequence. Queue waiting time is additional; this
is not a ten-minute event retention TTL. Restart may begin a new retry
sequence, so repeated restarts are not a retention mechanism.

File storage uses fsync and caps **each** bbolt database at 64 MiB. Two
exporters, their indexes and compaction need additional disk space: reserve
and enforce an installation-level filesystem/volume budget, initially
512 MiB, and alert before it fills. Compaction runs at startup and on rebound.
The database cap is distinct from a volume quota; neither removes unrelated
files or orphaned databases after changing component IDs. Keep exporter IDs
and spool paths stable across replacement. Review stored-state migration
before changing versions; do not delete an active queue to repair a full disk.
Use host/storage encryption and restrict volume/backups as telemetry data.

Persistent queues survive an abrupt collector restart when their volume
survives. Delivery is at least once and duplicates remain possible around
acknowledgement. Disk corruption/loss, full storage, permanent destination
errors, queue overflow and exhausted retries can still lose telemetry.
Application SDK buffers are finite and asynchronous; collector health must
not be a prerequisite for application startup or request processing. Data
not yet accepted by the collector can be lost during an outage. This local
queue is not an audit ledger, backup, replicated broker or HA guarantee.

## Observe and verify

The pinned binary exposes these Prometheus series without a `_total` suffix:

| Question | Series |
|---|---|
| Queued serialized bytes / capacity | `otelcol_exporter_queue_size`, `otelcol_exporter_queue_capacity` |
| Queue/storage refused input | `otelcol_exporter_enqueue_failed_spans`, `otelcol_exporter_enqueue_failed_log_records` |
| Terminal retry/export loss | `otelcol_exporter_send_failed_spans`, `otelcol_exporter_send_failed_log_records` |
| Accepted/refused/failed ingress | `otelcol_receiver_accepted_*`, `otelcol_receiver_refused_*`, `otelcol_receiver_failed_*` for `spans` / `log_records` |
| Contract filtering | `otelcol_processor_filter_spans_filtered`, `otelcol_processor_filter_logs_filtered` |
| Process resident memory bytes | `otelcol_process_memory_rss` |

Some error series are created only after the relevant event. Terminal
`send_failed` counts do not count every temporary 503 while a batch is still
retrying. Watch queue utilization and refusals during an outage and terminal
loss after retry exhaustion. Health port 13133 proves process readiness; it
does not prove export success, freshness, free disk or an empty queue.

Set `OTELCOL_TEST_BINARY` to the checksum-verified official executable and run
`python -m pytest scripts/monitoring/tests/test_otel_collector.py -q`.
The native tests validate the actual config, send OTLP through real filtering
and persistence, force a 4 KiB test queue to overflow against a disposable
503 sink, check byte-cap/error metrics and pre-queue canary removal, kill the
process without shutdown, then verify replay of every accepted synthetic
trace/log to the recovered sink. A separate short retry sequence verifies
terminal-loss metrics. The tests use loopback, temporary data and fake
payloads only. They require PyYAML, pytest and the application's OTLP protobuf
package. Without the binary the runtime tests are skipped; that is not
production verification.

Also set `LOKI_TEST_BINARY` and `JAEGER_TEST_BINARY` to their verified releases
to enable joint store readback. That test sends matching native trace/log IDs
through the real collector, retrieves the trace from Jaeger and the projected
log from Loki, repeats after collector replacement and checks Loki's series
API for its three allowed indexed resource labels. Query-result stream labels
alone are not index evidence: LogQL can promote filtered structured metadata
into those returned labels. This test covers disposable native processes,
not rendered container network/volume permissions or full application traffic.

Production acceptance still requires the rendered container network/UID/
volume permissions, encrypted cross-boundary settings where applicable,
application availability under telemetry outage, representative throughput
and memory/disk budgets, store readback, restart and retention evidence for
the selected deployment profile.

Configuration and limits follow the official
[OTLP receiver](https://github.com/open-telemetry/opentelemetry-collector/tree/v0.166.0/receiver/otlpreceiver),
[exporter queue/retry helper](https://github.com/open-telemetry/opentelemetry-collector/tree/v0.166.0/exporter/exporterhelper),
[file storage extension](https://github.com/open-telemetry/opentelemetry-collector-contrib/tree/v0.166.0/extension/storage/filestorage)
and [Collector resiliency guidance](https://opentelemetry.io/docs/collector/resiliency/).
