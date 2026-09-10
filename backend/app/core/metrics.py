"""
TestLookup — Prometheus custom application metrics.

Business-level metrics that complement the HTTP-level metrics emitted automatically
by prometheus-fastapi-instrumentator.

All metric objects are module-level singletons — import directly:
    from app.core.metrics import ingestion_runs_total, pipeline_stage_duration_seconds
"""
from prometheus_client import Counter, Gauge, Histogram

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

# Re-audit H5. finalize_run's post-steps each run in their own session so one
# failure cannot poison the next -- and each failure was a log line and nothing
# else. Nine steps (suite membership, canonical sync, deletion reconcile,
# failed-test assignment, auto-tagging, quarantine tagging, release linking,
# commit range, the activity ledger) could fail on every run indefinitely with
# no metric to graph and no alert to fire.
finalize_step_failures_total = Counter(
    "testlookup_finalize_step_failures_total",
    "Post-ingestion finalize steps that raised and were rolled back",
    ["step"],  # suite_sync|canonical_sync|canonical_deletion_reconcile|assign_failed_tests|auto_tagging|quarantine_tagging|release_linking|commit_range|activity_ledger
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
    multiprocess_mode="livesum",
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

# ``celery_task_duration_seconds`` stood here and was never emitted. It was a
# duplicate of ``celery_task_runtime_seconds`` below, which measures the same
# wall-clock execution time, carries a queue_name label as well, is emitted
# from the worker's task_postrun signal, and backs the TestLookupTaskLatencyHigh
# alert. Two names for one measurement is how the emitted one gets wired and
# the declared one is forgotten. Removed rather than wired: a second series
# with the same meaning would only invite the next reader to pick the wrong one.

# ── WebSocket ─────────────────────────────────────────────────────────────────

websocket_connections_active = Gauge(
    "testlookup_websocket_connections_active",
    "Number of active WebSocket connections (summed across all projects)",
    multiprocess_mode="livesum",
)

live_fanout_published_total = Counter(
    "testlookup_live_fanout_published_total",
    "Live dashboard notifications appended to Redis",
    ["result"],
)
live_fanout_delivered_total = Counter(
    "testlookup_live_fanout_delivered_total",
    "Live dashboard transport delivery attempts",
    ["transport", "result"],
)
live_fanout_replay_total = Counter(
    "testlookup_live_fanout_replay_total",
    "Live dashboard reconnect replay outcomes",
    ["result"],
)
live_fanout_subscriber_ready = Gauge(
    "testlookup_live_fanout_subscriber_ready",
    "Whether this API process has initialized its Redis fan-out subscriber",
    multiprocess_mode="livesum",
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
    multiprocess_mode="mostrecent",
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

# The declared vocabulary here used to read
#   expired_token | invalid_token | insufficient_role | inactive_user
# and NONE of those four was ever emitted. The counter's only call site is
# ``token_revocation._count``, which emits ``revocation_unavailable`` and
# ``revocation_write_failed`` — both outside the documented set. So a dashboard
# or alert filtering on reason="expired_token" matched zero series, for ever,
# while the four reasons an operator most wants to see went uncounted. Same
# class as the `backend.status-enum-vocab` gate: a consumer filtering against a
# vocabulary the producer never emits is silently, permanently empty.
#
# The four authentication reasons are now emitted from the dependency that
# actually rejects the request (`app/core/auth.py`), and the two revocation
# reasons are documented here rather than left undeclared.
auth_failures_total = Counter(
    "testlookup_auth_failures_total",
    "Total authentication/authorization failures",
    # expired_token | invalid_token | insufficient_role | inactive_user
    # | revocation_unavailable | revocation_write_failed
    ["reason"],
)

secret_read_failures_total = Counter(
    "testlookup_secret_read_failures_total",
    "Total failures reading secrets from secret_refs",
    ["scope"],  # ai_config | integrations_config | smtp_config
)

integration_health_gauge = Gauge(
    "testlookup_integration_health",
    "Integration provider health (1=healthy, 0.5=degraded, 0=down, -1=skipped)",
    ["provider"],
    multiprocess_mode="mostrecent",
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

# ── Authored test-case lifecycle governance ──────────────────────────────────

test_case_transitions_total = Counter(
    "testlookup_test_case_transitions_total",
    "Validated authored test-case lifecycle transitions",
    ["project", "from", "to", "actor_role"],
)

test_cases_by_state = Gauge(
    "testlookup_test_cases_by_state",
    "Current authored test cases in each governed lifecycle state",
    ["project", "state"],
    multiprocess_mode="mostrecent",
)

test_case_promotions_total = Counter(
    "testlookup_test_case_promotions_total",
    "Automation canonical cases promoted into authored test management",
    ["project"],
)

test_case_deprecations_without_reason_total = Counter(
    "testlookup_test_case_deprecations_without_reason_total",
    "Legacy DELETE deprecations that used the first-release compatibility reason",
    ["project"],
)

automation_cases_orphaned = Gauge(
    "testlookup_automation_cases_orphaned",
    "Deleted automation canonical cases awaiting retirement confirmation",
    ["project"],
    multiprocess_mode="mostrecent",
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

app_info = Gauge(
    "testlookup_app_info",
    "TestLookup static application metadata",
    ["version", "env", "llm_provider"],
    multiprocess_mode="mostrecent",
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
    multiprocess_mode="mostrecent",
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


# ── Release lifecycle (migration 0150) ────────────────────────────────────────

# TWO counters, not one, and the pairing is the point. A sweep counter that is
# never incremented and a violation counter that reads 0 look identical to a
# healthy system — "no violations" and "never ran" are the same number. Alert on
# violations only when sweeps is non-zero and increasing.
release_invariant_sweeps_total = Counter(
    "testlookup_release_invariant_sweeps_total",
    "Completed active-release reconciliation sweeps",
)

release_invariant_violations_total = Counter(
    "testlookup_release_invariant_violations_total",
    "Projects found with no active release, by what the sweep did about it",
    ["outcome"],  # repaired | repair_failed
)


# Same two-counter shape as the active-release invariant above, for the same
# reason: a drift counter reading zero is indistinguishable from a sweep that
# never ran.
release_primary_sweeps_total = Counter(
    "testlookup_release_primary_sweeps_total",
    "Completed primary_release_id drift sweeps",
)

release_primary_drift_total = Counter(
    "testlookup_release_primary_drift_total",
    "Runs whose denormalized primary_release_id disagreed with their primary link",
    ["outcome"],  # repaired | repair_failed
)


# ── Project activity ledger (epic ACT) ────────────────────────────────────────
# The ledger is best-effort by design: a write failure is swallowed so it can
# never fail the user's mutation. These two counters are therefore the ONLY way
# an operator can tell a quiet ledger ("nothing happened") from a broken one
# ("every write is being dropped"). Both are incremented in
# services/activity/service.py — declaration without emission would export a
# confident 0.0 and read as health.

activity_events_written_total = Counter(
    "testlookup_activity_events_written_total",
    "Activity ledger rows successfully written",
    ["category"],
)

activity_events_dropped_total = Counter(
    "testlookup_activity_events_dropped_total",
    "Activity ledger rows dropped, by reason",
    # unregistered_event | actor_type_not_allowed | bad_project_id | duplicate
    # | no_session_for_outcome | outcome_write_failed | attempt_write_failed
    ["reason"],
)
