"""The cross-run insights vocabulary (W23a FAIL-11), in ONE place.

Every enum-like field of an insight (``kind``, ``severity``,
``resolved_reason``) is a plain ``str`` on the wire and in the database; the
values it may take live here as module constants. The frontend mirrors them in
``frontend/src/types/insights.ts`` (pinned by
``tests/test_insight_cadence_vocab_frontend_parity.py``); ``INSIGHT_KINDS``,
``INSIGHT_SEVERITIES`` and ``INSIGHT_RESOLVE_REASONS`` are also the CHECK
literal lists of migration ``0243`` and of the ORM (same test).

Rules only: no LLM. A detector that cannot measure says so with one of the
``NOT_MEASURED`` sentences (absence is not health).

Constants only: no ``app.*`` import, no session, no I/O.
"""
from __future__ import annotations

INSIGHT_FLAG = "cross_run_insights"                 # seeded ON by 0243
INSIGHT_KINDS = ("perf_drift", "flake_rising", "coverage_drop", "cost_budget")
INSIGHT_SEVERITIES = ("low", "medium", "high")      # ranked in this order; the card's tone: info / warning / danger
INSIGHT_RESOLVE_REASONS = ("cleared", "stale")
INSIGHT_RANGES = {"24h": 24, "7d": 168}             # GET …/insights?range= ; hours
INSIGHT_RANGE_DEFAULT = "7d"
INSIGHT_LIST_MAX = 100                              # limit bounds; the answer always carries total
INSIGHT_LIST_DEFAULT = 50
WINDOW_KEY = {"perf_drift": "7d", "flake_rising": "7d", "coverage_drop": "7d"}   # cost_budget: "month:YYYY-MM"
STALE_AFTER_HOURS = 72                              # an open insight not re-confirmed for 72 h resolves "stale"
INSIGHT_SWEEP_LOCK_KEY = "testlookup:lock:insights-sweep"
INSIGHT_SWEEP_LOCK_TTL_SECONDS = 3300               # < the hourly cadence; > the 1740 s soft limit
MAX_SUBJECTS_PER_DETECTOR = 20                      # stored per evaluation, most severe first; `firing` counts all

# perf_drift (reads stored EXEC-05 findings; adds no statistics of its own)
PERF_WINDOW_DAYS = 7
PERF_MAX_RUNS_READ = 200                            # newest first; evidence says when the window held more
PERF_TEST_MIN_SPIKE_RUNS = 3                        # a test spiking in >= 3 distinct runs of the window
PERF_TEST_RECENT_HOURS = 48                         # … at least one of them in the last 48 h
PERF_SUITE_HIGH_RATIO = 2.0                         # suite ratio >= 2.0 -> high, else medium
PERF_TEST_HIGH_SIGMA = 6.0                          # test: max sigma >= 6 or >= 5 spiking runs -> high
PERF_TEST_HIGH_RUNS = 5

# flake_rising (the analytics_service.flaky_tests rule over two adjacent windows)
FLAKE_WINDOW_DAYS = 7
FLAKE_MIN_RUNS_PER_WINDOW = 5                       # per suite, in EACH window, else that suite is not measured
FLAKE_MIN_OBSERVATIONS = 3                          # == flaky_tests' HAVING COUNT(*) >= 3
FLAKE_FAIL_SHARE = (0.05, 0.95)                     # == flaky_tests' BETWEEN 0.05 AND 0.95
FLAKE_MIN_CURRENT = 3
FLAKE_MIN_RATIO = 2.0
FLAKE_MIN_DELTA = 2
FLAKE_HIGH_RATIO = 3.0
FLAKE_HIGH_MIN_CURRENT = 6
FLAKE_EXAMPLES = 5                                  # example test names kept in evidence

# coverage_drop (reads two stored TRND-07 snapshots)
COVERAGE_COMPARE_DAYS = 7
COVERAGE_MAX_SNAPSHOT_AGE_DAYS = 2                  # the newest snapshot must be at most 2 UTC days old
COVERAGE_PREVIOUS_TOLERANCE_DAYS = 2                # the "week before" snapshot may be 7..9 days before the newest
COVERAGE_MIN_EXERCISED = 20                         # the earlier snapshot exercised >= 20 tests
COVERAGE_MIN_DROP_SHARE = 0.10
COVERAGE_MIN_DROP_TESTS = 5
COVERAGE_HIGH_DROP_SHARE = 0.25

# cost_budget (reuses llm_cost_budget.get_current_usage; no new math)
COST_FIRING_STATUSES = {"SOFT_WARN": "medium", "CAPPED": "high"}

#: The ONLY link targets (server-built, the SPA re-checks): kind -> (path, label).
#: perf_drift evidence may add run_path "/runs/<uuid>?tab=changes" (EXEC-05's own run link).
INSIGHT_LINKS = {
    "perf_drift": ("/trends?tab=durations", "Trends › Execution time"),
    "flake_rising": ("/flaky", "Flaky tests"),
    "coverage_drop": ("/coverage?tab=history", "Coverage › History"),
    "cost_budget": ("/settings/billing", "LLM cost budget"),
}

#: Detector state reason sentences (fixed; the card shows them verbatim).
NOT_MEASURED = {
    "not_evaluated": "Not evaluated yet — the first check runs within the hour.",
    "flag_off": "Insights are off for this project (feature flag cross_run_insights).",
    "perf_no_findings": "No run in the last 7 days was checked for duration regressions.",
    "perf_tests_flag_off": (
        "Per-test duration spikes are off for this project (feature flag perf_regression_detection); "
        "suites are still checked."
    ),
    "flake_few_runs": "No suite had 5 runs in both of the last two weeks.",
    "coverage_no_snapshot": "No coverage snapshot yet: the first is taken at 00:05 UTC.",
    "coverage_stale_snapshot": "The newest coverage snapshot is more than 2 days old.",
    "coverage_no_previous": "There is no coverage snapshot from a week before the newest one.",
    "coverage_too_small": "Fewer than 20 tests were exercised a week ago.",
    "cost_flag_off": "The LLM cost budget is off (feature flag llm_cost_budget).",
    "cost_no_budget": "No AI budget is set for this project.",
    "detector_failed": "This check failed on its last run; it is retried within the hour.",
}

#: detail = {"code": <key>, "message": <sentence>} on the insight routes' own 4xx.
INSIGHT_ERRORS = {
    "insight_not_found": "Insight not found.",                      # 404 (another project's id alike)
    "unknown_range": "Unknown range. Valid: 24h, 7d.",              # 422
}
