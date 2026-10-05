# Visualization performance baseline (Phase D L2/L3)

This record is kept under `architecture/` so it ships with the source tree; the
repository-wide `docs/` ignore rule is for local working notes. It is the
evidence behind the visualization latency budgets codified in
`backend/app/services/performance_budgets.py` (Phase D L3), and the baseline a
later run should be compared against.

Raw run output (local working notes, not tracked): `docs/viz-work/l2-matrix.md`
(+ `l2-matrix.json`) and `docs/viz-work/l2-burst.md`. Every figure below is
copied from those files; nothing here is extrapolated.

## Budgets this run was held to (epic §5.5)

| Path | p95 | `LATENCY_BUDGETS` key | Observable as |
|---|---:|---|---|
| Count / rate chart-data miss, up to 90 d | 500 ms | `chart_data_rate` | harness only (shares chart-data's route) |
| Percentile chart-data miss (`duration_p95`) | 1,500 ms | `chart_data` | `handler="/api/v1/analytics/chart-data"`, alert `TestLookupSLOChartDataSlow` |
| Heatmap miss | 800 ms | `analytics_heatmap` | `handler="/api/v1/analytics/heatmap"`, alert `TestLookupSLOAnalyticsHeatmapSlow` |
| Rows, page 1 | 300 ms | `chart_rows` | `handler="/api/v1/analytics/chart-data/rows"`, alert `TestLookupSLOChartRowsSlow` |
| Cached read (any cacheable route) | 50 ms | none (`VIZ_CACHED_BUDGET_MS` in the harness) | harness only: a hit and a miss share one route |

`chart_data` is the route-level ceiling: the HTTP histogram cannot tell a
count/rate read from a percentile read on the same route, so the alert and
dashboard panel use 1.5 s and the tighter 500 ms is enforced by the harness.

## Environment

- **Date:** 2026-10-04. Matrix started 18:07:09 UTC; burst started 18:34:08 UTC.
- **Deployed revision:** commit `d657ef45`, homelab K3s cluster.
- **Dataset:** `seed_large_dataset.py --scale large` — 5,000 runs,
  1,000,000 test cases, 20 releases, 200 suites, in project
  `synthetic-perf-dataset` (the harness discovered 21 releases and 200 suites
  on that project). The seed was removed after the run.
- **Pod sizing:** 1 backend pod × 4 worker processes, database pool of 12
  connections. The per-principal rate limiter is held in process memory, so a
  pod's real ceiling is up to 4× the per-process limit.

## Method

- **Harness:** `backend/scripts/load_test_concurrent.py bench --suite viz`
  (Phase D L1), concurrency levels 1, 4, 8, 16; cache modes cold and warm;
  10 requests per worker per cell (`n` = 10 × concurrency).
- **Transport:** `kubectl -n testlookup port-forward svc/testlookup-backend
  18000:8000`; base URL `http://127.0.0.1:18000`. No database or Redis port was
  touched.
- **Cache modes:** *cold* sends a distinct cache key on every request (a miss);
  *warm* repeats one key (a hit after the first request).
- **Principals:** 9 existing users, one bearer token each from `--tokens-file`,
  rotated across workers so the per-principal rate limit is not what is being
  measured.
- **Server truth:** `--metrics-url http://127.0.0.1:18000/metrics`. The harness
  scrapes `testlookup_analytics_query_duration_seconds{route,outcome}` before
  and after each cell and computes the p95 from the bucket delta, the way
  Prometheus's `histogram_quantile` does. Note that this is the analytics
  read-layer histogram (it includes the cache lookup), not the
  `http_request_duration_seconds` histogram the SLO alerts read, so the alert
  series also carries middleware and serialization time.
- **Verdicts** in the raw matrix compare the **client** p95 with the budget.

## Results: single user (concurrency 1)

Times in ms. Client figures include the port-forward; server figures are the
histogram p95 for that cell.

| Scenario | Cold budget | Cold client p95 | Cold server p95 | Warm client p95 | Warm server p95 (hit ratio) | Cold verdict |
|---|---|---:|---:|---:|---:|---|
| `viz_chart_pass_rate_day_30d` | chart_data_rate 500 | 100.0 | 75.0 | 10.3 | 9.5 (0.917) | PASS |
| `viz_chart_pass_rate_day_90d` | chart_data_rate 500 | 26.9 | 40.0 | 8.7 | 9.5 (0.917) | PASS |
| `viz_chart_day_suite_top7_90d` | chart_data_rate 500 | 202.2 | 242.5 | 16.8 | 160.0 (0.917) | PASS |
| `viz_chart_2releases_2suites_90d` | chart_data_rate 500 | 181.2 | 242.5 | 11.9 | 160.0 (0.917) | PASS |
| `viz_chart_duration_p95_90d` | chart_data 1500 | 237.7 | 242.5 | 13.5 | 160.0 (0.917) | PASS |
| `viz_chart_failures_by_suite_90d` | chart_data_rate 500 | 142.5 | 242.5 | 12.3 | 160.0 (0.917) | PASS |
| `viz_heatmap_suite_day_90d` | analytics_heatmap 800 | 248.1 | 242.5 | 56.8 | 160.0 (0.917) | PASS |
| `viz_heatmap_suite_environment_30d` | analytics_heatmap 800 | 101.4 | 97.5 | 11.7 | 70.0 (0.917) | PASS |
| `viz_heatmap_suite_release_90d` | analytics_heatmap 800 | 159.8 | 242.5 | 12.7 | 160.0 (0.917) | PASS |
| `viz_heatmap_test_run_suite` | analytics_heatmap 800 | 71.7 | 75.0 | 14.7 | 26.0 (0.917) | PASS |
| `viz_coverage_map_root` | none | 144.9 | 225.0 | 13.1 | 160.0 (0.917) | n/a |
| `viz_coverage_map_suite` | none | 130.7 | 212.5 | 8.5 | 70.0 (0.917) | n/a |
| `viz_failure_groups_edges_30d` | none | 75.1 | 87.5 | 12.9 | 26.0 (0.917) | n/a |
| `viz_test_scatter_30d` | none | 141.2 | 97.5 | 31.6 | 70.0 (0.917) | n/a |
| `viz_chart_rows_page1` | chart_rows 300 | 26.3 | 9.5 | 11.0 | 9.5 (0.917) | PASS |
| `viz_explorer_discovery` | chart_data_rate 500 | 115.8 | 97.5 | 14.0 | 70.0 (0.917) | PASS |
| `viz_legacy_coverage_90d` | none | 479.4 | 487.5 | 9.2 | 700.0 (0.917) | n/a |
| `viz_legacy_metrics_summary` | none | 119.5 | - | 6.2 | - | n/a |

- **Every budgeted cold read met its budget for one user**, on the server and
  on the client.
- **Warm server p95 at concurrency 1 is not the hit cost.** Hit ratio 0.917 is
  11 hits and 1 miss (the request that fills the cache), and with 12 samples
  that one miss sets the bucket p95 (the 160 / 70 / 700 ms values). At
  concurrency 4, where hits dominate, the warm server p95 of every cacheable
  scenario was 9.5-9.7 ms.
- `viz_legacy_metrics_summary` is outside the analytics read layer, so it has
  no server series. `viz_heatmap_suite_day_90d` warm, at 56.8 ms client p95,
  was the one single-user cached read above 50 ms.

## Results: concurrency (cold, 90-day heavy reads)

Server p95 in ms / throughput in requests per second, per scenario cell.

| Scenario (cold, 90 d) | c=1 | c=4 | c=8 | c=16 |
|---|---:|---:|---:|---:|
| `viz_chart_pass_rate_day_90d` | 40.0 / 20.4 | 473.7 / 13.7 | 960.8 / 8.7 | 951.8 / 8.4 |
| `viz_chart_day_suite_top7_90d` | 242.5 / 4.7 | 486.8 / 7.6 | 960.8 / 8.3 | 1652.2 / 7.8 |
| `viz_chart_2releases_2suites_90d` | 242.5 / 4.9 | 466.7 / 9.9 | 478.7 / 10.1 | 496.1 / 10.3 |
| `viz_chart_duration_p95_90d` | 242.5 / 3.9 | 472.2 / 9.8 | 961.5 / 8.8 | 936.5 / 8.6 |
| `viz_chart_failures_by_suite_90d` | 242.5 / 6.2 | 482.1 / 9.2 | 971.8 / 8.4 | 1782.4 / 7.4 |
| `viz_heatmap_suite_day_90d` | 242.5 / 3.8 | 500.0 / 7.5 | 973.7 / 8.2 | 1905.9 / 7.8 |
| `viz_heatmap_suite_release_90d` | 242.5 / 6.1 | 486.8 / 9.9 | 959.2 / 9.9 | 1636.4 / 9.3 |
| `viz_legacy_coverage_90d` | 487.5 / 2.1 | 972.2 / 5.4 | 1911.1 / 5.6 | 1902.4 / 5.7 |

Client p95 (ms) for the same cells, including the port-forward:

| Scenario (cold, 90 d) | c=1 | c=4 | c=8 | c=16 |
|---|---:|---:|---:|---:|
| `viz_chart_pass_rate_day_90d` | 26.9 | 631.4 | 1975.2 | 4161.9 |
| `viz_chart_day_suite_top7_90d` | 202.2 | 1188.0 | 2184.1 | 4361.1 |
| `viz_chart_2releases_2suites_90d` | 181.2 | 1030.4 | 1772.7 | 3608.8 |
| `viz_chart_duration_p95_90d` | 237.7 | 972.6 | 1958.5 | 3617.5 |
| `viz_chart_failures_by_suite_90d` | 142.5 | 1063.6 | 1783.7 | 4265.4 |
| `viz_heatmap_suite_day_90d` | 248.1 | 1254.8 | 2115.7 | 4572.0 |
| `viz_heatmap_suite_release_90d` | 159.8 | 644.5 | 1291.8 | 2815.1 |
| `viz_legacy_coverage_90d` | 479.4 | 1429.1 | 2941.9 | 5172.2 |

Throughput stops growing after c=4. The 90-day chart and heatmap reads ran at
7.4-10.3 rps per scenario at c=8 and c=16. Over the same steps their server
p95 roughly doubled, from 466.7-500.0 ms at c=4 to 959.2-973.7 ms at c=8. The
one exception was `viz_chart_2releases_2suites_90d`, which stayed at
478.7-496.1 ms. At c=16, four of them reached 1.6-1.9 s. Latency that rises
while throughput stays flat is queueing, not slower queries.

### Run-wide server totals (all cells, from `/metrics`)

| Route | Miss count | Miss p95 ms | Hit count | Hit p95 ms | Hit ratio | Timeouts | Errors |
|---|---:|---:|---:|---:|---:|---:|---:|
| `/api/v1/analytics/chart-data` | 2067 | 967.6 | 2429 | 9.8 | 0.54 | 0 | 0 |
| `/api/v1/analytics/heatmap` | 1165 | 1455.6 | 1388 | 80.8 | 0.544 | 0 | 0 |
| `/api/v1/analytics/chart-data/rows` | 280 | 80.0 | 358 | 9.6 | 0.561 | 0 | 0 |
| `/api/v1/analytics/coverage-map` | 582 | 948.9 | 694 | 9.8 | 0.544 | 0 | 0 |
| `/api/v1/analytics/failure-groups` | 292 | 219.2 | 347 | 9.9 | 0.543 | 0 | 0 |
| `/api/v1/analytics/test-scatter` | 291 | 841.8 | 347 | 80.1 | 0.544 | 0 | 0 |
| `/api/v1/analytics/coverage` | 293 | 1884.6 | 347 | 9.8 | 0.542 | 0 | 0 |

- No statement timeouts, no errors, and no degraded reads over the whole run.
- Every matrix request returned 200; there were **no 429s** in the matrix.
- Heatmap and test-scatter **hits** have a p95 of about 80 ms against about
  10 ms for the other routes, so their cached responses cost more to serve.
  Warm `viz_heatmap_suite_day_90d` showed this most: server p95 106.0 ms at c=8
  and 216.1 ms at c=16.

## Rate-limit burst (one principal, chart-data)

- 150 requests sent in 1.27 s against a 120/min per-principal, per-process
  limit.
- Statuses: 120 × 200, 30 × 429. The first 429 was request #121.
- Every 429 carried `Retry-After` (value `60`) and body code `rate_limited`.

## Export flows

| Scenario | Run | Delivery | Final | Total ms | POST ms | Download ms | Job s | Polls |
|---|---:|---|---|---:|---:|---:|---:|---:|
| `viz_export_sync` | 1 | download | completed | 210.8 | 14.2 | 195.6 | - | - |
| `viz_export_sync` | 2 | download | completed | 140.7 | 8.1 | 132.5 | - | - |
| `viz_export_background` | 1 | background | completed | 6189.1 | 39.0 | - | 5.36 | 6 |
| `viz_export_background` | 2 | background | completed | 1029.4 | 14.8 | - | 0.96 | 1 |

The background export, end to end (request, poll until done), took 6.2 s on its
first run.

## Capacity conclusion

- **One user:** every cold visualization read is inside its §5.5 budget
  (server p95 at most 242.5 ms for the budgeted scenarios). Cached reads cost
  about 10 ms on the server.
- **One backend pod (4 processes, pool 12):** the 500 ms count/rate budget holds
  up to about **4 concurrent cold heavy (90-day) readers**: server p95 was
  ~470-500 ms at c=4, ~950 ms at c=8, and up to 1.6-1.9 s at c=16. Cold heavy
  reads level off at 7.4-10.3 rps per scenario on one pod.
- **Scaling lever:** the data points to capacity (more backend pods, or more
  processes and pool connections), not query tuning. One request already costs
  well inside the budget, and the extra latency under concurrency is time spent
  queueing.
- In this run, chart-data's cold server p95 crossed the 1.5 s `chart_data`
  alert threshold only at c=16, in 2 of its 5 scenarios. A sustained firing of
  that alert means a pod is past that level of cold load.

## Caveats

- **Client numbers include `kubectl port-forward` overhead.** The many warm
  "FAIL" verdicts in the raw matrix (client p95 above 50 ms at c≥4) have a
  server p95 of about 10 ms. The forward, not the backend, added the
  difference. Use the server column to judge the backend.
- **Server p95 is only as precise as the histogram buckets** (10, 50, 100,
  250, 500 ms, then 1, 2, 5 and 10 s). Values such as 242.5, 486.8 and 960.8 are interpolated
  inside a bucket, and they include any other traffic on the pod: the run-wide
  totals also show a few requests to `/top-failing`, `/flaky-tests` and
  `/failure-categories`, which no harness scenario sends.
- **One run.** There was one matrix pass and one burst, so there is no repeat
  and no variance estimate. Treat the curve as a shape, not a contract.
- **One synthetic project** of uniform shape. Real data with skewed suite sizes
  may behave differently.
- **Not recorded in these results** (the L2 plan asked for them): peak
  `pg_stat_activity` against the pool, `kubectl top`, `EXPLAIN` for the slowest
  queries, a deliberate 5 s statement-timeout probe and recovery check, and the
  LCP figure. Each one should be a follow-up if it is needed.
