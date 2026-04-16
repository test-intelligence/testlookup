# Throughput Benchmarks

Throughput benchmarks measure end-to-end API latency and request throughput under controlled concurrency. They wrap the existing `backend/scripts/load_test_concurrent.py` harness.

## Running

```bash
# From within the backend container (stack must be running):
make shell-backend
python /app/scripts/load_test_concurrent.py bench \
    --base-url http://localhost:8000 \
    --iterations 50 \
    --concurrency 5 \
    --check-budgets \
    --output /app/benchmarks/results/throughput.json
```

Or via the `make benchmark` target which runs both classification and throughput:

```bash
make benchmark
```

## What it measures

The harness exercises 8 scenarios defined in `backend/app/services/performance_budgets.py`:

| Scenario | Endpoint | p95 budget |
|----------|----------|-----------|
| project_list | GET /api/v1/projects | 200ms |
| run_list | GET /api/v1/runs?size=20 | 250ms |
| run_scoped_guard | GET /api/v1/runs/{run_id} | 250ms |
| run_intelligence_cached | GET /api/v1/runs/{run_id}/intelligence | 800ms |
| keyword_search | GET /api/v1/search?q=... | 300ms |
| flaky_coach | GET /api/v1/projects/{id}/flaky-coach | 600ms |
| health_details | GET /health/details | 2500ms |

Each scenario is warmed up, then run `--iterations` times at `--concurrency` level. The harness reports p50/p95/p99/mean latency and requests per second.

## Interpreting results

- **PASS**: p95 latency is under the budget for every scenario
- **FAIL**: at least one scenario exceeded its p95 budget

Budget violations on a fresh seed dataset with low concurrency usually indicate a missing index or a regression in query planning. Check the Postgres `EXPLAIN ANALYZE` for the offending endpoint.

## Reference machine

Benchmarks should document the hardware they ran on. The reference results in `benchmarks/results/` were produced on:

- Apple M2 MacBook Air, 16 GB RAM, Docker Desktop 4.x
- Docker resource limits: 4 CPU / 8 GB RAM
- Seed data: standard `make seed-data` (3 projects, 30 days of runs)
