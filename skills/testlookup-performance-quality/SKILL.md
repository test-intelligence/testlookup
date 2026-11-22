---
name: testlookup-performance-quality
description: Review and improve TestLookup performance evidence and regressions. Use for browser load and request costs, API and query latency, ingest completion time, render budgets, and profiling.
---

# TestLookup Performance Quality

## Set up a comparable measurement

- Choose the user action or asynchronous milestone and its existing budget. Record baseline and candidate SHA, build mode, target profile, flags, dependency mode, dataset size and shape, workload, concurrency, test window and warm or cold state. Use the same conditions for both runs. If a budget is missing, report the measured delta and a proposed acceptance threshold.
- Use `architecture/OBSERVABILITY.md`, `backend/app/core/metrics.py` and `infra/monitoring` to locate stage timings. Measure acceptance, queue wait, processing, durable completion and client visibility as applicable. Use query counts and plans, payload sizes, worker timing, resource usage or browser traces to localize the bottleneck.
- Compare equivalent results and report attempted and completed work, refusals, timeouts and errors. Include sample count, throughput, suitable percentiles and resource or queue signals. Record changes in workload or instrumentation and repeat noisy comparisons before proposing a conclusion.

## Select evidence and acceptance

- Use deterministic guards for request count, bounded payload, pagination, query count and wait behavior. See `frontend/tests/ci-e2e/perf-hot-pages-request-inventory.spec.ts`, `backend/tests/test_perf_size_bounds_ratchet.py` and `backend/tests/integration/test_perf_runs_statement_counts_postgres.py`. Use representative store fixtures for claims about query plans or pool contention.
- Measure UI paint with a production build and quiet host. From `frontend`, run `npx playwright test --config playwright.perf.config.ts` to record LCP. Keep browser, viewport and fixtures consistent. Use the harness output for timing comparisons and select separate deterministic CI assertions for the changed behavior.
- For runtime or load evidence, inspect `backend/scripts/load_test_harness.py` and `backend/scripts/load_test_concurrent.py` before use. Run within a bounded authorized disposable environment and stop on its agreed error or saturation limit. Keep payload, data shape and dependency mode representative.
- Prioritize the change that addresses the measured limiting stage. Check result equivalence and remeasure after the change. Report the user journey, baseline and candidate values, budget verdict, throughput and error impact, confidence, and the test or benchmark that can catch a regression.
