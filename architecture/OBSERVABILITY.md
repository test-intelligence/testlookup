# Observability architecture

This page explains how TestLookup produces and stores health signals,
operational telemetry, and durable evidence. For deployment choices and
verification steps, see [Monitor a TestLookup installation](../user-guide/observability.md).

## 1. The three signals plus one

| Signal | Where it comes from | Where it goes |
|---|---|---|
| **Metrics** | `core/metrics.py` (prometheus_client) | Prometheus → Grafana (`infra/monitoring`) |
| **Traces** | OpenTelemetry SDK instrumentation for API, Celery, and MCP operations | Sanitized OTLP export through a configured destination. The durable Compose profile uses an OpenTelemetry Collector and Jaeger. |
| **Logs** | API, worker, and MCP structured logging | Process logs. MCP stdio diagnostics use stderr. The optional operational projection sends selected records to the Collector and Loki. |
| **Decisions** | `BaseAgent.log_decision` | `agent_stage_results.decision_log` + OTEL span events + the Decision Trail UI |

## 2. Metrics (`core/metrics.py`)

Domain-shaped Prometheus instruments rather than generic middleware-only
metrics: ingestion (`ingestion_runs_total`, `ingestion_test_cases_total`,
`ingestion_duration_seconds`), uploads (`uploads_total`,
`upload_failures_total`, `upload_processing_seconds`), and the AI layer
(`ai_analyses_total`, …) — so dashboards speak the product's language
("analyses per mode", not just "requests per route").

The monitoring overlay provisions Prometheus, Grafana dashboards, and rule
files from `infra/monitoring/`. The Kubernetes monitoring resources use
Prometheus Operator PodMonitors and rules. Scrape and collector freshness
rules use an expected role inventory independent of discovery. The optional
Alertmanager profile routes incident notifications and watchdog heartbeats
over HTTPS. An operator configures a separate receiver to detect stale heartbeats.
Operators can also configure private host and PostgreSQL exporter scrapes.
[Telemetry contract](../docs/operations/telemetry-contract.md).
[Alert routing](../docs/operations/alert-routing.md).
[Infrastructure exporters](../docs/operations/infrastructure-exporters.md).

### 2a. LLM endpoint circuit breakers

`BudgetedLLM` checks a Redis-backed breaker immediately before provider
admission. State is isolated by the lowercase provider and a normalized base
URL; Redis keys and metric labels carry a stable endpoint digest rather than
the URL itself. Five consecutive availability failures open that endpoint for
120 seconds. One worker then receives a 30-second half-open probe lease; a
successful call closes the circuit and resets its failure sequence.

An open circuit raises before the monthly-cost reservation and cluster
semaphore, so summary and analysis callers enter their deterministic fallback
without waiting on a provider timeout. Authentication, validation, and other
non-availability errors do not count. A Redis bookkeeping failure is logged
and allows the provider call because the breaker store is not the inference
service.

The metrics are:

- `testlookup_llm_circuit_breaker_trips_total`, incremented once per transition
  to open; and
- `testlookup_llm_circuit_breaker_state{provider,endpoint}`, where closed is
  `0`, half-open is `0.5`, and open is `1`.

### 2b. Agent operational invariants

The `/metrics` scrape refreshes three gauges directly from PostgreSQL and
Redis, so quiet deployments still expose current state:

- `testlookup_agent_in_progress_overdue_seconds` is zero through the configured
  `AI_PIPELINE_DEADLINE_SECONDS` plus `AGENT_PIPELINE_ALERT_GRACE_SECONDS`, then
  reports the oldest run's overrun;
- `testlookup_agent_dlq_depth` totals the `persist_live_session` list and the
  shared Celery/live-event dead-letter stream; and
- `testlookup_pending_review_oldest_age_seconds` reports the oldest open
  agent-review age.

The versioned Prometheus rules alert at any deadline overrun, any DLQ entry,
and a pending review older than 24 hours. Operators can inspect both Redis DLQ
stores at `GET /api/v1/admin/dlq`; `POST /api/v1/admin/dlq/{id}/replay` accepts
only the allowlisted Celery task shapes written by the application.

### 2c. Logical Celery queue depths

`worker/queue_inventory.py` defines the five base queues (`critical`,
`ingestion`, `ai_analysis`, `agent_children`, `default`) and appends the
configured ingestion shards. Celery declarations/routes, `/health/ingestion`
and scrape collection share that inventory. Deployment worker `-Q`
subscriptions still require profile-specific verification; declaring a queue
does not establish that an active worker consumes it.

`celery_queue_length{queue_name}` and the health response's existing `queues`
map count broker-pending messages across **every Redis priority bin**, using
the configured broker URL, separator, priority steps and global key prefix.
The standard Redis transport uses four lists per logical queue; reading only
the unsuffixed list misses nonzero-priority work. Collection uses one
nontransactional read pipeline. A failed bin makes that logical queue unknown
(`null` in health), preserves other readable queues, and fails the scrape
collector's success and freshness contract. Dashboards and rules can use
`testlookup:celery_queue_pending`, which requires a healthy scrape and a
successful observation for that queue at most 60 seconds old.

The health endpoint's `status`, `redis_reachable` and memory/admission fields
retain their existing meaning: **application admission Redis**. Queue counts
refer to the separately configured broker; an `ok` admission status does not
make a `null` queue count healthy. No new response fields are introduced.
Pipeline work is linear in the declared inventory: with default priorities,
`4 * (5 + LIVE_INGEST_SHARD_COUNT)` length reads. Large shard counts need
profile sizing; the scrape's collection deadline is not a fixed command-count
limit or a claim of unlimited throughput.

These counts exclude active, reserved, and unacknowledged deliveries. Use the
worker's queue wait histogram to see how long observed deliveries took to
start. The duration can include publication, broker wait, and reservation.
Starts and completed attempts indicate progress. SQL outbox lifecycle signals
track durable outcomes. The size-only broker reader leaves exact oldest
pending message age unknown.
[Celery queue monitoring](../docs/operations/celery-queue-monitoring.md).

## 3. Traces (`core/tracing.py`)

Network trace export requires a configured OTLP endpoint. API, worker, and MCP
instrumentation sends sanitized spans when export is enabled. Worker
instrumentation starts inside the relevant process, including prefork children.
A validated W3C
`traceparent` links publication and delivery spans, and separately instrumented
Beat processes record scheduled publication work. Sampled spans can show where
an API request, worker attempt, or MCP tool spent time. Configure a destination
with access, egress, and retention policy for the installation.
[Worker and Beat tracing](../docs/operations/worker-tracing.md).
[Durable logs and traces](../docs/operations/durable-telemetry.md).

## 4. Logs — structlog, kwargs only

Use event names and keyword fields with `structlog` loggers. For an event that
can contain sensitive exception details, log a bounded category such as
`logger.warning("thing_failed", error_type=type(exc).__name__)`.

When the operational export is enabled, API, Celery, and MCP records use fixed
event names and bounded status and duration fields. Request and task IDs are
hashed for log lookup. When a record has an active span, its trace and span
IDs can correlate the record with the trace. Use resource labels to select the
service and environment, and use the record fields to filter by identifiers.
[Collector and export contract](../docs/operations/telemetry-collector.md).
[Search an incident](../docs/operations/durable-telemetry.md#search-an-incident).

## 5. Decision logs — observability for the AI layer

Every pipeline agent subclasses `BaseAgent`, which owns the telemetry so
individual agents can't forget it: per-stage timing, a Prometheus emission, an
OTEL span per stage — and `log_decision(...)`, which records each *choice* the
agent makes (routing mode chosen, fallback taken, threshold crossed) to:

- **`agent_stage_results.decision_log`** — a JSON array committed with the
  stage checkpoint (the same rows that let the pipeline resume from the last
  good stage), and
- an **OTEL span event**, so Jaeger timelines show the decision inline with
  the latency it explains.

The user-facing ends of this are the **Decision Trail** drawer and the Agent
Pipeline page's Workflow Progress — a verdict can always answer "which stage
decided this, and why" (see [AI_QUALITY.md](./AI_QUALITY.md) for the
provenance rules this feeds).

### 5a. Agentic-layer observability

The §5 decision logs cover the **per-ingest** pipeline. Agents that *act*
(Investigator, Fixer — see [README §6](./README.md#6-agentic-layer-investigator--fixer))
are observable through a different, coarser record, because the question about
them is "what did it do and was it allowed to" rather than "which stage decided
this":

- **`agent_runs`** is the ledger: `trigger`, `status`, `mode`,
  `actions_proposed` vs `actions_taken` (the gap between the two is what
  `shadow` mode exists to show), `tokens`, `cost_usd`, `duration_ms`, and
  **`prompt_registry_digest`** — which ties the run to the exact pinned prompt
  set that produced it (see
  [AI_QUALITY.md §5](./AI_QUALITY.md#5-prompt-registry--eval-attestation-servicesprompt_manifestjson)).
  A past agent decision is therefore reproducible, not just auditable.
- **`fix_attempts`** records every Fixer attempt — `status`, `attempt_no`,
  `patch_summary`, `patch` — whether or not it succeeded. Failed attempts are
  kept deliberately: "the Fixer tried and could not" is the useful signal.
- **`GET /api/v1/agents/event-log/health`** gives authorized QA leads and
  administrators a current, project-scoped view of the SQL outbox, including
  backlog, oldest pending age, capacity, and status counts. The shared
  PostgreSQL outbox stages event intent with the originating change. A leased
  relay materializes events idempotently in MongoDB, and authorized operators
  can requeue a failed event delivery for its project.
  [Pipeline event delivery](../docs/operations/pipeline-event-delivery.md).
- Surfaces: `/agents` (activity), `/settings/agent-activity`,
  `/settings/ai-agents` (policy + budgets).

> **Reading a failed pipeline.** A `failed` run keeps its non-started stages at
> `pending` rather than rewriting them — terminal parent, non-terminal children.
> The UI derives its display from the *pipeline* status, so this is a data
> detail rather than a rendering bug, but a query counting `pending` stages
> across pipelines will over-count work that will never run.

## 6. Health surfaces

- `GET /health/live` and `/health/ready` support Kubernetes API probes.
- `GET /health/version` identifies the serving API build without dependency probes.
- `GET /health/details` requires a signed-in session or an accepted API key. It
  reports per-dependency status (postgres/mongo/redis/minio/
  ollama/chroma, each probed with a time budget), uptime, and the **build
  provenance block** (`revision`, `built_at` — baked in at image build), so an
  operator can match a running container to its commit without shelling in.
  The MCP `health_check` tool renders the same payload for agents.
- **Integration Health** (`/settings/integration-health`) — the user-facing
  status board for outbound integrations.

Worker health uses a separate process probe. Readiness records consumer startup
for the current Linux parent, and liveness tracks whether that parent exists.
Use queue progress and durable outcomes to assess work after startup.
[Worker launch and readiness](../docs/operations/worker-readiness.md).

## Related docs

- The pipeline whose stages these signals instrument: [README.md §5](./README.md#5-ai-analysis-pipeline-langgraph)
- Deploying the monitoring stack: [DEPLOYMENT.md §2](./DEPLOYMENT.md#2-compose-variants)
- The audit trail (who-did-what, distinct from telemetry): [SECURITY.md §6](./SECURITY.md#6-audit--data-hygiene)
- [Measured API and SQL journeys](../docs/operations/journey-slis.md)
- [Release assurance and rollout evidence](../docs/operations/release-assurance.md)
