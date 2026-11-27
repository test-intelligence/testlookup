# Telemetry capacity and sampling

Use the [durable telemetry profile](durable-telemetry.md) for a single installation. Its Collector, Loki and Jaeger limits bound the footprint of those services. Size Prometheus separately for the application's scrape targets, rules and alert history.

## Reference measurements

The benchmark used Collector Contrib 0.166.0, Loki 3.7.10, Jaeger 2.23.0 and Prometheus 3.16.0 from the pinned releases. They ran as native loopback processes on an Intel Core Ultra 9 285K Windows host with 32 GiB of RAM. The store and queue settings matched the reference files. Native processes did not have the Compose cgroup limits. Each profile began on fresh storage and warmed up with 16 logs and 64 spans. The measured burst used 4,096 synthetic logs, four concurrent producers, 64 records per batch, roughly 5% server errors and four spans per retained trace. Logs represented API, two worker roles and MCP. Prometheus scraped the Collector, stores and itself every 2 seconds for this test.

| Measurement | Every root sampled | 10% of roots sampled |
|---|---:|---:|
| Logs and spans delivered | 4,096 and 16,384 | 4,096 and 1,640 |
| Uncompressed OTLP payload | 5.49 MiB | 2.43 MiB |
| Collector ingress and complete delivery | 0.267 s and 3.218 s | 0.300 s and 2.812 s |
| Collector ingestion HTTP p95 | 25.94 ms | 24.90 ms |
| Largest combined queue sample during ingress | 619.5 KiB | 359.8 KiB |
| Loki hashed request lookup p95, 12 reads | 31.88 ms | 31.10 ms |
| Jaeger four-span trace lookup p95, 12 reads | 25.56 ms | 20.02 ms |
| Prometheus head series observed | 2,259 | 2,259 |
| CPU seconds added, Collector / Loki / Jaeger / Prometheus | 0.63 / 0.36 / 0.64 / 0.03 | 0.42 / 0.33 / 0.13 / 0.03 |
| End RSS, Collector / Loki / Jaeger / Prometheus | 167 / 141 / 164 / 76 MiB | 162 / 151 / 65 / 73 MiB |
| End logical disk, Collector / Loki / Jaeger / Prometheus | 3.00 MiB / 2.24 MiB / 2.13 GiB / 94 KiB | 2.13 MiB / 2.24 MiB / 2.13 GiB / 90 KiB |

The 10% profile used 56% fewer ingress bytes and retained every log. The short run measures burst delivery and readback. Jaeger preallocated about 2.13 GiB even after warmup, and Loki and Prometheus still held recent data in their active write paths. Use measured disk allocation after several compaction cycles under site traffic to calculate storage growth. The burst result includes neither the application scrape series nor application load. The query samples use exact IDs in a recent two-minute range.

With both destinations unavailable, the unchanged Collector queue settings admitted about 31.6 MiB of logs and 32.0 MiB of traces. The next three batches per signal returned HTTP 503 in 1.9–20.6 ms. The Collector reported enqueue failures for 4,500 log records and 768 spans and used 128 MiB of logical queue files. The native recovery tests also exercise persistent replay after abrupt restart, retry exhaustion at a shortened deadline and Jaeger read expiry with a shortened TTL. Loki kept recent data across a forced restart and rejected a synthetic record timestamped eight days earlier under the configured seven-day age limit. Verify asynchronous disk reclamation at the site's configured retention intervals.

## Reproduce in disposable storage

Start the pinned binaries with the reference configuration, unique writable data directories and loopback-only ports. Point the Collector's HTTP exporters at the disposable stores. Use the same scrape and query intervals across comparisons. Keep the storage directories until you have recorded compaction growth, then discard the synthetic profile. The workload command accepts only explicit HTTP loopback origins.

```sh
python scripts/monitoring/measure_reference_capacity.py \
  --collector "$COLLECTOR_OTLP" --collector-metrics "$COLLECTOR_METRICS" \
  --loki "$LOKI" --jaeger "$JAEGER_QUERY" --jaeger-metrics "$JAEGER_METRICS" \
  --prometheus "$PROMETHEUS" --collector-data "$COLLECTOR_DATA" \
  --loki-data "$LOKI_DATA" --jaeger-data "$JAEGER_DATA" \
  --prometheus-data "$PROMETHEUS_DATA" --trace-fraction 1.0
```

Repeat on a fresh copy with `--trace-fraction 0.1`. The driver checks exact delivery counter deltas, queries up to 24 log and trace IDs, and reports observed queue pressure, process metrics, logical bytes and query times. It generates whole sampled traces. Separately load a candidate application with representative authenticated traffic and large reports, including successful jobs, failed jobs, retries, and concurrent live events. Measure application latency and accepted-to-completed progress alongside telemetry growth so a report's size and its telemetry fan-out can be compared separately.

## Size and operate the stores

| Signal | Reference limit | Site sizing input |
|---|---|---|
| Collector queues | 32 MiB per signal, 64 MiB ceiling per database, 10-minute retry per dequeued work item. Compose gives the Collector 1 CPU and 512 MiB of memory. | Reserve space for both databases and compaction, starting with 256 MiB plus filesystem overhead. Divide 32 MiB by each signal's observed enqueue bytes/s to estimate its queue-only outage coverage. Retry age, disk pressure and process availability can shorten it. |
| Loki | 7-day search/retention window with asynchronous compaction and deletion. The reference limit is 1 MiB/s per tenant and 512 KiB/s per stream before bursts. Compose gives Loki 1 CPU and 1 GiB of memory. | Measure compacted bytes/day, WAL, index and free space at peak traffic. Budget daily growth × retention plus write and compaction headroom. Measure hashed-ID queries at the full search horizon as well as recent lookups. |
| Jaeger | 72-hour span TTL. Compose gives Jaeger 1 CPU and 512 MiB of memory. | Start with the measured preallocation, add post-compaction daily growth × retention and reserve space for storage maintenance. Compare query latency and RSS under concurrency. |
| Prometheus | Baseline time retention is 15 days. The optional API objectives profile uses 35 days plus a site-selected size cap. | Measure samples/s, head series, and compacted bytes/day. Prometheus estimates 1–2 bytes per compacted sample. Allow extra room for the WAL and head blocks, and set the size cap to at most 80–85% of a dedicated volume. Whichever retention limit is hit first applies. [Prometheus storage](https://prometheus.io/docs/prometheus/latest/storage/). |

Set explicit disk quotas or volumes on the host with sustained free-space alerts. The shipped telemetry alerts surface high queue occupancy, refused ingress and failed exports. A short burst can fill the queue before an occupancy alert's hold time, so check refusal counters as well. If a store is down, restore write capacity and confirm drain before increasing ingest or retry limits. Separate metric and incident sinks from the application failure domain where required. Single-node storage and a single local filesystem share a loss boundary.

## Select a trace sample rate

The durable Compose profile defaults to `parentbased_always_on`. To reduce transport and storage for a larger site, set `OTEL_TRACES_SAMPLER=parentbased_traceidratio` and an explicit `OTEL_TRACES_SAMPLER_ARG` such as `0.1` on the API. Apply the same policy across its replicas. Child spans respect their parent's sampling decision. A head sampler makes its decision before the request outcome, so a failed request can have a log and trace identifier without a stored trace. Logs, Prometheus counters and authoritative audit evidence remain independent of the trace selection. [OpenTelemetry sampling](https://opentelemetry.io/docs/concepts/sampling/), [SDK environment variables](https://opentelemetry.io/docs/specs/otel/configuration/sdk-environment-variables/).

For a site that needs policy-driven tail sampling, the gateway operator owns routing all spans in a trace to the same sampler, bounded in-memory state, decision delay and sampling failure metrics. Decide how to persist accepted work through gateway failure before moving that stage in front of a durable queue. Verify error and slow-trace visibility with injected failures, peak concurrency, and trace-ID reads. Apply log and trace retention policies independently of [audit evidence custody](audit-archive-custody.md).
