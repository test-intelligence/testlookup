# PostgreSQL application pool metrics

API processes and Celery prefork children each own an independent SQLAlchemy async connection pool. `app.db.postgres.get_engine()` uses the instrumented `AsyncAdaptedQueuePool` while retaining the existing cached engine, worker event loop, connection settings and connection budget. No extra exporter or database query is required for these measurements.

Series have one bounded application label, `role` (`api`, `worker` or `operation`). Scrape labels still identify the deployment and target. Connection strings, credentials, SQL, tenant IDs and process IDs are never application metric labels.

| Metric | Meaning and aggregation inside one target |
|---|---|
| `sqlalchemy_pool_checked_out` | Sum of SQLAlchemy's current checked-out counts. |
| `sqlalchemy_pool_size` | Sum of configured persistent pool sizes. |
| `sqlalchemy_pool_max_overflow` | Sum of configured finite overflow allowances. |
| `sqlalchemy_pool_capacity` | Sum of configured finite `pool_size + max_overflow`. |
| `sqlalchemy_pool_capacity_limited` | Number of initialized pools with a finite capacity. |
| `sqlalchemy_pool_overflow` | Sum of currently open connections above persistent size; cold-pool negative overflow is clamped to zero. |
| `sqlalchemy_pool_utilization_ratio` | Maximum live process's checked-out/capacity ratio. |
| `sqlalchemy_pool_acquiring` | Sum of acquisition attempts in progress, including queue wait, connection creation and pre-ping. |
| `sqlalchemy_pool_acquire_seconds{outcome}` | Histogram of one public acquisition attempt, including queue wait, creation and pre-ping. Outcomes are `success`, `timeout` and `error`; cancellation is an error. |
| `sqlalchemy_pool_timeouts_total` | SQLAlchemy queue-capacity timeouts (code `3o7r`), counted once per public attempt. |

The engine is lazy: only processes that have initialized a pool contribute capacity. A pool's configuration remains visible while it is idle; these gauges are not a count of currently established server connections. Overflow connections count toward finite capacity even though they are discarded when the persistent queue is full on return. [SQLAlchemy documents these async queue and overflow semantics](https://docs.sqlalchemy.org/en/20/core/pooling.html).

Use the maximum process ratio for pressure alerts. Dividing summed checked-out connections by summed capacity answers a different question and can hide a fully occupied child behind idle siblings. `TestLookupDBPoolExhausted` fires when a target's maximum ratio exceeds 90% for two minutes, including worker targets. Acquisition latency is not query latency or pure queue-wait latency. The timeout counter preserves shorter exhaustion events that may not satisfy the alert's two-minute duration.

The Celery parent checks for dead PIDs in its private metrics directory on the existing file-guard interval. It removes confirmed dead-child live gauges and retains counters and histograms. This covers a hard death that skips the child shutdown signal.

The overview dashboard's PostgreSQL application pools row plots the busiest live process per API/worker target and five-minute timeout increases grouped by installation and role. Its pressure panel excludes unbounded pools instead of displaying them as zero percent utilization. Prometheus `increase` estimates the change from scraped samples; it is not an exact event ledger.

The application validates positive persistent size and nonnegative overflow. Defensive support for an unbounded raw SQLAlchemy pool reports capacity, limited-capacity count and utilization as zero; zero capacity is unknown/unbounded, never a denominator for percentage pressure. An absent series also does not prove health: check required target discovery and the initialized workload. Fleet connection-budget validation remains necessary before increasing limits.

Live gauges use the existing per-container multiprocess directory and dead-child cleanup. Counters and histograms retain completed observations across child recycling. Engine disposal creates a replacement pool with the same instrumentation; a late checkin from the detached pool cannot overwrite the replacement's pressure. This contract covers the application's single cached engine per process and role, rather than arbitrary additional independently constructed engines with the same role.

Run the deterministic real async-queue and multiprocess tests without services:

```sh
cd backend
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest -p pytest_asyncio.plugin tests/regression/test_db_pool_metrics.py
cd ../infra/monitoring/tests
promtool test rules pool-alerts.yml
```

For release acceptance against an isolated PostgreSQL integration database, set `TESTLOOKUP_POSTGRES_TEST_DSN` and run `tests/integration/test_db_pool_metrics_postgres.py`. It holds two disposable read-only connections, verifies actual checkout/capacity/overflow, induces one bounded third-acquisition timeout, and verifies recovery. A monitored disposable profile must additionally prove API and worker scrape discovery and alert firing/resolution. Local async SQLite, multiprocess and synthetic PromQL tests do not substitute for that deployment evidence.
