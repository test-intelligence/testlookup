"""
Executive Panel Builder — deterministically constructs the structured executive
summary panel from run data and analysis results.

This is a pure function (no DB, no async, no LLM) called by all three summary
generation paths: LLM, fallback, and rules engine. It ensures every summary
contains the same structured panel regardless of which engine produced it.

Usage:
    from app.services.executive_panel_builder import build_executive_panel
    panel = build_executive_panel(run_data, category_counts, ...)
"""
from typing import Any

from app.models.llm_schemas import (
    BaselineComparison,
    DominantFailure,
    ExecutivePanel,
    ExecutivePanelMetrics,
)

# Friendly category labels for key_takeaways
_CATEGORY_LABELS: dict[str, str] = {
    "PRODUCT_BUG": "product bug",
    "INFRASTRUCTURE": "infrastructure",
    "TEST_DATA": "test data",
    "AUTOMATION_DEFECT": "automation defect",
    "FLAKY": "flaky",
    "UNKNOWN": "unknown",
}

# Default actions when none are provided
_FALLBACK_ACTIONS: dict[str, str] = {
    "INFRASTRUCTURE": "Check infrastructure health and service connectivity",
    "TEST_DATA": "Verify test data setup and shared fixtures",
    "AUTOMATION_DEFECT": "Review test automation code for defects",
    "FLAKY": "Quarantine flaky tests and investigate root causes",
    "PRODUCT_BUG": "Review recent code changes in affected components",
    "UNKNOWN": "Review error details and stack traces manually",
}


def build_executive_panel(
    run_data: dict[str, Any],
    category_counts: dict[str, int],
    flaky_count: int = 0,
    anomaly_count: int = 0,
    cluster_count: int = 0,
    release_impact: str = "CONDITIONAL_GO",
    risk_score: int | None = None,
    baseline_diff: dict[str, Any] | None = None,
    recommended_actions: list[str] | None = None,
) -> dict[str, Any]:
    """Build a structured executive summary panel from run data.

    Args:
        run_data: Dict with build_number, branch, total_tests, failed_tests,
                  passed_tests, skipped_tests, pass_rate, duration_ms.
        category_counts: Dict mapping failure category -> count.
        flaky_count: Number of tests flagged as flaky.
        anomaly_count: Number of detected anomalies.
        cluster_count: Number of failure clusters.
        release_impact: GO | CONDITIONAL_GO | NO_GO.
        risk_score: Composite risk score 0-100 (None if unavailable).
        baseline_diff: Dict with pass_rate_delta, new_failures, resolved_failures,
                       regression_classification (None if no baseline).
        recommended_actions: Top action items from analysis.

    Returns:
        Dict validated through ExecutivePanel model.
    """
    build = str(run_data.get("build_number") or "unknown")
    branch = str(run_data.get("branch") or "unknown")
    total = int(run_data.get("total_tests") or 0)
    failed = int(run_data.get("failed_tests") or 0)
    passed = int(run_data.get("passed_tests") or 0)
    skipped = int(run_data.get("skipped_tests") or 0)
    pass_rate = float(run_data.get("pass_rate") or 0.0)
    duration_ms = run_data.get("duration_ms")
    duration_sec = int(duration_ms / 1000) if duration_ms else None

    # ── Headline ────────────────────────────────────────────────────────
    if failed == 0:
        headline = f"Build {build} \u2014 All {total} tests passed"
    elif failed == 1:
        headline = f"Build {build} \u2014 1 failure detected"
    else:
        headline = f"Build {build} \u2014 {failed} failures detected"

    # ── Metrics ─────────────────────────────────────────────────────────
    metrics = ExecutivePanelMetrics(
        build_number=build,
        branch=branch,
        total_tests=total,
        passed=passed,
        failed=failed,
        skipped=skipped,
        pass_rate=round(pass_rate, 1),
        duration_seconds=duration_sec,
        failure_clusters=cluster_count,
        anomaly_count=anomaly_count,
    )

    # ── Dominant failure ────────────────────────────────────────────────
    dominant: DominantFailure | None = None
    top_category = ""
    if failed > 0 and category_counts:
        top_category = max(category_counts, key=lambda c: category_counts[c])
        top_count = category_counts[top_category]
        dominant = DominantFailure(
            category=top_category,
            count=top_count,
            percentage=round((top_count / max(failed, 1)) * 100, 1),
        )

    # ── Key takeaways ───────────────────────────────────────────────────
    takeaways: list[str] = []
    if failed == 0:
        takeaways.append(f"All {total} tests passed with {pass_rate:.1f}% pass rate")
    else:
        if dominant:
            label = _CATEGORY_LABELS.get(top_category, top_category.lower().replace("_", " "))
            takeaways.append(
                f"{dominant.count} of {failed} failures are {label}-related"
            )
        if flaky_count > 0:
            takeaways.append(f"{flaky_count} test{'s' if flaky_count != 1 else ''} flagged as flaky")
        if anomaly_count > 0:
            takeaways.append(f"{anomaly_count} anomal{'ies' if anomaly_count != 1 else 'y'} detected in logs")

    # Baseline delta takeaway
    if baseline_diff:
        delta = baseline_diff.get("pass_rate_delta")
        if delta is not None and delta != 0:
            direction = "improved" if delta > 0 else "dropped"
            takeaways.append(f"Pass rate {direction} {abs(delta):.1f}% from baseline")
        new_count = len(baseline_diff.get("new_failures", []))
        if new_count > 0:
            takeaways.append(f"{new_count} new failure{'s' if new_count != 1 else ''} since last good run")

    # Limit to 4 takeaways
    takeaways = takeaways[:4]

    # ── Baseline comparison ─────────────────────────────────────────────
    baseline: BaselineComparison | None = None
    if baseline_diff:
        new_failures_list = baseline_diff.get("new_failures", [])
        resolved_list = baseline_diff.get("resolved_failures", [])
        baseline = BaselineComparison(
            pass_rate_delta=round(float(baseline_diff.get("pass_rate_delta") or 0), 1),
            new_failures=len(new_failures_list) if isinstance(new_failures_list, list) else int(new_failures_list or 0),
            resolved=len(resolved_list) if isinstance(resolved_list, list) else int(resolved_list or 0),
            classification=str(baseline_diff.get("regression_classification", "unclassified")),
        )

    # ── Next actions ────────────────────────────────────────────────────
    actions: list[str] = []
    if recommended_actions:
        actions = list(recommended_actions[:3])
    elif failed > 0 and top_category:
        actions.append(_FALLBACK_ACTIONS.get(top_category, _FALLBACK_ACTIONS["UNKNOWN"]))
        if flaky_count > 0:
            actions.append("Quarantine flaky tests to prevent blocking releases")
        if cluster_count > 1:
            actions.append("Investigate failure clusters for shared root causes")
    actions = actions[:3]

    # ── Build and validate ──────────────────────────────────────────────
    panel = ExecutivePanel(
        headline=headline,
        status_signal=release_impact,
        risk_score=risk_score,
        metrics=metrics,
        dominant_failure=dominant,
        key_takeaways=takeaways,
        baseline_comparison=baseline,
        next_actions=actions,
    )

    return panel.model_dump()
