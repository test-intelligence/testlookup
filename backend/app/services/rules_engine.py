"""
Rules-based analysis engine — zero external dependencies.

Provides test failure classification, flakiness detection, anomaly flagging,
and template-based summary generation using only pattern matching, statistical
heuristics, and historical data from PostgreSQL.

This engine is the fallback when neither LLM nor ML models are available.
It always works — no model training required, no network calls.

Usage:
    from app.services.rules_engine import RulesEngine
    result = await RulesEngine.classify_test(test_case, history, run_context)
    summary = RulesEngine.generate_summary(run_data, classifications)
"""
import structlog
from typing import Any

from app.services.confidence_bands import (
    get_band,
    historical_flakiness_confidence,
)

logger = structlog.get_logger("services.rules_engine")


# ── Keyword Patterns ────────────────────────────────────────────────────────
# Confidence for each pattern lives in the band table
# (services/confidence_bands.py) keyed by rule_id, alongside its documented
# basis ("empirical" vs "heuristic_estimate"). AI-F4.

_PATTERNS: list[tuple[str, list[str], str, str]] = [
    # (rule_id, keywords, category, summary_template)
    (
        "pattern.oom",
        ["oomkilled", "out of memory", "memory limit", "oom", "killed process"],
        "INFRASTRUCTURE",
        "Container/process ran out of memory (OOMKilled). Check resource limits and memory leaks.",
    ),
    (
        "pattern.connection_refused",
        ["connection refused", "connection reset", "econnrefused", "econnreset", "broken pipe"],
        "INFRASTRUCTURE",
        "Network connectivity issue: connection refused or reset. Check upstream service health.",
    ),
    (
        "pattern.timeout",
        ["timeout", "timed out", "deadline exceeded", "socket timeout", "read timeout"],
        "INFRASTRUCTURE",
        "Request or operation timed out. Check service latency, network, and timeout configuration.",
    ),
    (
        "pattern.http_5xx",
        ["502 bad gateway", "503 service unavailable", "504 gateway timeout", "500 internal server"],
        "INFRASTRUCTURE",
        "HTTP server error (5xx). Upstream service returned an error — check health and logs.",
    ),
    (
        "pattern.dns",
        ["dns resolution", "name resolution", "unknown host", "getaddrinfo", "eai_again"],
        "INFRASTRUCTURE",
        "DNS resolution failure. Check DNS configuration and network connectivity.",
    ),
    # US-9.4 infra rule-pack additions — classic runner/host exhaustion and
    # unreachable-network shapes that previously fell through to UNKNOWN.
    (
        "pattern.disk_full",
        ["no space left on device", "disk quota exceeded", "enospc"],
        "INFRASTRUCTURE",
        "Disk space exhausted on the runner/host (ENOSPC). Clean workspace/artifacts and check volume sizing.",
    ),
    (
        "pattern.network_unreachable",
        ["network is unreachable", "host unreachable", "ehostunreach", "enetunreach", "no route to host"],
        "INFRASTRUCTURE",
        "Network/host unreachable. Check routing, firewall rules, and VPN/proxy configuration.",
    ),
    (
        "pattern.resource_exhaustion",
        ["too many open files", "emfile", "cannot allocate memory", "resource temporarily unavailable"],
        "INFRASTRUCTURE",
        "OS resource exhaustion (file descriptors / memory / process limits). Check ulimits and runner sizing.",
    ),
    (
        "pattern.tls",
        ["certificate", "ssl", "tls", "handshake failure"],
        "INFRASTRUCTURE",
        "TLS/SSL certificate or handshake failure. Check certificate validity and trust chain.",
    ),
    (
        "pattern.not_found",
        ["404 not found", "resource not found", "no such file", "filenotfound", "path not found"],
        "TEST_DATA",
        "Resource not found (404). Check test data setup and environment state.",
    ),
    (
        "pattern.setup_fixture",
        ["setup failed", "before method", "beforeeach", "beforeall", "fixture", "@before", "precondition"],
        "TEST_DATA",
        "Test setup or fixture failed before the test could run. Check test data prerequisites.",
    ),
    (
        "pattern.null_reference",
        ["nullpointerexception", "undefined is not", "cannot read propert", "typeerror: null", "nonetype"],
        "AUTOMATION_DEFECT",
        "Null/undefined reference encountered. Review test code for missing null checks.",
    ),
    (
        "pattern.ui_locator",
        ["element not found", "no such element", "locator", "selector", "stale element", "element not interactable"],
        "AUTOMATION_DEFECT",
        "UI element locator failed. Check for UI changes, timing issues, or incorrect selectors.",
    ),
    (
        "pattern.missing_dependency",
        ["class not found", "classnotfound", "nosuchmethod", "import error", "module not found"],
        "AUTOMATION_DEFECT",
        "Missing class or module. Check dependencies and classpath configuration.",
    ),
    (
        "pattern.flaky_keywords",
        ["flaky", "intermittent", "race condition", "eventually", "retry exceeded", "sporadic"],
        "FLAKY",
        "Failure matches flaky/intermittent test patterns. Verify with re-run before investigating.",
    ),
    (
        "pattern.assertion",
        ["assertion", "expected", "but was", "assertequals", "assertthat", "assert_equal", "should be"],
        "PRODUCT_BUG",
        "Assertion failure: expected vs actual value mismatch. Likely a product bug or spec change.",
    ),
    (
        "pattern.auth",
        ["permission denied", "access denied", "forbidden", "unauthorized", "401", "403"],
        "TEST_DATA",
        "Authentication or authorization failure. Check test credentials and access permissions.",
    ),
]


# ── Classification ──────────────────────────────────────────────────────────


class RulesEngine:
    """Stateless rules-based analysis engine."""

    @staticmethod
    def classify_test(
        error_message: str | None,
        test_name: str | None = None,
        duration_ms: int | None = None,
        severity: str | None = None,
        history: dict | None = None,
        run_context: dict | None = None,
    ) -> dict[str, Any]:
        """Classify a single test failure using rules and heuristics.

        Args:
            error_message: The test's error/exception message.
            test_name: Test name (for pattern matching).
            duration_ms: Execution time in milliseconds.
            severity: Allure severity level.
            history: Historical stats dict with pass_count, fail_count, etc.
            run_context: Run-level context with pass_rate, failed_tests, etc.

        Returns:
            Dict matching AIAnalysis shape: failure_category, confidence_score,
            root_cause_summary, is_flaky, recommended_actions, etc.
        """
        history = history or {}
        run_context = run_context or {}
        error_lower = (error_message or "").lower()

        # ── 1. Historical flakiness check (strongest signal) ────────────
        hist_pass = history.get("pass_count", 0)
        hist_fail = history.get("fail_count", 0)
        hist_total = hist_pass + hist_fail
        if hist_total >= 5:
            hist_failure_rate = hist_fail / hist_total
            if 0.10 <= hist_failure_rate <= 0.90 and hist_pass > 0 and hist_fail > 0:
                return _build_result(
                    category="FLAKY",
                    summary=f"Test has a {hist_failure_rate*100:.0f}% historical failure rate "
                            f"({hist_fail}/{hist_total} runs). Exhibits flaky behavior — "
                            f"passes and fails non-deterministically.",
                    rule_id="heuristic.historical_flakiness",
                    confidence=historical_flakiness_confidence(hist_total),
                    is_flaky=True,
                    actions=[
                        "Quarantine test to prevent blocking releases",
                        "Investigate race conditions or timing dependencies",
                        "Add retry logic or stabilize test data",
                    ],
                )

        # ── 2. Regression check (new failure after passing streak) ──────
        consecutive_passes = history.get("consecutive_passes", 0)
        if consecutive_passes >= 3 and hist_fail <= 1:
            return _build_result(
                category="PRODUCT_BUG",
                summary=f"Test passed {consecutive_passes} consecutive times before this failure. "
                        f"Likely a new regression introduced by recent code changes.",
                rule_id="heuristic.regression_after_streak",
                is_flaky=False,
                actions=[
                    "Check recent commits for the affected component",
                    "Review the diff between last passing and current build",
                    "Write a targeted unit test for the regression",
                ],
            )

        # ── 3. Duration anomaly (performance regression / infra) ────────
        median_duration = history.get("median_duration_ms")
        if duration_ms and median_duration and median_duration > 0:
            ratio = duration_ms / median_duration
            if ratio > 3.0 and duration_ms > 5000:
                return _build_result(
                    category="INFRASTRUCTURE",
                    summary=f"Test took {duration_ms}ms — {ratio:.1f}x the historical median "
                            f"({median_duration}ms). Likely a performance degradation or infra issue.",
                    rule_id="heuristic.duration_anomaly",
                    is_flaky=False,
                    actions=[
                        "Check service latency and resource utilization",
                        "Review recent infrastructure changes",
                        "Verify network connectivity and DNS resolution",
                    ],
                )

        # ── 4. Suite-level failure (all tests in suite fail → infra/data) ─
        suite_failure_rate = run_context.get("suite_failure_rate", 0)
        if suite_failure_rate >= 0.8 and run_context.get("suite_test_count", 0) >= 3:
            return _build_result(
                category="INFRASTRUCTURE" if "timeout" in error_lower or "connection" in error_lower else "TEST_DATA",
                summary=f"Suite-level failure: {suite_failure_rate*100:.0f}% of tests in this suite failed. "
                        f"Likely a shared dependency, environment, or test data issue.",
                rule_id="heuristic.suite_level_failure",
                is_flaky=False,
                actions=[
                    "Check shared test fixtures and setup/teardown",
                    "Verify environment and external service availability",
                    "Review suite-level configuration changes",
                ],
            )

        # ── 5. Keyword pattern matching ─────────────────────────────────
        for rule_id, keywords, category, summary in _PATTERNS:
            if any(kw in error_lower for kw in keywords):
                return _build_result(
                    category=category,
                    summary=summary,
                    rule_id=rule_id,
                    is_flaky=(category == "FLAKY"),
                    actions=_default_actions(category),
                )

        # ── 6. Cross-suite blast radius (many suites failing) ───────────
        cross_suite_rate = run_context.get("cross_suite_failure_rate", 0)
        if cross_suite_rate >= 0.5 and error_message:
            return _build_result(
                category="INFRASTRUCTURE",
                summary=f"Failures detected across {cross_suite_rate*100:.0f}% of test suites "
                        f"in this run. Broad blast radius suggests infrastructure or environment issue.",
                rule_id="heuristic.cross_suite_blast",
                is_flaky=False,
                actions=[
                    "Check shared infrastructure components (database, network, services)",
                    "Review deployment or environment changes",
                ],
            )

        # ── 7. Fallback: UNKNOWN ────────────────────────────────────────
        return _build_result(
            category="UNKNOWN",
            summary="Could not determine failure cause from available data. Manual review required.",
            rule_id="heuristic.unknown_fallback",
            is_flaky=False,
            actions=["Review stack trace and error message manually", "Check recent code changes"],
        )

    # ── Summary Generation ──────────────────────────────────────────────────

    @staticmethod
    def generate_summary(
        run_data: dict,
        classifications: dict[str, dict],
        anomalies: list[dict] | None = None,
    ) -> dict:
        """Generate a 4-layer structured summary from rules-based classifications.

        Returns a dict with layer1_executive_summary, layer2_incident_view,
        layer3_evidence_pack, and layer4_action_plan keys — same shape as
        the LLM-generated summaries for full backward compatibility.
        """
        total = int(run_data.get("total_tests") or 0)
        failed = int(run_data.get("failed_tests") or 0)
        pass_rate = float(run_data.get("pass_rate") or 0.0)
        build = run_data.get("build_number") or "unknown"
        branch = run_data.get("branch") or "unknown"
        anomalies = anomalies or []

        # Aggregate category counts
        category_counts: dict[str, int] = {}
        flaky_ids: list[str] = []
        stack_traces: list[str] = []
        all_actions: list[str] = []
        data_sources: set[str] = {"test_results"}

        for tc_id, cls in classifications.items():
            cat = cls.get("failure_category", "UNKNOWN")
            category_counts[cat] = category_counts.get(cat, 0) + 1
            if cls.get("is_flaky"):
                flaky_ids.append(str(tc_id))
            for a in cls.get("recommended_actions", [])[:2]:
                if a not in all_actions:
                    all_actions.append(a)

        top_category = max(category_counts, key=lambda k: category_counts.get(k, 0)) if category_counts else "UNKNOWN"
        top_count = category_counts.get(top_category, 0)

        # Determine criticality and release impact
        if failed == 0:
            criticality, release_impact = "LOW", "GO"
        elif pass_rate >= 90:
            criticality, release_impact = "MEDIUM", "CONDITIONAL_GO"
        elif pass_rate >= 75:
            criticality, release_impact = "HIGH", "CONDITIONAL_GO"
        else:
            criticality, release_impact = "CRITICAL", "NO_GO"

        # ── Layer 1: Executive summary ──────────────────────────────────
        if failed == 0:
            exec_summary = (
                f"Build {build} on {branch} completed successfully with all "
                f"{total} tests passing ({pass_rate:.1f}% pass rate). "
                f"No failures detected across any test suite. "
                f"Release signal is GO."
            )
        elif pass_rate >= 90:
            exec_summary = (
                f"Build {build} on {branch} completed with {failed} failures "
                f"out of {total} tests ({pass_rate:.1f}% pass rate). "
                f"The dominant failure category is {top_category.lower().replace('_', ' ')} "
                f"({top_count} occurrences)"
                f"{f', with {len(flaky_ids)} known flaky tests' if flaky_ids else ''}. "
                f"Release signal is {release_impact.replace('_', ' ')}."
            )
        else:
            exec_summary = (
                f"Build {build} on {branch} has {failed} failing tests out of "
                f"{total} ({pass_rate:.1f}% pass rate), indicating significant "
                f"quality issues. {top_count} failures classified as "
                f"{top_category.lower().replace('_', ' ')}"
                f"{f', {len(flaky_ids)} flaky tests identified' if flaky_ids else ''}. "
                f"Immediate investigation required — release signal is "
                f"{release_impact.replace('_', ' ')}."
            )

        # ── Layer 2: Incident view ──────────────────────────────────────
        what_failed = _describe_what_failed(category_counts, failed)
        likely_cause = _infer_likely_cause(top_category, top_count, failed)

        incident_view = {
            "what_failed": what_failed,
            "likely_cause": likely_cause,
            "scope": run_data.get("jenkins_job") or run_data.get("ocp_namespace") or "test run",
            "criticality": criticality,
            "release_impact": release_impact,
            "failure_breakdown": {
                "product_bugs": category_counts.get("PRODUCT_BUG", 0),
                "infrastructure": category_counts.get("INFRASTRUCTURE", 0),
                "test_data": category_counts.get("TEST_DATA", 0),
                "automation_defect": category_counts.get("AUTOMATION_DEFECT", 0),
                "flaky": category_counts.get("FLAKY", 0),
                "unknown": category_counts.get("UNKNOWN", 0),
            },
        }

        # ── Layer 3: Evidence pack ──────────────────────────────────────
        anomaly_descriptions = [
            str(a.get("summary") or a.get("message") or a.get("description", ""))
            for a in anomalies[:5]
        ]
        if "test_case_history" in str(classifications):
            data_sources.add("historical_analysis")

        evidence_pack = {
            "top_stack_traces": stack_traces[:3],
            "log_anomalies": anomaly_descriptions,
            "flaky_test_ids": flaky_ids[:10],
            "similar_historical_failures": [],
            "data_sources_used": sorted(data_sources),
        }

        # ── Layer 4: Action plan ────────────────────────────────────────
        action_plan = _build_action_plan(top_category, all_actions, release_impact, flaky_ids)

        # ── Executive panel (structured, scannable) ────────────────────
        from app.services.executive_panel_builder import build_executive_panel

        executive_panel = build_executive_panel(
            run_data=run_data,
            category_counts=category_counts,
            flaky_count=len(flaky_ids),
            anomaly_count=len(anomalies),
            cluster_count=0,
            release_impact=release_impact,
            recommended_actions=all_actions[:3],
        )

        return {
            "layer1_executive_summary": exec_summary,
            "layer2_incident_view": incident_view,
            "layer3_evidence_pack": evidence_pack,
            "layer4_action_plan": action_plan,
            "executive_panel": executive_panel,
        }


# ── Private Helpers ─────────────────────────────────────────────────────────


def _build_result(
    category: str,
    summary: str,
    rule_id: str,
    is_flaky: bool,
    actions: list[str],
    confidence: int | None = None,
) -> dict[str, Any]:
    """Build an AIAnalysis-shaped result.

    ``confidence`` defaults to the rule's band value; only dynamic rules
    (historical flakiness) pass an explicit computed value. ``confidence_basis``
    ("empirical" | "heuristic_estimate") and ``confidence_rule_id`` carry the
    band's documented basis through the analysis record (AI-F4).
    """
    band = get_band(rule_id)
    if confidence is None:
        confidence = band.confidence
    return {
        "root_cause_summary": summary,
        "failure_category": category,
        "backend_error_found": category == "INFRASTRUCTURE",
        "pod_issue_found": False,
        "is_flaky": is_flaky,
        "confidence_score": confidence,
        "recommended_actions": actions,
        "evidence_references": [],
        "requires_human_review": confidence < 70,
        "classified_by": "rules_engine",
        "tools_used": [],
        "llm_provider": "none",
        "llm_model": "rules_engine",
        "confidence_basis": band.basis,
        "confidence_rule_id": rule_id,
    }


def _default_actions(category: str) -> list[str]:
    actions_map = {
        "INFRASTRUCTURE": [
            "Check service health and infrastructure status",
            "Review recent deployment or environment changes",
            "Verify network connectivity and DNS resolution",
        ],
        "TEST_DATA": [
            "Verify test data setup and prerequisites",
            "Check shared fixtures and environment state",
            "Review test data generation scripts",
        ],
        "AUTOMATION_DEFECT": [
            "Review test code for null checks and timing issues",
            "Update locators if UI has changed",
            "Check test framework compatibility",
        ],
        "FLAKY": [
            "Quarantine flaky test to prevent blocking releases",
            "Investigate race conditions and timing dependencies",
            "Add explicit waits or retry logic",
        ],
        "PRODUCT_BUG": [
            "Review recent code changes in the affected component",
            "Check the assertion details for expected vs actual values",
            "Write a targeted unit test for the regression",
        ],
    }
    return actions_map.get(category, ["Review error details manually"])


def _describe_what_failed(category_counts: dict[str, int], total_failed: int) -> str:
    if not category_counts:
        return f"{total_failed} test(s) failed."
    parts = []
    for cat, count in sorted(category_counts.items(), key=lambda x: -x[1]):
        parts.append(f"{count} {cat.lower().replace('_', ' ')}")
    return f"{total_failed} tests failed: {', '.join(parts)}."


def _infer_likely_cause(top_category: str, top_count: int, total_failed: int) -> str:
    pct = (top_count / max(total_failed, 1)) * 100
    friendly = top_category.lower().replace("_", " ")
    if pct >= 80:
        return f"Primary cause: {friendly} ({pct:.0f}% of failures). Concentrated in a single failure mode."
    elif pct >= 50:
        return f"Dominant cause: {friendly} ({pct:.0f}% of failures), with additional failure categories present."
    else:
        return f"Mixed failure causes. Most common: {friendly} ({pct:.0f}%), but no single dominant category."


def _build_action_plan(
    top_category: str,
    all_actions: list[str],
    release_impact: str,
    flaky_ids: list[str],
) -> dict:
    mitigation_map = {
        "INFRASTRUCTURE": "Check infrastructure health and service connectivity. Consider rollback if environment is degraded.",
        "TEST_DATA": "Verify test data setup and shared fixtures. Re-run after confirming data integrity.",
        "AUTOMATION_DEFECT": "Review and fix test automation code. Update locators and timing if UI changed.",
        "FLAKY": "Quarantine flaky tests and re-run the suite. Investigate root causes of non-determinism.",
        "PRODUCT_BUG": "Investigate recent code changes in the affected area. Prioritize fix before release.",
        "UNKNOWN": "Manual review required. Examine stack traces and recent changes.",
    }

    owner_hints = {
        "qa": f"Re-run failing suites after fix. {'Quarantine ' + str(len(flaky_ids)) + ' flaky tests.' if flaky_ids else 'Monitor next run.'}",
        "developer": f"Investigate {top_category.lower().replace('_', ' ')} failures. Check recent commits.",
        "sre": "Monitor infrastructure health. Check resource utilization and service dependencies.",
        "release_manager": f"Release signal is {release_impact.replace('_', ' ')}. {'Proceed with caution.' if release_impact == 'CONDITIONAL_GO' else 'Await fixes.' if release_impact == 'NO_GO' else 'Clear to proceed.'}",
    }

    return {
        "immediate_mitigation": mitigation_map.get(top_category, mitigation_map["UNKNOWN"]),
        "fix_recommendations": all_actions[:5],
        "validation_steps": [
            "Re-run the failing test suite after applying fixes",
            "Verify pass rate meets the release threshold",
            "Confirm no new regressions introduced",
        ],
        "rollback_guidance": "Rollback the last relevant change if the same failures persist after investigation.",
        "owner_hints": owner_hints,
    }
