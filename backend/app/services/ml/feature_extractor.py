"""
ML Feature Extractor — builds a fixed-size numeric feature vector from test case data.

Produces 31 features across 4 groups:
  Group 1: Error message signals (12 features — keywords, text stats, stack trace)
  Group 2: Test execution context (7 numeric features)
  Group 3: Historical signals (8 features from TestCaseHistory)
  Group 4: Environment signals (4 run-level features)

All features are numeric (int/float) — compatible with scikit-learn tree-based
models without additional encoding. Missing values are represented as -1 or 0
(HistGradientBoostingClassifier handles missing values natively).

Usage:
    from app.services.ml.feature_extractor import extract_features
    features = extract_features(test_case_dict, history_dict, run_context_dict)
    # features: dict with 31 keys, all numeric values
"""
import math
import re
from datetime import datetime, timezone
from typing import Any

# Patterns that indicate a real stack trace (language-agnostic)
_STACK_TRACE_PATTERNS = re.compile(
    r"(\bat\s+[\w.$<>]+\([\w.]+:\d+\))"       # Java: at com.Foo.bar(Foo.java:42)
    r"|(\bFile\s+\"[^\"]+\",\s+line\s+\d+)"    # Python: File "foo.py", line 12
    r"|(^\s+at\s+[\w./]+\s*\(.*:\d+:\d+\))"    # JS/TS: at Object.fn (file.js:1:2)
    r"|(\btraceback\s+\(most\s+recent)"         # Python: Traceback (most recent
    r"|(Caused\s+by:)"                          # Java chained exceptions
    r"|(^\s+\d+\s*\|)"                          # Rust/Go line-numbered traces
    , re.IGNORECASE | re.MULTILINE
)

# Feature names in canonical order (must match training)
FEATURE_NAMES: list[str] = [
    # Group 1: Error message signals
    "error_msg_length",
    "error_msg_log_length",
    "error_word_count",
    "stack_trace_depth",
    "has_timeout_keyword",
    "has_connection_keyword",
    "has_assertion_keyword",
    "has_null_keyword",
    "has_oom_keyword",
    "has_element_keyword",
    "has_setup_keyword",
    "has_http_error_keyword",
    # Group 2: Execution context
    "duration_ms",
    "duration_vs_median_ratio",
    "severity_encoded",
    "has_real_stack_trace",
    "run_pass_rate",
    "suite_failure_rate",
    "run_failed_test_count",
    # Group 3: Historical signals
    "historical_failure_rate",
    "historical_pass_rate",
    "historical_run_count",
    "days_since_last_pass",
    "consecutive_failures",
    "is_new_test",
    "status_volatility",
    "failure_rate_trend",
    # Group 4: Environment signals
    "hour_of_day",
    "is_weekend",
    "cross_suite_failure_rate",
    "same_error_other_tests",
]

_SEVERITY_MAP = {
    "BLOCKER": 4, "CRITICAL": 3, "MAJOR": 2, "MINOR": 1, "TRIVIAL": 0,
}

_KEYWORD_GROUPS = {
    "has_timeout_keyword": ["timeout", "timed out", "deadline exceeded", "socket timeout"],
    "has_connection_keyword": ["connection refused", "connection reset", "econnrefused", "broken pipe"],
    "has_assertion_keyword": ["assertion", "expected", "but was", "assertequals", "assert_equal"],
    "has_null_keyword": ["nullpointer", "undefined is not", "cannot read propert", "nonetype"],
    "has_oom_keyword": ["oomkilled", "out of memory", "memory limit"],
    "has_element_keyword": ["element not found", "no such element", "locator", "stale element"],
    "has_setup_keyword": ["setup failed", "fixture", "beforeall", "beforeeach", "precondition"],
    "has_http_error_keyword": ["502", "503", "504", "500 internal", "404 not found"],
}


def extract_features(
    test_case: dict[str, Any],
    history: dict[str, Any] | None = None,
    run_context: dict[str, Any] | None = None,
) -> dict[str, float]:
    """Extract a 31-feature numeric vector from test case data.

    Args:
        test_case: Dict with error_message, duration_ms, severity, etc.
        history: Historical stats: pass_count, fail_count, median_duration_ms,
                 consecutive_failures, last_pass_at, status_sequence.
        run_context: Run-level context: pass_rate, failed_tests, suite_failure_rate,
                     cross_suite_failure_rate, same_error_count, start_time.

    Returns:
        Dict mapping feature name → numeric value. All 31 features guaranteed present.
    """
    history = history or {}
    run_context = run_context or {}
    error_msg = (test_case.get("error_message") or "").lower()

    # ── Group 1: Error message signals ──────────────────────────────────
    raw_error = test_case.get("error_message") or ""
    stack_trace_matches = list(_STACK_TRACE_PATTERNS.finditer(raw_error))
    stack_depth = len(stack_trace_matches)

    features: dict[str, float] = {
        "error_msg_length": float(len(error_msg)),
        "error_msg_log_length": math.log1p(len(error_msg)),
        "error_word_count": float(len(error_msg.split())),
        "stack_trace_depth": float(stack_depth),
    }
    for feat_name, keywords in _KEYWORD_GROUPS.items():
        features[feat_name] = 1.0 if any(kw in error_msg for kw in keywords) else 0.0

    # ── Group 2: Execution context ──────────────────────────────────────
    duration = test_case.get("duration_ms") or 0
    median_dur = history.get("median_duration_ms") or 0
    features["duration_ms"] = float(duration)
    features["duration_vs_median_ratio"] = (
        float(duration / median_dur) if median_dur > 0 else -1.0
    )
    features["severity_encoded"] = float(
        _SEVERITY_MAP.get((test_case.get("severity") or "").upper(), 2)
    )
    # Real stack trace detection using language-aware patterns
    features["has_real_stack_trace"] = 1.0 if stack_depth > 0 else 0.0
    features["run_pass_rate"] = float(run_context.get("pass_rate", 0))
    features["suite_failure_rate"] = float(run_context.get("suite_failure_rate", 0))
    features["run_failed_test_count"] = float(run_context.get("failed_tests", 0))

    # ── Group 3: Historical signals ─────────────────────────────────────
    hist_pass = history.get("pass_count", 0)
    hist_fail = history.get("fail_count", 0)
    hist_total = hist_pass + hist_fail
    features["historical_failure_rate"] = (
        float(hist_fail / hist_total) if hist_total > 0 else -1.0
    )
    features["historical_pass_rate"] = (
        float(hist_pass / hist_total) if hist_total > 0 else -1.0
    )
    features["historical_run_count"] = float(hist_total)

    last_pass_at = history.get("last_pass_at")
    if last_pass_at and isinstance(last_pass_at, datetime):
        days_since = (datetime.now(timezone.utc) - last_pass_at).total_seconds() / 86400
        features["days_since_last_pass"] = float(days_since)
    else:
        features["days_since_last_pass"] = -1.0

    features["consecutive_failures"] = float(history.get("consecutive_failures", 0))
    features["is_new_test"] = 1.0 if hist_total < 3 else 0.0

    # Status volatility: standard deviation of pass/fail over recent history
    status_seq = history.get("status_sequence", [])
    if len(status_seq) >= 3:
        binary = [1.0 if s in ("FAILED", "BROKEN") else 0.0 for s in status_seq[-20:]]
        mean = sum(binary) / len(binary)
        variance = sum((x - mean) ** 2 for x in binary) / len(binary)
        features["status_volatility"] = float(variance ** 0.5)
    else:
        features["status_volatility"] = -1.0

    features["failure_rate_trend"] = float(history.get("failure_rate_trend", 0))

    # ── Group 4: Environment signals ────────────────────────────────────
    start_time = run_context.get("start_time")
    if isinstance(start_time, datetime):
        features["hour_of_day"] = float(start_time.hour)
        features["is_weekend"] = 1.0 if start_time.weekday() >= 5 else 0.0
    else:
        features["hour_of_day"] = -1.0
        features["is_weekend"] = -1.0

    features["cross_suite_failure_rate"] = float(
        run_context.get("cross_suite_failure_rate", 0)
    )
    features["same_error_other_tests"] = float(
        run_context.get("same_error_count", 0)
    )

    return features


def features_to_array(features: dict[str, float]) -> list[float]:
    """Convert feature dict to ordered array matching FEATURE_NAMES order."""
    return [features.get(name, -1.0) for name in FEATURE_NAMES]
