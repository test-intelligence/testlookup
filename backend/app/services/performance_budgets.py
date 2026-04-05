"""
Performance Budgets — codified latency and throughput targets (OPS-03-1).

These budgets are the source of truth for what "fast enough" means.
Load tests assert against these values. Dashboards alert when breached.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LatencyBudget:
    """Max acceptable latency for an operation."""
    operation: str
    p50_ms: int   # median
    p95_ms: int   # 95th percentile
    p99_ms: int   # 99th percentile
    description: str


@dataclass(frozen=True)
class ThroughputBudget:
    """Minimum acceptable throughput for a workload."""
    operation: str
    min_rps: float       # requests per second
    description: str


# ── Latency Budgets ──────────────────────────────────────────────────────────

LATENCY_BUDGETS: list[LatencyBudget] = [
    LatencyBudget("keyword_search", 50, 200, 500, "Keyword search (PostgreSQL ILIKE) for a 10-char query"),
    LatencyBudget("semantic_search", 100, 500, 1000, "Semantic search via ChromaDB for a natural language query"),
    LatencyBudget("hybrid_search", 150, 600, 1200, "Combined keyword + semantic search with ranking"),
    LatencyBudget("run_intelligence", 200, 800, 2000, "Full run intelligence page load from cached snapshot"),
    LatencyBudget("run_list", 50, 200, 500, "Paginated test run listing (page_size=20)"),
    LatencyBudget("test_case_list", 50, 250, 600, "Test cases for a single run (page_size=50)"),
    LatencyBudget("release_decision", 100, 400, 800, "Release gate decision retrieval with council context"),
    LatencyBudget("pdf_export", 500, 2000, 5000, "PDF report generation for a run with 200 test cases"),
    LatencyBudget("evidence_bundle", 1000, 5000, 10000, "ZIP evidence bundle generation"),
    LatencyBudget("search_reindex_incremental", 5000, 15000, 30000, "Incremental search reindex (up to 5000 docs)"),
    LatencyBudget("search_reindex_full", 30000, 120000, 300000, "Full search reindex for 100K test cases"),
]

# ── Throughput Budgets ───────────────────────────────────────────────────────

THROUGHPUT_BUDGETS: list[ThroughputBudget] = [
    ThroughputBudget("search_concurrent", 20.0, "Concurrent search requests (keyword or semantic)"),
    ThroughputBudget("run_ingestion", 5.0, "Concurrent test run ingestion (webhook + parsing)"),
    ThroughputBudget("api_general", 100.0, "General API throughput (mixed read endpoints)"),
    ThroughputBudget("live_events_batch", 50.0, "Live streaming batch event ingestion (per session)"),
]

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
