# Disposable release signal gate

`scripts/monitoring/release_signal_gate.py` checks a candidate against a disposable monitored Compose stack. The orchestrator provisions pinned images and synthetic credentials, records runtime image inspection, runs the checker, retains sanitized evidence and removes the entire stack. Production and shared application targets are outside this gate's write scope.

The checker uses ordinary application APIs. It submits one two-test JSON batch with `run_ai: false`, executes a read-only broker probe on `default` and `agent_children`, and queries Prometheus, Loki and Jaeger. It sends no synthetic records directly to the log or trace backends. A dedicated project with a `release-signal-` slug is required before the batch is accepted. Configure that fixture with no outbound integrations.

## Candidate and target contract

Use the existing immutable release manifest. Generate runtime proof with `scripts/monitoring/inspect_compose_candidate.py` immediately before running the checker. The inspection verifies the uniquely named disposable project's private networks, running API, primary worker, child worker and MCP containers, configured candidate references and actual OCI index/platform/config digest identities. Its result is trusted local inspection evidence.

The proof has schema version `1`, `disposable: true`, profile `compose`, the manifest's `source_sha`, an aware `observed_at` timestamp, the synthetic `project_id`, and exactly four `roles`. Each role contains `role`, `component`, `candidate`, `running`, and `verified_runtime_digests`. A running config digest is accepted when the inspector has bound it to the candidate's raw OCI descriptors and included it in that list. The checker requires an exact candidate reference, matching source/project/profile, matching verified runtime digest and inspection age of at most ten minutes.

Set these variables in the disposable runner:

| Variable | Purpose |
|---|---|
| `TESTLOOKUP_DISPOSABLE_SIGNAL_GATE=1` | Explicitly selects the authorized disposable write lane. |
| `TESTLOOKUP_API_URL` | API base origin. |
| `TESTLOOKUP_PROMETHEUS_URL` | Private Prometheus query API origin. |
| `TESTLOOKUP_LOKI_URL` | Private Loki query API origin. |
| `TESTLOOKUP_JAEGER_URL` | Private Jaeger query API origin. |
| `TESTLOOKUP_MCP_URL` | MCP base origin for an operational request marker. |
| `TESTLOOKUP_API_KEY` | Synthetic project-bound key with ingest and run-read permission. |
| `TESTLOOKUP_SIGNAL_PROJECT_ID` | UUID of the dedicated preseeded project. |
| `OBSERVABILITY_INSTALLATION` | The same explicit installation on API, workers and MCP resources. |
| `APP_ENV` | Matching `development`, `staging` or `production` fixture environment. |
| `CELERY_BROKER_URL`, `CELERY_RESULT_BACKEND` | Private fixture broker and result backend. |

URLs contain only an HTTP(S) origin. Credentials belong in the separate API-key variable. The HTTP client disables ambient proxies and redirects and bounds response sizes. The reference recipe uses private Compose HTTP endpoints. A hosted or cross-trust TLS/authenticated query deployment needs its own reviewed runner transport configuration and acceptance.

The candidate reports `BUILD_REVISION` equal to the manifest SHA and normal `APP_VERSION` equal to its semantic version without `v`. Operational resources retain `service.version`, `testlookup.build.revision`, `testlookup.installation`, `testlookup.service.role` and environment. The Collector validates and preserves these fields. Loki stores build/installation/role as structured metadata rather than index labels.

Run from a runner with the backend's locked Celery dependencies:

```sh
python scripts/monitoring/release_signal_gate.py \
  --manifest /proof/release-manifest.json \
  --runtime-proof /proof/runtime-proof.json \
  --out /proof/release-signal-proof.json
```

The existing `backend/tests/integration/test_metrics_fleet_runtime.py` keeps its ordinary positive fleet test. Set `TESTLOOKUP_FULL_SIGNAL_GATE=1`, `TESTLOOKUP_SIGNAL_MANIFEST` and `TESTLOOKUP_SIGNAL_RUNTIME_PROOF` to execute the same checker from that integration lane. Ordinary unit runs skip these deployed-stack cases.

## Progressive acceptance

The checker requires every stage:

1. Verify API build/version/environment, dedicated fixture project and the expected healthy API/worker scrape role inventory. Scope every legacy metric query to those exact jobs and instances.
2. Wait for a scrape after the provenance request, then observe exactly five new `/health/version` requests. A counter reset or overshoot fails the proof.
3. Execute existing registered `app.worker.celery_app.queue_delivery_probe` once on each worker queue. Require both receipts to echo the synthetic correlation ID, intended routing key and a consumer hostname, exactly two successful task-counter increments and two runtime observations. This replaces the old unregistered `celery.ping` task name. Celery control ping remains a different mechanism.
4. Submit the normal batch with a fresh client request ID and W3C trace context. Require the echoed ID and valid accepted run/task identities. Poll a fresh run read until both expected normalized results are durable, with no rejected rows. Require the three staged notification/transition/webhook operation intents and every operation's explicit durable completion. Pending, published, processing, failed and unknown states cannot pass through a Celery result alone.
5. Retrieve the candidate-qualified trace and actual API/primary-worker/child-worker spans. Retrieve the ingestion API access log, both probe execution logs and ingestion-worker execution log through their hashed identifiers. Each log must carry the same client trace ID and an actual span ID from the corresponding qualified role. Wrong build, missing child trace or missing receiver data fails.
6. Issue a harmless missing-path request to MCP, require its 404 and correlation echo, and retrieve its candidate-bound operational log. This stage verifies the MCP logging path. MCP tool authorization and browser journeys have separate functional gates.
7. Require every expected operational collector series on each API target, successful collection and last-success age at most 120 seconds. Independently check the actual SQL journey snapshot timestamp, because a successful cached scrape preserves its original database observation time.

The result stores source/profile, manifest and runtime-proof hashes, synthetic trace ID, hashed client/run identifiers, completed stage names and fixed failure codes. It excludes API keys, broker URLs, raw operational responses, exception messages and log/trace payloads. Preserve the runtime proof beside the result so reviewers can inspect the observed image identities. A failed run retains its completed stage list and exits nonzero.

Firing/resolved notification delivery, a complete browser/MCP tool journey, cluster rollout behavior and restore verification have separate required evidence. A source-level checker or fixture test does not establish that a candidate completed this live disposable-stack gate. Record the actual manifest and execution result when it runs.

## Worker trace boundary

The opt-in telemetry Compose profile configures worker trace endpoints. The Kubernetes telemetry-client component explicitly enables worker tracing and supplies the existing mTLS client identity. Base profiles retain their existing tracing defaults.

`app.worker.tracing` initializes the SDK from `worker_process_init` in a standalone Celery prefork child. A PID guard avoids repeated setup. Publication propagates only canonical W3C `traceparent`. Baggage and tracestate are removed from broker propagation, while unrelated business headers are preserved. Consumer spans use the fixed name `celery.consume` and bounded queue/status attributes. They contain no arguments, payloads, task IDs or exception text. Task execution logs run before the span closes so their hashed task identities link to that span. Success, failure, retry and eager execution restore the caller's prior context.

A standalone Celery parent must start without an already initialized API tracer. This is the shipped process topology. Initializing an API SDK and then launching a worker pool in that same process needs separate fork/exporter lifecycle validation. Hard-killed executions can lose unfinished spans. Durable business recovery and outcome accounting continue to use their SQL state.

Run focused local checks with plugin autoload disabled where needed:

```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD = '1'
python -m pytest -q scripts/monitoring/tests/test_release_signal_gate.py
cd backend
python -m pytest -q -p pytest_asyncio.plugin tests/test_worker_tracing.py
```

The fixtures exercise known successful joined evidence and rejected missing-role, missing-trace, missing-log, stale-collector, failed-business-state, wrong-build and exact-delta cases. Worker tests use a real local SDK exporter and real eager Celery signal execution. A provisioned prefork monitored stack remains a distinct runtime proof.
