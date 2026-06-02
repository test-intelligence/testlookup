"""
Golden Dataset Definitions — Phase 5: Make Evaluation a Release Gate.

Reference labeled datasets for each evaluation category. These provide the
baseline ground-truth that prompt/model/routing changes are evaluated against.

Four categories:
  - classification: failure category correctness
  - root_cause: root cause extraction quality
  - duplicate_detection: duplicate defect detection precision/recall
  - release_decision: GO/NO_GO recommendation accuracy
"""
from __future__ import annotations


def get_golden_classification_items() -> list[dict]:
    """Golden dataset for failure classification accuracy."""
    return [
        # ── Product Bug cases ────────────────────────────────────────────
        {
            "input": {
                "root_cause_summary": "NullPointerException in PaymentService.processRefund() due to missing null check on customer account object",
                "failure_category": "PRODUCT_BUG",
                "confidence_score": 92,
                "is_flaky": False,
            },
            "expected_output": {"failure_category": "PRODUCT_BUG", "correct": True},
            "metadata": {"label": "clear_product_bug_npe"},
        },
        {
            "input": {
                "root_cause_summary": "Off-by-one error in pagination logic causes last page to return empty results",
                "failure_category": "PRODUCT_BUG",
                "confidence_score": 88,
                "is_flaky": False,
            },
            "expected_output": {"failure_category": "PRODUCT_BUG", "correct": True},
            "metadata": {"label": "pagination_off_by_one"},
        },
        {
            "input": {
                "root_cause_summary": "SQL injection vulnerability in search query allows unescaped input",
                "failure_category": "PRODUCT_BUG",
                "confidence_score": 95,
                "is_flaky": False,
            },
            "expected_output": {"failure_category": "PRODUCT_BUG", "correct": True},
            "metadata": {"label": "sql_injection_bug"},
        },
        # ── Infrastructure cases ─────────────────────────────────────────
        {
            "input": {
                "root_cause_summary": "Connection pool exhausted — all 50 database connections in use, causing 30s timeout",
                "failure_category": "INFRASTRUCTURE",
                "confidence_score": 90,
                "is_flaky": False,
            },
            "expected_output": {"failure_category": "INFRASTRUCTURE", "correct": True},
            "metadata": {"label": "db_pool_exhaustion"},
        },
        {
            "input": {
                "root_cause_summary": "Kubernetes pod OOMKilled after exceeding 512Mi memory limit during batch processing",
                "failure_category": "INFRASTRUCTURE",
                "confidence_score": 95,
                "is_flaky": False,
            },
            "expected_output": {"failure_category": "INFRASTRUCTURE", "correct": True},
            "metadata": {"label": "oom_kill"},
        },
        {
            "input": {
                "root_cause_summary": "DNS resolution failure for external payment gateway api.stripe.com",
                "failure_category": "INFRASTRUCTURE",
                "confidence_score": 87,
                "is_flaky": False,
            },
            "expected_output": {"failure_category": "INFRASTRUCTURE", "correct": True},
            "metadata": {"label": "dns_resolution_failure"},
        },
        # ── Flaky cases ──────────────────────────────────────────────────
        {
            "input": {
                "root_cause_summary": "Race condition in async event handler — test passes on retry, fails intermittently under load",
                "failure_category": "FLAKY",
                "confidence_score": 78,
                "is_flaky": True,
            },
            "expected_output": {"failure_category": "FLAKY", "correct": True},
            "metadata": {"label": "race_condition_flaky"},
        },
        {
            "input": {
                "root_cause_summary": "Timing-dependent assertion fails when CI runner is under heavy load",
                "failure_category": "FLAKY",
                "confidence_score": 72,
                "is_flaky": True,
            },
            "expected_output": {"failure_category": "FLAKY", "correct": True},
            "metadata": {"label": "timing_sensitive_flaky"},
        },
        # ── Automation Defect / Test Data cases ──────────────────────────
        {
            "input": {
                "root_cause_summary": "Test selector #submit-btn changed to .btn-primary in latest UI refactor",
                "failure_category": "AUTOMATION_DEFECT",
                "confidence_score": 91,
                "is_flaky": False,
            },
            "expected_output": {"failure_category": "AUTOMATION_DEFECT", "correct": True},
            "metadata": {"label": "stale_selector"},
        },
        {
            "input": {
                "root_cause_summary": "Hard-coded test data references deleted user account that no longer exists in staging",
                "failure_category": "TEST_DATA",
                "confidence_score": 85,
                "is_flaky": False,
            },
            "expected_output": {"failure_category": "TEST_DATA", "correct": True},
            "metadata": {"label": "stale_test_data"},
        },
        # ── Misclassification cases (AI should get these wrong) ──────────
        {
            "input": {
                "root_cause_summary": "Connection timeout to database during peak hours",
                "failure_category": "PRODUCT_BUG",
                "confidence_score": 55,
                "is_flaky": False,
            },
            "expected_output": {"failure_category": "INFRASTRUCTURE", "correct": False},
            "metadata": {"label": "misclassified_infra_as_bug"},
        },
        {
            "input": {
                "root_cause_summary": "Intermittent assertion failure in async callback test — 60% pass rate over 10 runs",
                "failure_category": "PRODUCT_BUG",
                "confidence_score": 45,
                "is_flaky": True,
            },
            "expected_output": {"failure_category": "FLAKY", "correct": False},
            "metadata": {"label": "misclassified_flaky_as_bug"},
        },
    ]


def get_golden_root_cause_items() -> list[dict]:
    """Golden dataset for root cause extraction quality."""
    return [
        {
            "input": {
                "test_name": "test_payment_refund_success",
                "error_message": "NullPointerException: Cannot invoke method on null",
                "stack_trace": "at PaymentService.processRefund(PaymentService.java:142)",
            },
            "expected_output": {
                "has_root_cause": True,
                "mentions_file": True,
                "mentions_line": True,
                "actionable": True,
                "category": "PRODUCT_BUG",
            },
            "metadata": {"label": "clear_npe_root_cause"},
        },
        {
            "input": {
                "test_name": "test_api_response_time",
                "error_message": "AssertionError: Expected response time < 200ms, got 5432ms",
                "stack_trace": "at PerformanceTest.testApiLatency(PerformanceTest.java:88)",
            },
            "expected_output": {
                "has_root_cause": True,
                "mentions_file": True,
                "mentions_line": True,
                "actionable": True,
                "category": "INFRASTRUCTURE",
            },
            "metadata": {"label": "latency_root_cause"},
        },
        {
            "input": {
                "test_name": "test_login_flow",
                "error_message": "ElementNotFound: #login-button",
                "stack_trace": "at LoginPage.clickLogin(LoginPage.ts:25)",
            },
            "expected_output": {
                "has_root_cause": True,
                "mentions_file": True,
                "mentions_line": True,
                "actionable": True,
                "category": "AUTOMATION_DEFECT",
            },
            "metadata": {"label": "selector_change_root_cause"},
        },
        {
            "input": {
                "test_name": "test_data_import",
                "error_message": "FileNotFoundError: /data/fixtures/users.csv",
                "stack_trace": "at DataImporter.load(DataImporter.py:12)",
            },
            "expected_output": {
                "has_root_cause": True,
                "mentions_file": True,
                "mentions_line": True,
                "actionable": True,
                "category": "TEST_DATA",
            },
            "metadata": {"label": "missing_fixture_root_cause"},
        },
        {
            "input": {
                "test_name": "test_concurrent_writes",
                "error_message": "AssertionError: Expected 10, got 8",
                "stack_trace": "(no trace available)",
            },
            "expected_output": {
                "has_root_cause": False,
                "mentions_file": False,
                "mentions_line": False,
                "actionable": False,
                "category": "UNKNOWN",
            },
            "metadata": {"label": "insufficient_evidence"},
        },
    ]


def get_golden_duplicate_detection_items() -> list[dict]:
    """Golden dataset for duplicate defect detection precision/recall."""
    return [
        # True duplicates
        {
            "input": {
                "title_a": "NullPointerException in PaymentService.processRefund",
                "title_b": "NPE when processing refund in payment service",
                "component_a": "payment-service",
                "component_b": "payment-service",
            },
            "expected_output": {"is_duplicate": True, "similarity_threshold": 0.7},
            "metadata": {"label": "semantic_duplicate_npe"},
        },
        {
            "input": {
                "title_a": "Login timeout on staging environment",
                "title_b": "Authentication service timeout in staging",
                "component_a": "auth-service",
                "component_b": "auth-service",
            },
            "expected_output": {"is_duplicate": True, "similarity_threshold": 0.6},
            "metadata": {"label": "semantic_duplicate_timeout"},
        },
        {
            "input": {
                "title_a": "Database connection pool exhausted under load",
                "title_b": "DB pool max connections reached during peak traffic",
                "component_a": "data-layer",
                "component_b": "database",
            },
            "expected_output": {"is_duplicate": True, "similarity_threshold": 0.6},
            "metadata": {"label": "semantic_duplicate_db_pool"},
        },
        # Not duplicates
        {
            "input": {
                "title_a": "NullPointerException in PaymentService.processRefund",
                "title_b": "OutOfMemoryError in ReportGenerator batch job",
                "component_a": "payment-service",
                "component_b": "reporting",
            },
            "expected_output": {"is_duplicate": False, "similarity_threshold": 0.7},
            "metadata": {"label": "different_components"},
        },
        {
            "input": {
                "title_a": "Login page CSS broken in Chrome",
                "title_b": "Payment API returns 500 for large orders",
                "component_a": "frontend",
                "component_b": "payment-service",
            },
            "expected_output": {"is_duplicate": False, "similarity_threshold": 0.7},
            "metadata": {"label": "completely_different"},
        },
        {
            "input": {
                "title_a": "Timeout in user search API",
                "title_b": "Timeout in order search API",
                "component_a": "user-service",
                "component_b": "order-service",
            },
            "expected_output": {"is_duplicate": False, "similarity_threshold": 0.7},
            "metadata": {"label": "similar_symptom_different_service"},
        },
    ]


def get_golden_release_decision_items() -> list[dict]:
    """Golden dataset for release decision (GO/NO_GO) accuracy."""
    return [
        # Clear GO
        {
            "input": {
                "pass_rate": 99.2,
                "total_tests": 500,
                "failed_tests": 4,
                "critical_failures": 0,
                "flaky_count": 2,
                "regression_count": 0,
                "risk_score": 12,
            },
            "expected_output": {"recommendation": "GO", "max_acceptable_risk": 20},
            "metadata": {"label": "near_perfect_run"},
        },
        {
            "input": {
                "pass_rate": 97.5,
                "total_tests": 200,
                "failed_tests": 5,
                "critical_failures": 0,
                "flaky_count": 3,
                "regression_count": 0,
                "risk_score": 18,
            },
            "expected_output": {"recommendation": "GO", "max_acceptable_risk": 20},
            "metadata": {"label": "good_run_with_flaky"},
        },
        # Clear NO_GO
        {
            "input": {
                "pass_rate": 72.0,
                "total_tests": 500,
                "failed_tests": 140,
                "critical_failures": 15,
                "flaky_count": 5,
                "regression_count": 12,
                "risk_score": 78,
            },
            "expected_output": {"recommendation": "NO_GO", "min_risk_for_nogo": 55},
            "metadata": {"label": "high_failure_rate"},
        },
        {
            "input": {
                "pass_rate": 85.0,
                "total_tests": 300,
                "failed_tests": 45,
                "critical_failures": 8,
                "flaky_count": 2,
                "regression_count": 10,
                "risk_score": 65,
            },
            "expected_output": {"recommendation": "NO_GO", "min_risk_for_nogo": 55},
            "metadata": {"label": "many_regressions"},
        },
        # Conditional GO
        {
            "input": {
                "pass_rate": 92.0,
                "total_tests": 400,
                "failed_tests": 32,
                "critical_failures": 2,
                "flaky_count": 10,
                "regression_count": 3,
                "risk_score": 38,
            },
            "expected_output": {"recommendation": "CONDITIONAL_GO", "risk_range": [20, 55]},
            "metadata": {"label": "borderline_with_conditions"},
        },
        {
            "input": {
                "pass_rate": 94.5,
                "total_tests": 250,
                "failed_tests": 14,
                "critical_failures": 1,
                "flaky_count": 8,
                "regression_count": 2,
                "risk_score": 30,
            },
            "expected_output": {"recommendation": "CONDITIONAL_GO", "risk_range": [20, 55]},
            "metadata": {"label": "moderate_risk_with_flaky"},
        },
    ]


# ── Registry ─────────────────────────────────────────────────────────────────

GOLDEN_DATASETS: dict[str, dict] = {
    "classification": {
        "name": "Golden: Failure Classification",
        "description": "Reference dataset for failure category classification accuracy (12 labeled items)",
        "task_type": "classification",
        "get_items": get_golden_classification_items,
    },
    "root_cause": {
        "name": "Golden: Root Cause Extraction",
        "description": "Reference dataset for root cause analysis quality (5 labeled items)",
        "task_type": "root_cause",
        "get_items": get_golden_root_cause_items,
    },
    "duplicate_detection": {
        "name": "Golden: Duplicate Detection",
        "description": "Reference dataset for duplicate defect detection precision/recall (6 labeled items)",
        "task_type": "duplicate_detection",
        "get_items": get_golden_duplicate_detection_items,
    },
    "release_decision": {
        "name": "Golden: Release Decision",
        "description": "Reference dataset for GO/NO_GO recommendation accuracy (6 labeled items)",
        "task_type": "release_decision",
        "get_items": get_golden_release_decision_items,
    },
}


def get_all_golden_datasets() -> list[dict]:
    """Return all golden datasets ready for DB insertion."""
    result = []
    for key, spec in GOLDEN_DATASETS.items():
        items = spec["get_items"]()
        result.append({
            "name": spec["name"],
            "description": spec["description"],
            "task_type": spec["task_type"],
            "items": items,
            "item_count": len(items),
        })
    return result
