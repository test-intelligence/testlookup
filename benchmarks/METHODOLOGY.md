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
- **Concurrency**: higher concurrency surfaces pool contention. The default `--concurrency 5` is representative of a small team; production deployments should benchmark at their expected concurrency.

### Reproducibility

To reproduce a reference result:

```bash
make clean                # fresh start
make dev                  # boot + auto-seed
make benchmark            # runs classification + throughput
```

The output in `benchmarks/results/` includes the git SHA, Python version, platform, and timestamp so results can be correlated with code changes.
