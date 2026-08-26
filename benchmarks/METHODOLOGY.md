# Benchmark Methodology

## Classification benchmarks

### Dataset

The labelled dataset (`benchmarks/classification/dataset.json`) contains 42 synthetic test-failure cases. Each case has:

- **Input fields**: `error_message`, `test_name`, `duration_ms`, `severity`, optional `history` (pass/fail counts, status sequence), optional `run_context` (suite/cross-suite failure rates)
- **Expected label**: one of `PRODUCT_BUG`, `INFRASTRUCTURE`, `TEST_DATA`, `AUTOMATION_DEFECT`, `FLAKY`, `UNKNOWN`
- **Expected `is_flaky`**: boolean

### Coverage

The dataset exercises:

- All 14 keyword pattern groups in the rules engine (OOM, connection refused, timeout, HTTP 5xx, DNS, TLS/SSL, 404, setup/fixture, null reference, element not found, class not found, flaky/intermittent, assertion, auth)
- Historical flakiness triggers (pass rate 10-90% with mixed history)
- Regression triggers (consecutive passes followed by first failure)
- Duration anomaly triggers (duration 3x+ median)
- Suite-level failure triggers (suite_failure_rate >= 80%)
- Cross-suite blast radius triggers (cross_suite_failure_rate >= 50%)
- Fallback / unknown paths (no error message, empty string, unrecognisable error)

### Metrics

- **Accuracy**: correct predictions / total cases
- **Per-category precision**: TP / (TP + FP)
- **Per-category recall**: TP / (TP + FN)
- **Per-category F1**: harmonic mean of precision and recall
- **Macro precision/recall/F1**: unweighted average across categories with at least 1 support case
- **Latency**: p50 and p95 wall-clock time per classification (single-threaded)

### Limitations

- The dataset is synthetic. Real-world error messages have more variety, misspellings, and multi-language content.
- The dataset is small (42 cases). It's designed for regression detection, not statistically rigorous benchmarking. A production evaluation suite should aim for 500+ labelled cases across diverse codebases.
- The labels reflect the rules engine's intended behaviour. They may disagree with an LLM-based classifier's judgment, especially on ambiguous cases (e.g. a timeout that's actually a product bug).
- The dataset does not exercise the ML classifier's feature extraction path end-to-end. To benchmark ML mode, train a model first (`make train-ml`) and run with `--mode ml`.

### Extending the dataset

Add cases to `benchmarks/classification/dataset.json` in the same schema. Every case needs an `id`, `label`, `is_flaky`, and `error_message`. The evaluator auto-discovers new cases.

Naming convention: `{CATEGORY_PREFIX}-{NN}` (e.g. `INF-11`, `FL-06`).

## Throughput benchmarks

### Harness

The throughput harness (`backend/scripts/load_test_concurrent.py`) is a custom Python script that:

1. Authenticates against the live API
2. Discovers fixture data (project IDs, run IDs) from the database
3. Runs each scenario N times at C concurrency using `asyncio` + `httpx`
4. Collects per-request latency, computes percentiles, and compares against budgets

### Scenarios

8 scenarios cover the critical API paths. Budgets are defined in `backend/app/services/performance_budgets.py` and validated by:

- `tests/test_slo_alerts_match_budgets.py` (keeps Prometheus alert thresholds in sync with code budgets)
- `infra/monitoring/prometheus-rules/testlookup-alerts.yml` (production alerting rules)

### Variables that affect results

- **Hardware**: CPU, RAM, disk IOPS (SSD vs HDD makes a 5-10x difference on Postgres queries)
- **Docker resource limits**: the default Docker Desktop allocation is often 2 CPU / 4 GB — insufficient for realistic benchmarks
- **Seed data volume**: the standard seed produces 3 projects x 30 days x ~30 runs/day. Larger datasets change query plans.
- **Warm-up**: the first few requests populate caches (Redis, in-process). The harness supports a `--warmup` flag.
- **SQL echo**: `make dev` turns it on, and it added ~13% to a measured search request. Unlike the variables above it is invisible — nothing in the output says it is on — so see the section below before trusting any absolute latency number.
- **Concurrency**: higher concurrency surfaces pool contention. The default `--concurrency 5` is representative of a small team; production deployments should benchmark at their expected concurrency.

### SQL echo is ON under `make dev` — it inflates every local latency number

`backend/app/db/postgres.py` builds the engine with `echo=settings.is_development`, and
`scripts/gen-dev-env.sh` sets `APP_ENV=development`. So the stack you get from `make dev` logs
**every statement to stdout**, and that write is on the request's critical path.

Measured on a search request (in-process ASGI, median of 15, 15,780 test cases):

| | with echo (default `make dev`) | echo off |
|---|---:|---:|
| full `/api/v1/search` request | 32.2 ms | 27.9 ms |

That is ~13% of the request. A `cProfile` of the same requests attributed a larger share —
**0.179 s of 0.759 s** — to `logging` → `TextIOWrapper.write` via `sqlalchemy/log.py:info`, but
treat that as a pointer rather than a number: cProfile reports cumulative time under its own
overhead, so it exaggerates. The wall-clock A/B above is the figure to quote.

Either way it is pure measurement overhead. `is_development` is false in any real deployment,
so production never pays it.

**This matters for the procedure below**, which says to run `make dev` and then `make benchmark`.
Those numbers are inflated. A/B comparisons collected the same way stay valid — both arms pay
the same tax — but absolute figures, and any attempt to split "database time" from "framework
time", will be wrong.

To measure without it, set `APP_ENV=production` for the run, or disable the loggers in-process:

```python
for name in ("sqlalchemy.engine", "sqlalchemy.engine.Engine", "sqlalchemy"):
    log = logging.getLogger(name)
    log.setLevel(logging.CRITICAL)
    log.propagate = False
    log.handlers[:] = []
```

**Do that AFTER importing anything that builds the engine.** `create_async_engine(echo=True)`
attaches its own handler and sets the level when the engine is constructed, so suppression
written at the top of a script is silently undone by a later `from app.main import app`. The
symptom is a profile that looks clean while the SQL is still being written.

### Where the time goes in an HTTP measurement

The throughput harness measures over HTTP, so its latency includes uvicorn, the TCP round trip
and the client itself. On the same machine and dataset, one search request measured **~46 ms
over HTTP** but **27.9 ms in-process** — roughly **18 ms is outside the application**.

Worth remembering when a scenario sits near its budget: a p95 close to the threshold may be
measuring the harness as much as the server. Confirm against an in-process measurement before
concluding the endpoint is at fault.

### Reproducibility

To reproduce a reference result:

```bash
make clean                # fresh start
make dev                  # boot + auto-seed
make benchmark            # runs classification + throughput
```

The output in `benchmarks/results/` includes the git SHA, Python version, platform, and timestamp so results can be correlated with code changes.

Note that `make dev` sets `APP_ENV=development`, so a run produced this way carries the SQL-echo
overhead described above. For absolute numbers rather than an A/B comparison, re-run with
`APP_ENV=production`.
