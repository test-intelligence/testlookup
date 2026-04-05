"""
MCP Prompt Templates — reusable investigation workflows.

Prompts chain multiple tools into a structured workflow. AI Assistants expand them
into tool calls automatically when a user invokes the prompt.
"""

from __future__ import annotations

from mcp.types import GetPromptResult, PromptMessage, TextContent  # type: ignore[import]


def register(mcp) -> None:  # noqa: ANN001

    @mcp.prompt()
    def investigate_failure(test_case_id: str, project_id: str) -> str:
        """
        Full investigation workflow for a failing test case.
        Retrieves the error, triggers AI root-cause analysis, and summarises findings.

        Args:
            test_case_id: UUID of the failing test case.
            project_id: Project UUID the test belongs to.
        """
        return f"""You are a QA engineer investigating a test failure. Follow these steps exactly:

1. Call `get_dashboard_metrics` with project_id="{project_id}" to understand current quality context.
2. Call `search_tests` with the test_case_id="{test_case_id}" as the query to find the run context (get the run_id).
3. Call `get_test_case` with the run_id and test_case_id="{test_case_id}" to read the full error message and labels.
4. Call `trigger_ai_analysis` with test_case_id="{test_case_id}" to run the ReAct agent. Wait for the result.
5. If the analysis shows is_flaky=true, call `get_flaky_tests` with project_id="{project_id}" for flakiness context.
6. If the analysis shows backend_error_found=true, note that Splunk log correlation was attempted.
7. If the analysis shows pod_issue_found=true, note the infrastructure context.

Compile a structured investigation report with:
- **Test:** Name, suite, class
- **Error:** The exact error/exception
- **Root Cause:** The AI's summary (in plain English)
- **Category:** PRODUCT_BUG / INFRASTRUCTURE / TEST_DATA / AUTOMATION_DEFECT / FLAKY
- **Confidence:** Score and whether human review is needed
- **Evidence:** What tools the agent used to reach its conclusion
- **Actions:** The 3 recommended actions from the analysis
- **Context:** How this relates to the project's overall quality (from dashboard metrics)

Be concise and actionable. A developer should be able to read this in under 2 minutes and know exactly what to do next."""

    @mcp.prompt()
    def release_readiness_report(project_id: str, release_version: str = "next") -> str:
        """
        Generate an executive-ready go/no-go release assessment for a project.

        Args:
            project_id: Project UUID.
            release_version: Release label for the report header (e.g., "v2.4.0").
        """
        return f"""You are a QA lead preparing a release readiness report for version **{release_version}**.
Run these steps in order, then compile the final report:

1. Call `check_release_readiness` with project_id="{project_id}" — this is the primary signal.
2. Call `get_dashboard_metrics` with project_id="{project_id}", days=7.
3. Call `get_test_trends` with project_id="{project_id}", days=14 — look for trend direction.
4. Call `get_failure_categories` with project_id="{project_id}", days=7 — categorise risk.
5. Call `get_defects` with project_id="{project_id}", resolution_status="OPEN" — list blocking issues.
6. Call `get_flaky_tests` with project_id="{project_id}", days=7 — identify risk from flakiness.
7. Call `get_ai_analysis_summary` with project_id="{project_id}", days=7 — check triage coverage.

Produce a release readiness report with this structure:

---
# Release Readiness Report — {release_version}
**Decision: 🟢 GO / 🟡 CONDITIONAL GO / 🔴 NO-GO**

## Quality Signal
[Pass rate, trend, execution volume]

## Risk Assessment
[Breakdown by failure category — what % are product bugs vs infra noise?]

## Blockers
[List any explicit blockers with Jira ticket IDs if available]

## Warnings
[Items to monitor post-release]

## Flakiness Risk
[How many flaky tests could mask real failures?]

## AI Triage Coverage
[What % of failures have been analysed? Any unreviewed high-risk items?]

## Recommendation
[1-2 sentence plain-English recommendation for the release manager]
---

Be specific with numbers. Use the actual data from the tools — do not fabricate metrics."""

    @mcp.prompt()
    def weekly_quality_digest(project_id: str) -> str:
        """
        Generate a weekly quality digest suitable for sharing with the team.
        Covers pass rate trend, top issues, flakiness, and AI triage stats.

        Args:
            project_id: Project UUID.
        """
        return f"""You are a QA engineer preparing the weekly quality digest for the team.
Collect data by running these tools:

1. `get_dashboard_metrics` — project_id="{project_id}", days=7 (current week)
2. `get_dashboard_metrics` — project_id="{project_id}", days=14 (to compute week-over-week change)
3. `get_test_trends` — project_id="{project_id}", days=7
4. `get_top_failing_tests` — project_id="{project_id}", days=7, limit=5
5. `get_flaky_tests` — project_id="{project_id}", days=7, limit=10
6. `get_failure_categories` — project_id="{project_id}", days=7
7. `get_ai_analysis_summary` — project_id="{project_id}", days=7

Write the digest in this format:

---
# Weekly Quality Digest — Week of [date]

## 📊 At a Glance
| Metric | This Week | Last Week | Change |
...

## 📈 Pass Rate Trend
[Describe the daily trend — improving, declining, stable? Any spikes?]

## 🔴 Top 5 Failing Tests This Week
[List with failure count and category]

## 🎲 Flakiness Watch
[Any new flaky tests? Any improving? Total count vs last week]

## 🗂 Failure Categories
[What's the split? Are infra failures a theme this week?]

## 🤖 AI Triage Coverage
[How many failures were auto-triaged? Average confidence?]

## 🎯 Action Items for Next Week
[3-5 concrete, prioritised items the team should address]
---

Keep the tone factual and data-driven. This will be shared in Slack."""

    @mcp.prompt()
    def flakiness_investigation(project_id: str, threshold_pct: int = 30) -> str:
        """
        Deep-dive investigation into flaky tests above a failure rate threshold.
        Produces a prioritised remediation plan.

        Args:
            project_id: Project UUID.
            threshold_pct: Minimum failure rate to include (default 30%).
        """
        return f"""You are a senior QA engineer investigating test flakiness.
The goal is to produce a prioritised remediation plan for tests with ≥{threshold_pct}% failure rate.

Steps:
1. `get_flaky_tests` — project_id="{project_id}", days=30, limit=50
2. `get_failure_categories` — project_id="{project_id}", days=30 (see how much is FLAKY vs AUTOMATION_DEFECT)
3. `get_ai_analysis_summary` — project_id="{project_id}", days=30 (check how many flaky tests were AI-identified)
4. `get_dashboard_metrics` — project_id="{project_id}" (understand impact on overall pass rate)
5. For the top 3 flakiest tests, call `search_tests` with the test name to get recent run history.

Produce a report structured as:

---
# Flakiness Investigation Report

## Overview
[Total flaky tests, % above threshold, impact on overall pass rate]

## Prioritised Remediation List
For each test above {threshold_pct}% failure rate, in order of severity:
- **Test:** [name] | **Suite:** [suite] | **Rate:** [X%] | **Runs:** [N]
- **Pattern:** [Does it fail on specific days? After certain test order? Infrastructure-related?]
- **Recommended Fix:** [Specific action — add retry, fix assertion timing, stabilise test data, etc.]

## Root Cause Patterns
[Group by likely cause: race conditions, environment dependency, data dependency, timing issues]

## Quick Wins (fix in < 1 day)
[Tests that can be stabilised with a simple change]

## Strategic Improvements
[Tests that need architectural changes or dedicated investigation]

## Estimated Impact
[If top N tests were fixed, what would the pass rate improvement be?]
---"""

    @mcp.prompt()
    def defect_triage_session(project_id: str) -> str:
        """
        Structured defect triage session — review all open defects, identify
        duplicates, confirm still-failing, and recommend prioritisation.

        Args:
            project_id: Project UUID.
        """
        return f"""You are a QA lead running a defect triage session.
Goal: Review all open defects, confirm their status, and produce a prioritised triage list.

Steps:
1. `get_defects` — project_id="{project_id}", resolution_status="OPEN", size=50
2. `get_ai_analysis_summary` — project_id="{project_id}", days=30
3. `get_failure_categories` — project_id="{project_id}", days=30
4. `get_dashboard_metrics` — project_id="{project_id}" (understand severity context)
5. For the top 5 highest-confidence open defects, call `search_tests` to confirm the test is still failing.

Produce a triage summary:

---
# Defect Triage Session

## Summary
- Total Open: [N]
- High Confidence (AI ≥80%): [N]
- Awaiting Human Review: [N]
- Likely Duplicates: [N]

## P1 — Fix Immediately (PRODUCT_BUG + high confidence + still failing)
[List with Jira ID, test name, recommended owner]

## P2 — Fix This Sprint (confirmed bugs, lower confidence)
[List]

## P3 — Investigate (UNKNOWN category or low confidence)
[List — these need human review before classification]

## Recommend WONTFIX
[Tests that haven't been seen recently — may have been silently fixed]

## Recommend DUPLICATE
[Tests with identical error patterns that may map to the same root cause]
---"""

    @mcp.prompt()
    def suite_health_check(project_id: str, suite_name: str) -> str:
        """
        Focused health check for a specific test suite.
        Returns coverage, worst tests, and failure pattern analysis.

        Args:
            project_id: Project UUID.
            suite_name: Name of the test suite to analyse.
        """
        return f"""You are analysing the health of the **{suite_name}** test suite.

Steps:
1. `search_tests` — query="{suite_name}", project_id="{project_id}", days=30
2. `get_coverage_report` — project_id="{project_id}", days=30 (find {suite_name} in the per-suite breakdown)
3. `get_top_failing_tests` — project_id="{project_id}", days=30, limit=50 (filter to {suite_name})
4. `get_flaky_tests` — project_id="{project_id}", days=30, limit=50 (filter to {suite_name})
5. `get_failure_categories` — project_id="{project_id}", days=30

Produce a suite health report:

---
# Suite Health: {suite_name}

## Overview
- **Total Unique Tests:** [N]
- **Pass Rate:** [X%]
- **Executions (30d):** [N]
- **Overall Health:** 🟢 Healthy / 🟡 Needs Attention / 🔴 Critical

## Worst Performing Tests
[Top 5 by failure count with category and last failure date]

## Flaky Tests in This Suite
[Tests with intermittent failures — stabilisation opportunities]

## Failure Pattern
[What's driving failures? Product bugs, infra noise, test data, automation debt?]

## Recommended Actions
1. [Most impactful action]
2. [Second action]
3. [Third action]

## Suite Quality Score: [X/100]
[Composite score based on pass rate, flakiness, and failure category distribution]
---"""
