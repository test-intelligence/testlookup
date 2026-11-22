---
name: testlookup-observability-quality
description: Review and improve TestLookup operational telemetry. Use for Prometheus metrics, Grafana dashboards and alerts, OpenTelemetry traces, structured logs, health probes, and fleet-wide signal correctness.
---

# TestLookup Observability Quality

## Trace the signal

- Read `architecture/OBSERVABILITY.md` and the rendered monitoring profile. Trace the signal through `backend/app/core/metrics.py`, `backend/app/core/tracing.py`, `backend/app/core/logging_config.py` or `backend/app/routers/health.py` as applicable. Locate its producers in the API, workers or scheduler and its destination in `infra/monitoring` or `k8s/monitoring`.
- Define the operator question, signal name, unit, event boundary, expected change and relevant scope. Check label stability and cardinality, sensitive data handling and correlation across one request or job. Inspect readiness, scrape and exporter behavior when a dependency or collector is absent.
- Check dashboard and alert expressions alongside the instrument. Verify grouping, counter resets, histogram buckets, zero activity, missing targets and rollouts. Derive expected fleet totals from the topology and distinguish process-local metrics from metrics emitted by a single shared collector.

## Verify and hand off

- Use `backend/tests/regression/test_grafana_dashboard_metrics_are_declared.py` for dashboard metric references and `backend/tests/test_prometheus_settings.py` for the configuration contract. For an alert change, exercise a normal interval, a threshold crossing and recovery with synthetic input. Verify collection or alert evaluation in the target profile when that is part of acceptance.
- For fleet totals or scrape wiring, read the fixture requirements in `backend/tests/integration/test_metrics_fleet_runtime.py`. Compare a known synthetic workload with signals from the relevant API and worker processes in a disposable deployment. For trace or logging changes, check the authorized exporter or sanitized local sink for the expected fields.
- Record the build and profile, actual scrape targets, sample or time window, observed signals and affected alert or dashboard. Link the performance skill for measured budgets and the auditability skill for durable decision provenance.
