# Worker and Beat tracing

The optional [durable telemetry profile](durable-telemetry.md) enables API, worker and standalone Beat traces. The application keeps scheduling and consuming work when the collector is unavailable. Traces describe broker publication and task execution. Durable workflow and delivery results remain in SQL.

| Process | Trace resource role | Initialization boundary | Graceful drain |
| --- | --- | --- | --- |
| API | `api` | Existing API startup | Bounded process-exit fallback |
| Parent-queue prefork child | `worker` | `worker_process_init` after fork | `worker_process_shutdown` |
| Sub-agent prefork child | `worker-children` | `worker_process_init` after fork | `worker_process_shutdown` |
| Standalone Beat | `beat` | Beat service startup before its first publication | Service exit after scheduler cleanup |

Compose source and release installations use `docker-compose.monitoring.yml` followed by `docker-compose.telemetry.yml`. The telemetry overlay sets `OTEL_ENABLED=true` and `OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector:4318` for both workers and Beat. These processes join the private internal ingress network. The base installation keeps its existing opt-in behavior.

The `prod-telemetry`, `k3s-telemetry` and `openshift-telemetry` Kubernetes overlays cover critical, ingestion, AI, default and child worker Deployments and their HPA replicas. Beat uses the same HTTPS gateway and mounts `testlookup-otel-beat-client`. All consumer roles mount `testlookup-otel-worker-client`. Each Secret contains `ca.crt`, `tls.crt` and `tls.key`. Missing credentials leave work available and prevent successful authenticated telemetry delivery. Validate gateway admission and successful export before accepting a site installation.

`OTEL_LOGS_ROLE` also selects the worker trace resource role. The standalone Beat service sets its trace role explicitly. Initialize tracing only after the prefork boundary in worker children. Reference worker parents have no trace provider. Embedded `worker --beat` is outside these reference profiles and has no separate Beat lifecycle guarantee.

## Correlation and privacy

The application wraps Celery's `send_task` boundary in a fixed `celery.publish` producer span. Registered tasks, retries and standalone Beat use this boundary. The wrapper closes the span on publication success and failure and restores the caller's context. A producer span means the publication call ended. It provides no independent proof of durable broker acceptance.

The existing publish signal removes baggage and tracestate and sends a validated W3C `traceparent`. A consumer starts a fixed `celery.consume` span from that parent. Eager execution keeps its current in-process parent. Broker deliveries start from an empty context so unrelated deliveries cannot inherit the previous task's context.

Operational spans contain a finite queue family, operation type and bounded outcome. They omit task names, arguments, task IDs and exception messages. A queue that the boundary cannot identify uses `unknown` or `other`. Automatic Redis instrumentation is suppressed during broker publication because a command span could contain the encoded task body. The manual producer span stays active for propagation. Every exporter uses the existing shared export sanitizer and the collector applies its existing second projection.

## Shutdown and loss budget

The SDK queue is process memory. Its standard bounded defaults are 2,048 spans and batches of up to 512. Site `OTEL_BSP_*` environment settings can tune those bounds. Measure memory, intake and loss before increasing them. Durable queue protection begins after the collector accepts a batch.

The application gives trace shutdown one 1.5-second process-local budget. Subsequent shutdown hooks share its deadline. SDK shutdown runs on a daemon thread because the pinned Python SDK's `force_flush` can block in exporter IO despite a timeout argument. A successful drain sends queued spans and closes the exporter. An unavailable or slow collector can consume the budget and leave spans unsent. The process then exits. Forced termination, hard task limits and OOM kills can lose spans before any graceful hook runs.

Check `trace_shutdown_budget_exceeded` in process logs and inspect collector receiver and queue signals. Increasing a container grace period alone does not extend this trace budget. Keep workflow reconciliation and the independent alert delivery path available during telemetry loss.

## Verification

Run the focused suite from the repository root:

```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'
python -m pytest -p pytest_asyncio.plugin backend/tests/test_worker_tracing.py backend/tests/test_worker_trace_lifecycle.py backend/tests/regression/test_worker_trace_profiles.py backend/tests/test_tracing.py backend/tests/regression/test_trace_export_boundary.py scripts/monitoring/tests/test_telemetry_profile.py -q
```

The profile tests render the three Kubernetes telemetry overlays and both Compose source and release profiles. They check every consumer role, Beat, private transport and client certificate mounts. Kustomize and Docker Compose must be installed for the respective render checks.

`backend/tests/regression/native_worker_tracing.py` is an explicitly disposable Linux PID-1 fixture. It uses current trace modules with synthetic settings, real prefork workers, standalone Beat, a private loopback Valkey and a local OTLP protobuf sink. The proof checks child recycling, task retry parentage, graceful drain, payload absence and a stalled sink's bounded exit. Run it only with `TESTLOOKUP_DISPOSABLE_WORKER_TRACING=1`, a read-only source fixture and an isolated broker. It provides no live installation or full candidate-image acceptance evidence. Follow the separate release signal gate for the selected immutable images.

The lifecycle follows [Celery's process signals](https://docs.celeryq.dev/en/stable/userguide/signals.html), the [Celery Beat service boundary](https://github.com/celery/celery/blob/v5.7.0/celery/beat.py), and the [pinned OpenTelemetry batch processor implementation](https://github.com/open-telemetry/opentelemetry-python/blob/v1.45.2/opentelemetry-sdk/src/opentelemetry/sdk/_shared_internal/__init__.py).
