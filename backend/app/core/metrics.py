"""
TestLookup — Prometheus custom application metrics.

Business-level metrics that complement the HTTP-level metrics emitted automatically
by prometheus-fastapi-instrumentator.

All metric objects are module-level singletons — import directly:
    from app.core.metrics import ingestion_runs_total, pipeline_stage_duration_seconds
"""
from prometheus_client import Counter, Gauge, Histogram, Info

# ── Ingestion ─────────────────────────────────────────────────────────────────

ingestion_runs_total = Counter(
    "testlookup_ingestion_runs_total",
    "Total test-run ingestion tasks processed",
    ["status"],  # success | failure
)

ingestion_test_cases_total = Counter(
    "testlookup_ingestion_test_cases_total",
    "Total individual test cases ingested",
    ["framework", "status"],  # framework: allure|testng|junit; status: passed|failed|skipped
)

ingestion_duration_seconds = Histogram(
    "testlookup_ingestion_duration_seconds",
    "Wall-clock time for a complete test-run ingestion",
    buckets=[1, 5, 15, 30, 60, 120, 300],
)

# ── Manual report upload (MRU-15) ───────────────────────────────────────────────

uploads_total = Counter(
    "testlookup_uploads_total",
    "Manual report uploads by outcome and detected format",
    ["state", "format"],  # state: succeeded|failed; format: junit|testng|allure|archive|cypress|playwright
)

upload_failures_total = Counter(
    "testlookup_upload_failures_total",
    "Manual report upload failures by error code",
    ["code"],  # parse_error|empty_report|ingest_error|zip_bomb|unsafe_path|nested_zip|...
)

upload_processing_seconds = Histogram(
    "testlookup_upload_processing_seconds",
    "Wall-clock time to parse + ingest a SUCCESSFUL manual upload (worker side)",
    buckets=[0.5, 1, 2, 5, 15, 30, 60, 120],
)

# ── AI Pipeline ───────────────────────────────────────────────────────────────

ai_analyses_total = Counter(
    "testlookup_analyses_total",
    "Total AI root-cause analysis pipeline runs completed",
    ["workflow_type", "status"],  # workflow_type: offline|deep; status: success|failure
)

ai_analysis_duration_seconds = Histogram(
    "testlookup_analysis_duration_seconds",
    "Wall-clock time for a complete AI pipeline run",
    ["workflow_type"],
    buckets=[5, 15, 30, 60, 120, 300, 600],
)

active_pipeline_runs = Gauge(
    "testlookup_active_pipeline_runs",
    "Pipeline runs currently in progress",
    ["workflow_type"],
)

pipeline_execution_context_persist_failures_total = Counter(
    "testlookup_pipeline_execution_context_persist_failures_total",
    "Pipeline runs whose frozen execution-context snapshot could not be persisted before graph execution",
)

# ── Pipeline Stages ───────────────────────────────────────────────────────────

pipeline_stage_duration_seconds = Histogram(
    "testlookup_pipeline_stage_duration_seconds",
    "Duration of each individual agent stage",
    ["stage_name", "status"],  # status: completed|failed
    buckets=[0.5, 1, 2, 5, 10, 30, 60, 120],
)

pipeline_stage_runs_total = Counter(
    "testlookup_pipeline_stage_runs_total",
    "Total agent stage executions",
    ["stage_name", "status"],
)

# ── Phase 6: Agent Observability & Cost Control ──────────────────────────────

pipeline_stage_tokens_total = Counter(
    "testlookup_pipeline_stage_tokens_total",
    "Total tokens consumed per agent stage",
    ["stage_name", "direction"],  # direction: input | output
)

pipeline_stage_cost_usd = Counter(
    "testlookup_pipeline_stage_cost_usd",
    "Estimated cost in USD per agent stage",
    ["stage_name"],
)

#: A cloud LLM call we could not price. Without this, an unpriced model is an
#: invisible understatement of the bill — which is the exact defect that left
#: every cost_usd at 0.00 before pricing existed.
llm_unpriced_calls_total = Counter(
    "testlookup_llm_unpriced_calls_total",
    "Cloud LLM calls with no matching entry in the price table",
    ["provider", "model"],
)

pipeline_stage_llm_calls_total = Counter(
    "testlookup_pipeline_stage_llm_calls_total",
    "Total LLM calls per agent stage",
    ["stage_name"],
)

pipeline_fallback_total = Counter(
    "testlookup_pipeline_fallback_total",
    "Number of fallback invocations per agent stage",
    ["stage_name", "reason"],  # reason: llm_timeout | llm_error | token_limit | provider_unavailable
)

pipeline_stage_errors_by_category = Counter(
    "testlookup_pipeline_stage_errors_by_category",
    "Error counts by category per agent stage",
    ["stage_name", "error_category"],  # error_category: transient | permanent | provider_error | token_limit | timeout
)

# ── Release Gate ──────────────────────────────────────────────────────────────

release_decisions_total = Counter(
    "testlookup_release_decisions_total",
    "Total release gate decisions issued",
    ["recommendation"],  # GO | NO_GO | CONDITIONAL_GO
)

# ── LLM / Inference ───────────────────────────────────────────────────────────

llm_requests_total = Counter(
    "testlookup_llm_requests_total",
    "Total LLM inference calls made",
    ["provider", "status"],  # status: success|failure|timeout
)

llm_request_duration_seconds = Histogram(
    "testlookup_llm_request_duration_seconds",
    "LLM inference latency (wall-clock)",
    ["provider"],
    buckets=[1, 2, 5, 10, 30, 60, 120],
)

llm_circuit_breaker_trips_total = Counter(
    "testlookup_llm_circuit_breaker_trips_total",
    "Number of times the LLM circuit breaker transitioned to OPEN state",
)

# ── Celery ────────────────────────────────────────────────────────────────────

celery_tasks_total = Counter(
    "testlookup_celery_tasks_total",
    "Total Celery background tasks executed",
    ["task_name", "status"],  # status: success|failure|retry
)

celery_task_duration_seconds = Histogram(
    "testlookup_celery_task_duration_seconds",
    "Celery task wall-clock execution time",
    ["task_name"],
    buckets=[1, 5, 15, 30, 60, 300, 600],
)

# ── WebSocket ─────────────────────────────────────────────────────────────────

websocket_connections_active = Gauge(
    "testlookup_websocket_connections_active",
    "Number of active WebSocket connections (summed across all projects)",
)

# ── Run Intelligence (Epic 11) ────────────────────────────────────────────────

run_intelligence_duration_seconds = Histogram(
    "testlookup_run_intelligence_duration_seconds",
    "Latency of the Run Intelligence aggregation endpoint",
    buckets=[0.1, 0.5, 1, 2, 5, 10, 30],
)

run_intelligence_requests_total = Counter(
    "testlookup_run_intelligence_requests_total",
    "Total Run Intelligence endpoint requests",
    ["status"],  # success | failure
)

# ── Summary Fallback (Epic 11) ───────────────────────────────────────────────

summary_fallback_total = Counter(
    "testlookup_summary_fallback_total",
    "Number of times summary generation fell back to deterministic mode",
    ["mode"],  # executive | developer | manager
)

summary_requests_total = Counter(
    "testlookup_summary_requests_total",
    "Total summary generation requests by mode",
    ["mode", "source"],  # source: llm | fallback | cached
)

# ── Semantic Search (Epic 11) ────────────────────────────────────────────────

semantic_search_total = Counter(
    "testlookup_semantic_search_total",
    "Total semantic search requests",
    ["search_type", "status"],  # search_type: keyword|semantic|hybrid; status: success|fallback|error
)

semantic_search_duration_seconds = Histogram(
    "testlookup_semantic_search_duration_seconds",
    "Semantic search latency",
    ["search_type"],
    buckets=[0.05, 0.1, 0.5, 1, 2, 5],
)

search_index_documents = Gauge(
    "testlookup_search_index_documents",
    "Number of documents in the ChromaDB search index",
)

# ── Defect Promotion (Epic 11) ───────────────────────────────────────────────

defect_promotions_total = Counter(
    "testlookup_defect_promotions_total",
    "Total defect promotions from failure clusters",
    ["result"],  # success | duplicate_detected | jira_created | jira_failed | local_only
)

# ── Release Override (Epic 11) ───────────────────────────────────────────────

release_overrides_total = Counter(
    "testlookup_release_overrides_total",
    "Total release decision overrides by QA leads",
    ["from_recommendation", "to_recommendation"],  # GO→NO_GO, etc.
)

# ── Launch Hardening (Epic 8) ─────────────────────────────────────────────────

auth_failures_total = Counter(
    "testlookup_auth_failures_total",
    "Total authentication/authorization failures",
    ["reason"],  # expired_token | invalid_token | insufficient_role | inactive_user
)

secret_read_failures_total = Counter(
    "testlookup_secret_read_failures_total",
    "Total failures reading secrets from secret_refs",
    ["scope"],  # ai_config | integrations_config | smtp_config
)

integration_health_gauge = Gauge(
    "testlookup_integration_health",
    "Integration provider health status (1=healthy, 0.5=degraded, 0=down)",
    ["provider"],  # jira | splunk | ocp | slack | teams | chromadb | ollama
)

feature_flag_evaluations_total = Counter(
    "testlookup_feature_flag_evaluations_total",
    "Total feature flag evaluations",
    ["flag_key", "result"],  # result: enabled | disabled | default
)

report_exports_total = Counter(
    "testlookup_report_exports_total",
    "Total report/export downloads",
    ["format", "type"],  # format: excel|word|pdf; type: test_cases|test_plan|strategy|intelligence
)

# ── Tier 0-2 operations (Phase E-3, 2026-04-15) ─────────────────────────────

quarantine_proposals_total = Counter(
    "testlookup_quarantine_proposals_total",
    "Total flaky quarantine proposals created",
    ["source"],  # auto | manual
)

quarantine_approvals_total = Counter(
    "testlookup_quarantine_approvals_total",
    "Total flaky quarantine state transitions on approval",
    ["outcome"],  # approved | rejected
)

quarantine_expired_total = Counter(
    "testlookup_quarantine_expired_total",
    "Total quarantined tests that transitioned to RELEASED or RE_QUARANTINED "
    "via the nightly maintenance sweep",
    ["terminal_state"],  # released | re_quarantined
)

perf_baseline_refresh_runs_total = Counter(
    "testlookup_perf_baseline_refresh_runs_total",
    "Total perf baseline nightly refresh task runs",
    ["status"],  # success | skipped | failure
)

retro_digest_dispatch_total = Counter(
    "testlookup_retro_digest_dispatch_total",
    "Total weekly retro digest dispatch task runs",
    ["status"],  # success | skipped | failure
)

webhook_delivery_attempts_total = Counter(
    "testlookup_webhook_delivery_attempts_total",
    "Total outbound webhook delivery attempts",
    ["event_type", "result"],  # result: success | failure | retry
)

compliance_pack_generated_total = Counter(
    "testlookup_compliance_pack_generated_total",
    "Total release compliance packs generated",
    ["result"],  # success | disabled | not_available | error
)

# P2-3: orphan TestSuite rows flagged by the nightly reaper. Increments
# once per detected orphan — see docs/DATABASE_AUDIT_2026-05-16.md.
orphan_test_suites_total = Counter(
    "testlookup_orphan_test_suites_total",
    "Total orphan TestSuite rows flagged for ops review (no canonical_test_cases children)",
)

# ── Service Metadata ──────────────────────────────────────────────────────────

app_info = Info(
    "testlookup_app",
    "TestLookup static application metadata",
)


# ── Celery queue depth ────────────────────────────────────────────────────────
#
# ``celery_queue_length`` is what the TestLookupCeleryQueueBacklog alert rule
# fires on. It was never emitted by anything — verified against the live
# deployment's /metrics endpoint, 0 samples — so the alert that exists to catch
# "queue backing up, workers may need scaling" could never fire. That is exactly
# the condition the 2026-08-08 worker CPU-throttling fix was about.
#
# The value must be exposed by the *backend*, because that is the process
# Prometheus scrapes; a gauge set inside a worker never reaches it. Celery
# queues are plain Redis lists, so the backend can read their depth directly.
#
# Collected at scrape time rather than on a timer: a gauge refreshed by a beat
# task goes stale (and keeps reporting its last value) whenever the beat pod is
# the thing that is unhealthy.

celery_queue_length = Gauge(
    "celery_queue_length",
    "Pending tasks per Celery queue, read from the Redis broker at scrape time",
    ["queue_name"],
)


# ── Celery task runtime ───────────────────────────────────────────────────────
#
# ``celery_task_runtime_seconds`` backs the TestLookupTaskLatencyHigh alert
# (p99 > 120s). Like celery_queue_length before it, the alert existed while
# nothing emitted the metric — verified live, 0 samples.
#
# This one has to be recorded inside the WORKER, not the backend, because only
# the worker sees task execution. Workers are prefork: each child has its own
# registry, so the process exporting /metrics cannot see sibling children's
# values. prometheus_client's multiprocess mode is the mechanism — children
# write to PROMETHEUS_MULTIPROC_DIR and the exporter aggregates the files.
#
# Buckets are chosen around the alert's 120s threshold so the histogram can
# actually resolve a p99 near it; the default buckets top out at 10s, which
# would make every long task land in +Inf and the quantile meaningless.

celery_task_runtime_seconds = Histogram(
    "celery_task_runtime_seconds",
    "Celery task execution time in seconds",
    ["task_name", "queue_name"],
    buckets=(0.1, 0.5, 1, 2, 5, 10, 30, 60, 120, 300, 600, float("inf")),
)
