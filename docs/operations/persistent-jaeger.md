# Persistent Jaeger reference

The opt-in `docker-compose.telemetry.yml` profile replaces the original monitoring add-on's ephemeral all-in-one with Jaeger **2.23.0** and the native [jaeger.yml](../../infra/monitoring/jaeger.yml) configuration. Keep this profile separate from the default monitoring command. It is a small-install reference with one process and one dedicated Badger volume, not a highly available or horizontally scalable trace service.

Jaeger's [Badger documentation](https://www.jaegertracing.io/docs/2.23/storage/badger/) restricts embedded storage to a single node. Do not mount this writable database into multiple Jaeger instances or share it over a network filesystem. The collector and query extension in this process use the same `incident_traces` backend. Larger deployments should use an operator-owned supported distributed backend; Jaeger documents Cassandra, Elasticsearch and OpenSearch, and recommends OpenSearch for large production deployments. See its [storage backend contract](https://www.jaegertracing.io/docs/2.23/storage/).

## Immutable artifacts and process contract

| Artifact | Pin |
|---|---|
| Container index | `cr.jaegertracing.io/jaegertracing/jaeger:2.23.0@sha256:3823829974ccfc2485d2e48b165579091f32295022ae80ca49704e2f4834116e` |
| Upstream source | `v2.23.0`, commit `d723f91447f29a345c288f37eb8d1ce7807bc0be` |
| Linux amd64 archive SHA256 | `4b7bd152ed31d2205e4e73b873d289c61cd3fabaecc86443070143448d40b753` |
| Windows amd64 ZIP SHA256 | `56eccf9d72df52b65fc1bcbe0cb3c9cd566681de99274f1e8ecf07a7f9bf9f4c` |

Both binary archives come from the official [2.23.0 release](https://github.com/jaegertracing/jaeger/releases/tag/v2.23.0), published November 5, 2026. The multi-platform image index was resolved from the official registry; its Linux amd64 manifest is `sha256:1d157a0fa25fb0e1588427522031bce7aad80152800df62772511b8b4916140e`, and arm64 is `sha256:c8c9a90a7cd02710a9ba7406ebab7126017da76a7b77724b2725184eab418455`.

The release image's [Dockerfile](https://github.com/jaegertracing/jaeger/blob/v2.23.0/cmd/jaeger/Dockerfile) specifies UID/GID `10001:10001` and executable `/cmd/jaeger/jaeger-linux`. The command is `--config=/etc/jaeger/config.yaml`. The dedicated volume mounted at `/var/lib/jaeger` must already be writable by that user. The profile's narrowly scoped volume-initialization service handles this; the Jaeger process itself stays non-root with a read-only root filesystem.

| Surface | Reference behavior |
|---|---|
| OTLP/HTTP | Private `4318`, traces only; request bodies limited to 4 MiB. The upstream Collector exports to `http://jaeger:4318/v1/traces`. |
| Query/UI | Private `16686`; Grafana datasource UID remains `jaeger`, URL `http://jaeger:16686`. |
| Query gRPC | Loopback-only `127.0.0.1:16685`; not needed by the supplied Grafana path. |
| Health | Private `13133/status`; the old all-in-one `14269` health endpoint and `wget` check do not apply to the new distroless release. |
| Internal metrics | Private `8888`; inspect storage and receiver/exporter failures along with Collector queue/drop telemetry. |

No AI/MCP sidecar, remote sampling, Zipkin receiver, debug exporter, pprof or zpages is enabled. These endpoints have no built-in public authentication in this reference; keep them on the dedicated internal network. Do not publish the storage ports directly to clients. Native UI access for an authorized operator needs an approved authenticated gateway or temporary loopback tunnel; it is not published on the host by the opt-in profile.

## Acknowledgements, retention and resource limits

Badger is explicitly non-ephemeral. Its `consistency: true` enables synchronous disk writes, with separate `keys` and `values` directories under the dedicated volume. The Jaeger pipeline has no batch processor or exporter queue; the upstream Collector owns batching, durable buffering and retries. The storage exporter has its own queue and retry disabled, so a final-hop acknowledgement follows its storage call instead of a second volatile enqueue. A returned storage error can therefore reach the upstream collector for its bounded retry policy. This does not guarantee exactly-once end-to-end delivery.

Span keys have a finite **72-hour TTL**. Expired spans become unreadable; physical file reclamation depends on Badger maintenance/compaction. TTL is not a disk quota or a deletion guarantee for backups and snapshots. Use a dedicated capacity-limited filesystem or volume quota, monitor free bytes/inodes and write rejection, and budget temporary compaction/backup space. Do not delete database files underneath the running process to make room.

The Compose process ceiling is 512 MiB, with `GOMEMLIMIT=384MiB` and a memory limiter configured for 384 MiB hard/96 MiB spike headroom. The limiter is a pressure control, not a substitute for the container memory ceiling or workload sizing. A one-span Windows native baseline used approximately 43.7 MB RSS. Two tiny retained traces yielded about 2.28 GB of logical database file lengths because Badger preallocates files; that is not a measurement of physical allocation. Provision several GiB of dedicated storage headroom even for a small test, then measure representative ingestion, compaction and restart workloads before choosing production capacity.

Align the Collector's bounded batches and attribute/event limits with the 4 MiB final receiver limit. A request rejected as too large is not repaired by retrying the same body. Collector queue capacity, storage pressure, discarded data and retry exhaustion need explicit operational ownership.

For maintenance, stop the single writer before a filesystem-consistent backup, retain its matching configuration/version, and rehearse restore on an isolated volume. Replication, failover, online backup, disaster recovery, storage access controls and compliance retention are operator responsibilities. A BYO distributed store must verify write/read credentials, TLS, index lifecycle/retention, backup/restore and actual trace readback; selecting a supported backend alone does not prove those controls.

## Grafana compatibility boundary

Jaeger 2.23 serves native service discovery and search through `/api/v3/services` and `/api/v3/trace-summaries`. It retains direct trace-ID readback. Grafana's pinned Jaeger **13.0.8** plugin uses the v3 gateway for health, service/operation discovery and trace-ID queries when `jaegerEnableGrpcEndpoint` is enabled. Despite that flag's name, these calls use HTTP on `16686`, not the gRPC listener.

That plugin still sends its Search query to the removed legacy `/api/traces` endpoint. Full Grafana Jaeger Search is therefore unsupported in this reference. Use direct trace-ID/log links in Grafana and the private native Jaeger UI for discovery/search. The [pinned plugin query implementation](https://github.com/grafana/grafana-jaeger-datasource/blob/v13.0.8/pkg/jaeger/querydata.go) makes that distinction explicit. Do not describe a healthy datasource or working trace-ID lookup as proof that every search mode works.

## Executable verification

After independently verifying the release archive checksum, run:

```sh
JAEGER_TEST_BINARY=/absolute/path/jaeger-2.23.0-linux-amd64/jaeger \
  python -m unittest discover -s scripts -p test_jaeger_persistence.py

python scripts/ops/verify_jaeger_persistence.py \
  --jaeger-binary /absolute/path/jaeger-2.23.0-linux-amd64/jaeger
```

The seven tests include native schema validation and rejection of an unknown storage key. The real-binary proof derives a private loopback configuration from the reference, writes a synthetic trace, forcibly kills the process after acknowledgement, restarts against the same files, and verifies trace-ID retrieval, native service discovery/search and continued ingestion. A separate disposable three-second TTL store proves read expiry. It reports the known legacy Grafana Search limitation rather than treating it as a successful feature. No application, homelab service or production trace is touched.

The local Windows release proof passed all seven cases. The joint Loki/Grafana proof can reuse `running_jaeger` from the same helper to compare an actual Grafana trace-ID result with the correlated synthetic Loki log. Container admission, Linux volume ownership, filesystem quotas, restart of the complete Compose profile, representative throughput, retention reclamation and site-specific backup/restore still need deployment evidence. Native binary tests do not substitute for those checks.
