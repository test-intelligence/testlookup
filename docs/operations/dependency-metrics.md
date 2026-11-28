# Dependency resource metrics

The application exposes Redis/Valkey memory through the existing private API `/metrics` endpoint. The `redis` operational collector reads `INFO memory` from the actual application client (`REDIS_URL`) and broker client (`CELERY_BROKER_URL`). This needs no additional exporter image in Compose, Kubernetes, K3s, OpenShift or disconnected installations. The profile's Prometheus/PodMonitor and scrape authentication must still be configured.

## Redis and Valkey signal contract

Each series carries a bounded `store_role`: `application` or `broker`. A role identifies which configured connection was observed. It does not identify a distinct physical server: URLs differing only in logical database can share server memory. Queries use `max` across redundant API observations and preserve role/installation scope. Do not sum roles or replicas. URLs, credentials, database numbers, key names and project IDs are never metric labels.

| Metric | Unit and meaning |
|---|---|
| `redis_memory_used_bytes` | Allocator memory reported by `used_memory`. |
| `redis_memory_max_bytes` | Reported `maxmemory`; zero explicitly means no dataset eviction limit. |
| `testlookup_redis_memory_not_counted_for_evict_bytes` | Reported replication/AOF buffers excluded from the eviction calculation. |
| `testlookup_redis_memory_counted_for_evict_bytes` | `max(used_memory - mem_not_counted_for_evict, 0)`. |
| `testlookup_redis_memory_rss_bytes` | Process RSS; this is not a container memory limit. |
| `testlookup_redis_memory_limit_configured` | 1 for positive `maxmemory`, otherwise 0. |

The reader validates every required field before updating either role. ACL denial, malformed/missing fields, connection failures and deadline expiry fail `testlookup_metric_collection_success{collector="redis"}` without advancing its last-success timestamp. The previous resource samples remain diagnostic history; supplied alerts and dashboard panels exclude samples without same-target success and freshness within 120 seconds. An unreadable limit is not interpreted as an unlimited limit or healthy zero.

`TestLookupRedisMemoryHigh` watches fresh eviction-counted utilization above 85% for five minutes. `TestLookupRedisMemoryLimitMissing` watches an explicitly unlimited limit for ten minutes. A legitimate managed-service capacity policy can require an explicitly scoped exception plus its provider's equivalent capacity alert. A missing observation still needs investigation; do not silence collection failure merely because no resource samples were emitted.

The backend performs only `INFO memory` for these signals. It does not call `CONFIG`, inspect application keys or mutate server state. Existing app credentials/TLS and the single-attempt Redis client policy apply; broker and application clients are shared when their configured URLs match and are closed by the existing Redis shutdown lifecycle. If the app ACL currently denies `INFO`, permit that command deliberately or use an approved external monitoring integration. A dedicated external exporter account should have only the read/connection commands its selected exporter requires; it does not need application key or configuration-write access just to read memory. Protect exporter credentials through the platform secret mechanism and keep operational endpoints private.

Redis documents both the [INFO memory fields](https://redis.io/docs/latest/commands/info/) and [maxmemory/eviction semantics](https://redis.io/docs/latest/develop/reference/eviction/). `maxmemory=0` is unlimited; replication/AOF buffers need separate headroom. Do not replace this value with `total_system_memory`: host RAM is not a reliable container capacity. Use platform container/host memory telemetry for RSS-versus-container-limit alerts. Apply least privilege through the [Redis ACL model](https://redis.io/docs/latest/operate/oss_and_stack/management/security/acl/).

## Profile and external-exporter responsibilities

| Profile | Required built-in path | Additional infrastructure responsibility |
|---|---|---|
| Source/release Compose | API `/metrics`, actual configured Redis roles, `redis` collector freshness, supplied memory alerts. | The optional site profile scrapes selected host and PostgreSQL exporters over mutual TLS. The platform supplies CPU, workload restarts and data-volume capacity as applicable. |
| Kubernetes/K3s/OpenShift | Selected API PodMonitor, allowed namespace/network path, correct scrape credentials and loaded generated rules. | Reuse node-exporter/kube-state-metrics or approved platform equivalents. Their presence and actual samples must be verified. |
| Managed/BYO Redis | Built-in `INFO memory` when supported and authorized. | For clustered/managed services, verify all required primaries/shards and provider-specific memory limits. A single app connection is not evidence of whole-cluster capacity. |
| Disconnected | Same native reader and locally available metrics/routing; no monitoring downloads introduced by this producer. | Any optional external exporter must be pinned, scanned, mirrored and included in the installation inventory. |

An external Redis exporter may coexist in a separate scrape job for node, replication and provider-specific coverage. Its similarly named `redis_memory_*` metrics do not automatically satisfy TestLookup's native `store_role`, freshness and job contract. The supplied memory rules explicitly select `job="testlookup-backend"`; provide a separately tested adapter/rule for the external job. Do not relabel an arbitrary server as the application's broker or merge native and external samples into one total. PostgreSQL exporters likewise do not substitute for Python connection-pool observations.

The optional host and PostgreSQL profile declares its expected targets separately from discovery and checks that healthy scrapes contain the required samples. [Infrastructure exporters](infrastructure-exporters.md).

Acceptance requires actual target discovery, nonempty required series, a controlled threshold crossing/recovery on disposable storage, and denied/unavailable `INFO` yielding failed freshness rather than healthy memory. Run:

```sh
cd backend
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest -p pytest_asyncio.plugin tests/regression/test_redis_resource_metrics.py tests/regression/test_grafana_dashboard_metrics_are_declared.py
cd ../infra/monitoring/tests
promtool test rules redis-resources.yml
```

The Python suite includes a local synthetic RESP server to exercise real redis-py parsing and distinct/shared connection authorities. It changes no production Redis configuration. The profile-specific real-server capacity and host-exporter drill remains a separate release acceptance obligation.
