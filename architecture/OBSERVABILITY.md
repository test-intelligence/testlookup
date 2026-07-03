# Observability — metrics, traces, decisions

> Companion to [README.md](./README.md). How you see inside TestLookup: the
> Prometheus/Grafana stack, OTEL tracing, structured logs, and the layer that's
> unusual here — **agent decision logs**, which make the AI pipeline's choices
> as observable as its latencies. Verified against the implementation
> 2026-07-02.

## 1. The three signals plus one

| Signal | Where it comes from | Where it goes |
|---|---|---|
| **Metrics** | `core/metrics.py` (prometheus_client) | Prometheus → Grafana (`infra/monitoring`) |
| **Traces** | `core/tracing.py` (OTEL SDK) | OTLP HTTP → Jaeger or any OTEL collector |
| **Logs** | `structlog` (JSON, kwargs-only) | stdout → your log pipeline |
| **Decisions** | `BaseAgent.log_decision` | `agent_stage_results.decision_log` + OTEL span events + the Decision Trail UI |

## 2. Metrics (`core/metrics.py`)

Domain-shaped Prometheus instruments rather than generic middleware-only
metrics: ingestion (`ingestion_runs_total`, `ingestion_test_cases_total`,
`ingestion_duration_seconds`), uploads (`uploads_total`,
`upload_failures_total`, `upload_processing_seconds`), and the AI layer
(`ai_analyses_total`, …) — so dashboards speak the product's language
("analyses per mode", not just "requests per route").

The compose sidecar stack (`docker-compose.monitoring.yml` +
`infra/monitoring/`) ships `prometheus.yml` scrape config, provisioned
**Grafana dashboards**, and **alert rules** (`prometheus-rules/testlookup-alerts.yml`)
— alerts are versioned artifacts in the repo, not console-configured state.

## 3. Traces (`core/tracing.py`)

`setup_tracing(otlp_endpoint=…)` wires the OTEL SDK with an OTLP HTTP exporter
(e.g. `http://jaeger:4318`); unset, tracing is a no-op — consistent with the
offline-first posture (no telemetry leaves a default install, see
[SECURITY.md §5](./SECURITY.md#5-offline-first-as-a-security-property)).
Requests, Celery stages, and agent stages become spans, so a slow analysis is
attributable to a specific pipeline stage in a Jaeger timeline.

## 4. Logs — structlog, kwargs only

JSON structured logging via `structlog`. One sharp edge is a repo convention
because it caused real 500s: **BoundLogger does not accept stdlib-style
positional args** — `logger.warning("x: %s", exc)` raises `TypeError`
*inside the except block that was handling the original error*. Always
key-value: `logger.warning("thing_failed", error=str(exc))`.

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

## 6. Health surfaces

- `GET /health/live` / `/health/ready` — K8s probes.
- `GET /health/details` — per-dependency status (postgres/mongo/redis/minio/
  ollama/chroma, each probed with a time budget), uptime, and the **build
  provenance block** (`revision`, `built_at` — baked in at image build), so an
  operator can match a running container to its commit without shelling in.
  The MCP `health_check` tool renders the same payload for agents.
- **Integration Health** (`/settings/integration-health`) — the user-facing
  status board for outbound integrations.

## Related docs

- The pipeline whose stages these signals instrument: [README.md §5](./README.md#5-ai-analysis-pipeline-langgraph)
- Deploying the monitoring stack: [DEPLOYMENT.md §2](./DEPLOYMENT.md#2-compose-variants)
- The audit trail (who-did-what, distinct from telemetry): [SECURITY.md §6](./SECURITY.md#6-audit--data-hygiene)
