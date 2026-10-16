"""
Performance Budgets — codified latency and throughput targets (OPS-03-1).

These budgets are the **single source of truth** for what "fast enough"
means. Three places must stay in sync with this file:

  1. ``backend/scripts/load_test_concurrent.py::SCENARIOS`` — load harness
     that exercises each path against a live server and reports p50/p95/p99.
  2. ``backend/tests/test_performance_budgets_live.py`` — pytest-runnable
     smoke check that asserts every scenario's p95 within its budget.
  3. ``infra/monitoring/prometheus-rules/testlookup-alerts.yml::testlookup.slo``
     — production alerts that fire on sustained budget breach.

Each scenario's ``http_handler`` field maps the budget to the
``handler="..."`` label that ``prometheus_fastapi_instrumentator`` emits,
so load harness + test + alert all reference the same path template.

When a load-test wave moves a budget (e.g. "run_list optimization
brought p95 from 735ms to 220ms under 10-way concurrency"), update the
``p95_ms`` here AND adjust the matching alert threshold so they don't
drift. The accompanying alert YAML cross-references operation names.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class LatencyBudget:
    """Max acceptable latency for an operation.

    ``http_handler`` is the FastAPI route template (with ``{path_param}``
    placeholders) so the budget can be looked up against the
    ``http_request_duration_seconds_bucket{handler="..."}`` series in
    Prometheus. ``None`` means the operation is internal (e.g. a Celery
    task) and not directly observable on the HTTP histogram.
    """
    operation: str
    p50_ms: int   # median
    p95_ms: int   # 95th percentile
    p99_ms: int   # 99th percentile
    description: str
    http_handler: Optional[str] = None
    http_method: str = "GET"


@dataclass(frozen=True)
class ThroughputBudget:
    """Minimum acceptable throughput for a workload."""
    operation: str
    min_rps: float       # requests per second
    description: str


# ── Latency Budgets ──────────────────────────────────────────────────────────

LATENCY_BUDGETS: list[LatencyBudget] = [
    # ── HTTP read paths covered by the live load harness ──────────────────
    LatencyBudget(
        "project_list", 50, 200, 400,
        "List user-accessible projects",
        http_handler="/api/v1/projects",
    ),
    LatencyBudget(
        "run_list", 50, 250, 500,
        "Paginated run list (page_size=20). Budget bumped from 200ms → 250ms "
        "after load-test wave gave 220ms p95 under 10-way concurrent on dev VMs.",
        http_handler="/api/v1/runs",
    ),
    LatencyBudget(
        "run_scoped_guard", 50, 250, 500,
        "Single run lookup. Includes require_run_access guard overhead.",
        http_handler="/api/v1/runs/{run_id}",
    ),
    LatencyBudget(
        "run_intelligence", 200, 800, 2000,
        "Run intelligence snapshot (warm cache). Cold cache populates via "
        "dedicated write session; this budget covers steady-state reads.",
        http_handler="/api/v1/runs/{run_id}/intelligence",
    ),
    LatencyBudget(
        "keyword_search", 50, 300, 600,
        "Search via pg_trgm GIN indexes. Budget bumped from 200ms → 300ms "
        "to accommodate p95 under 10-way concurrent load.",
        http_handler="/api/v1/search",
    ),
    LatencyBudget(
        "flaky_coach", 100, 600, 1200,
        "Project flaky leaderboard. First call per project populates a "
        "cache via dedicated write session (item #4); steady state is fast.",
        http_handler="/api/v1/projects/{project_id}/flaky-coach",
    ),
    LatencyBudget(
        "health_details", 200, 2500, 5000,
        "Deep health probe. Each non-critical dependency is bounded by a "
        "1.5s httpx.Timeout; budget allows for serial slow critical probes.",
        http_handler="/health/details",
    ),

    # ── Visualization analytics reads ─────────────────────────────────────
    # Measured on a reference deployment with 1M test cases and one API pod.
    # Only p95 is a specified target; p50/p99 are derived (≈ p95/5 and
    # 2 × p95) so the shape invariants hold.
    # The viz load harness (``load_test_concurrent.py bench --suite viz``)
    # holds each cold cell to these keys; a warm (cached) cell to
    # ``VIZ_CACHED_BUDGET_MS`` there.
    LatencyBudget(
        "chart_data", 300, 1500, 3000,
        "Generic chart-data read, cache miss. Route-level ceiling = the "
        "percentile-metric budget (duration_p95 et al.): the HTTP histogram "
        "cannot tell metric classes apart, so count/rate reads are held to "
        "the tighter harness-only chart_data_rate. Homelab 90 d duration_p95, "
        "1 user: server p95 242.5 ms.",
        http_handler="/api/v1/analytics/chart-data",
    ),
    LatencyBudget(
        "chart_data_rate", 100, 500, 1000,
        "Count/rate chart-data read (pass_rate, failures, executions), cache "
        "miss, up to 90 d. Harness-only: shares chart_data's route, so no "
        "alert can isolate it. Homelab 1 user: server p95 40-242.5 ms; holds "
        "to ~4 concurrent cold heavy readers per pod.",
    ),
    LatencyBudget(
        "analytics_heatmap", 200, 800, 1600,
        "Heatmap read (suite_day / suite_environment / suite_release / "
        "test_run), cache miss. Homelab 1 user: server p95 75-242.5 ms.",
        http_handler="/api/v1/analytics/heatmap",
    ),
    LatencyBudget(
        "chart_rows", 50, 300, 600,
        "Chart drill-down rows, page 1 (size 50). Homelab 1 user: server "
        "p95 9.5 ms.",
        http_handler="/api/v1/analytics/chart-data/rows",
    ),

    # ── EXEC-03 execution metrics (live harness ``SCENARIOS``) ───────────
    # Harness-only for now: no ``http_handler``, so no Prometheus SLO alert
    # or dashboard panel is required (``test_slo_alerts_match_budgets``).
    # The story's target is to stay inside chart_data's 1500 ms p95.
    LatencyBudget(
        "execution_metrics", 300, 1500, 3000,
        "GET /analytics/execution-metrics, 30 d, one project: per-day "
        "totals, per-suite p50/p95 vs the previous window, suite + test "
        "regressions, slowest tests, duration histogram. Same ceiling as "
        "chart_data (EXEC-03).",
    ),
    LatencyBudget(
        "execution_suite_history", 60, 300, 600,
        "GET /analytics/execution/suite-history, 90 d, one suite: an index "
        "range on suite_executions (project_id, suite_key, run_created_at).",
    ),
    # W06 DEF-04: harness-only like the two above (no ``http_handler``, so no
    # Prometheus SLO alert or dashboard panel is required).
    LatencyBudget(
        "defect_trend", 60, 300, 600,
        "GET /analytics/defects/trend, 90 d, one project: one generate_series "
        "statement for the daily points and one totals statement, both over "
        "that project's defects (ix_defects_project_id); cache miss.",
    ),
    LatencyBudget(
        "run_timing", 80, 400, 800,
        "GET /runs/{run_id}/timing: one run's aggregate, suites, slowest 20 "
        "(ix_test_cases_run_duration), lanes, a timeline of at most 2,000 "
        "tests and the run's duration findings.",
    ),
    # W07 (FAIL-02 / FAIL-01 / FAIL-04 / FAIL-03): harness-only like the W06
    # defect trend (no ``http_handler``, so no Prometheus SLO alert or
    # dashboard panel is required). Scenario names equal these names.
    LatencyBudget(
        "time_to_fix", 120, 600, 1200,
        "GET /analytics/time-to-fix, 90 d, one project: one window-function "
        "statement over the project's history, 90 d, cache miss.",
    ),
    LatencyBudget(
        "top_failing_detail", 80, 400, 800,
        "GET /analytics/top-failing, 30 d, one project: the top-N plus the "
        "per-failure detail (one DISTINCT ON statement for the latest failing "
        "rows; the ownership rules and component map only when a row has no "
        "eligible assignee).",
    ),
    LatencyBudget(
        "run_ai_verdicts", 30, 150, 300,
        "GET /runs/ai-verdicts, 25 run ids: the stored snapshots' verdict "
        "fields (payload parsed once per row) and one batched review read; "
        "nothing computed.",
    ),
    LatencyBudget(
        "run_failure_signatures", 120, 600, 1200,
        "GET /runs/failure-signatures, 50 run ids: one statement grouping at "
        "most 500 failing rows per run by FAILURE_SIGNATURE_SQL "
        "(ix_test_cases_run_status).",
    ),
    # W08 (TRND-06 / TRND-07 / TRND-08 / TRND-13): harness-only like W06/W07
    # (no ``http_handler``, so no Prometheus SLO alert or dashboard panel is
    # required). Scenario names equal these names.
    LatencyBudget(
        "trend_summary", 150, 600, 1200,
        "GET /analytics/trend-summary, 90 d, one project: the page's two reads "
        "(get_trend_data's run aggregates and coverage_stats' two statements) "
        "summarised in Python; cache miss.",
    ),
    LatencyBudget(
        "coverage_history", 50, 200, 400,
        "GET /analytics/coverage/history, 365 d, one project: an index range on "
        "coverage_snapshots (project_id, snapshot_day) of at most 365 rows plus "
        "the project's first day.",
    ),
    LatencyBudget(
        "coverage_untagged_tests", 120, 600, 1200,
        "GET /analytics/coverage/untagged-tests, 90 d, one project: the "
        "unlabelled executions grouped by test, counted and paged in one "
        "statement, with each page row's catalogue suite.",
    ),
    LatencyBudget(
        "value_by_team", 300, 1500, 3000,
        "GET /value-metrics/by-team, 365 d, one project: one grouped statement "
        "per (effective suite, test) with no row limit, the defects, the "
        "headline's two leg queries and the cluster members, resolved in Python.",
    ),
    LatencyBudget(
        "chart_data_facet", 300, 1500, 3000,
        "GET /analytics/chart-data, 90 d, one Explorer panel: executions by day "
        "and suite (execution-row grain) faceted to one branch; the same ceiling "
        "as chart_data.",
    ),

    # ── Other HTTP and pipeline operations not in the live harness ────────
    LatencyBudget(
        "semantic_search", 100, 500, 1000,
        "Semantic search via ChromaDB for a natural language query",
    ),
    LatencyBudget(
        "hybrid_search", 150, 600, 1200,
        "Combined keyword + semantic search with ranking",
    ),
    LatencyBudget(
        "test_case_list", 50, 250, 600,
        "Test cases for a single run (page_size=50)",
    ),
    LatencyBudget(
        "release_decision", 100, 400, 800,
        "Release gate decision retrieval with council context",
    ),
    LatencyBudget(
        "pdf_export", 500, 2000, 5000,
        "PDF report generation for a run with 200 test cases (off-loop via run_in_threadpool)",
    ),
    LatencyBudget(
        "evidence_bundle", 1000, 5000, 10000,
        "ZIP evidence bundle generation",
    ),
    LatencyBudget(
        "search_reindex_incremental", 5000, 15000, 30000,
        "Incremental search reindex (up to 5000 docs)",
    ),
    LatencyBudget(
        "search_reindex_full", 30000, 120000, 300000,
        "Full search reindex for 100K test cases",
    ),
]


def get_budget(operation: str) -> Optional[LatencyBudget]:
    """Return the budget for an operation by name, or None if unknown."""
    for b in LATENCY_BUDGETS:
        if b.operation == operation:
            return b
    return None

# ── Throughput Budgets ───────────────────────────────────────────────────────

THROUGHPUT_BUDGETS: list[ThroughputBudget] = [
    ThroughputBudget("search_concurrent", 20.0, "Concurrent search requests (keyword or semantic)"),
    ThroughputBudget("run_ingestion", 5.0, "Concurrent test run ingestion (webhook + parsing)"),
    ThroughputBudget("api_general", 100.0, "General API throughput (mixed read endpoints)"),
    ThroughputBudget("live_events_batch", 50.0, "Live streaming batch event ingestion (per session)"),
]


def get_throughput_budget(operation: str) -> Optional[ThroughputBudget]:
    """Return the throughput budget for an operation by name, or None.

    These budgets existed for a long time with no accessor at all — they were
    only ever serialized into :func:`get_all_budgets`, so nothing could look one
    up and nothing ever checked one. That was not harmless: the load harness
    measured ``search_concurrent`` at 17.4 rps against this file's 20.0 and
    reported "All budgets met", because it only compared p95 latency.
    """
    for b in THROUGHPUT_BUDGETS:
        if b.operation == operation:
            return b
    return None

# ── Scale Scenarios ──────────────────────────────────────────────────────────

SCALE_SCENARIOS: list[dict] = [
    {
        "name": "small_team",
        "description": "Small team: 10 projects, 50 runs/day, 500 tests/run",
        "projects": 10, "runs_per_day": 50, "tests_per_run": 500,
        "concurrent_users": 10,
    },
    {
        "name": "mid_enterprise",
        "description": "Mid enterprise: 50 projects, 200 runs/day, 2000 tests/run",
        "projects": 50, "runs_per_day": 200, "tests_per_run": 2000,
        "concurrent_users": 50,
    },
    {
        "name": "large_enterprise",
        "description": "Large enterprise: 200 projects, 1000 runs/day, 5000 tests/run",
        "projects": 200, "runs_per_day": 1000, "tests_per_run": 5000,
        "concurrent_users": 200,
    },
]


def get_all_budgets() -> dict:
    """Return all performance budgets as a serializable dict."""
    return {
        "latency_budgets": [
            {
                "operation": b.operation,
                "p50_ms": b.p50_ms,
                "p95_ms": b.p95_ms,
                "p99_ms": b.p99_ms,
                "description": b.description,
            }
            for b in LATENCY_BUDGETS
        ],
        "throughput_budgets": [
            {
                "operation": b.operation,
                "min_rps": b.min_rps,
                "description": b.description,
            }
            for b in THROUGHPUT_BUDGETS
        ],
        "scale_scenarios": SCALE_SCENARIOS,
    }
