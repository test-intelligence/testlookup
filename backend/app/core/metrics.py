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

# ── Service Metadata ──────────────────────────────────────────────────────────

app_info = Info(
    "testlookup_app",
    "TestLookup static application metadata",
)
