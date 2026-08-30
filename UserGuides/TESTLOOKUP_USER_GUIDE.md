  # TestLookup User Guide

Version: 1.0
Date: 2026-04-07
Audience: QA Engineers, Developers, QA Leads, Release Managers, Admins

---

## Table of Contents

1. [Introduction](#1-introduction)
2. [Getting Started](#2-getting-started)
3. [Navigation and Layout](#3-navigation-and-layout)
4. [Dashboard and Overview](#4-dashboard-and-overview)
5. [Test Runs](#5-test-runs)
6. [Run Intelligence (AI Analysis)](#6-run-intelligence-ai-analysis)
7. [Deep Investigation](#7-deep-investigation)
8. [Release Gate](#8-release-gate)
9. [Coverage](#9-coverage)
10. [Failure Analysis](#10-failure-analysis)
11. [Trends](#11-trends)
12. [Defects](#12-defects)
13. [Search](#13-search)
14. [Test Case Management](#14-test-case-management)
15. [Live Execution Monitoring](#15-live-execution-monitoring)
16. [AI Chat](#16-ai-chat)
17. [AI Pipeline Status](#17-ai-pipeline-status)
18. [Flaky Coach](#18-flaky-coach)
19. [Intelligence Hub](#19-intelligence-hub)
20. [Value Metrics](#20-value-metrics)
21. [Project Management](#21-project-management)
22. [Release Management](#22-release-management)
23. [Release Gate Policies](#23-release-gate-policies)
24. [Service Ownership](#24-service-ownership)
25. [User Management](#25-user-management)
26. [Settings](#26-settings)
27. [Client SDK and Test Reporters](#27-client-sdk-and-test-reporters)
28. [MCP Server (AI Assistant Integration)](#28-mcp-server-ai-assistant-integration)
29. [Keyboard Shortcuts](#29-keyboard-shortcuts)
30. [Troubleshooting](#30-troubleshooting)
31. [Glossary](#31-glossary)

---

## 1. Introduction

### What is TestLookup?

TestLookup is an AI-powered software testing intelligence platform that helps engineering teams understand why test runs fail, group related failures, promote defects, and make evidence-backed release decisions. It ingests test results from 50+ frameworks, applies AI/ML analysis to classify failures, and provides actionable insights at every level — from individual test cases to full release readiness assessments.

### Core Workflow

```
Ingest Test Results → AI Analyzes Failures → Cluster Related Issues →
Promote Defects → Make Release Decision → Track Trends Over Time
```

### Key Capabilities

- **Run Intelligence:** AI-generated summaries with evidence for every test run.
- **Deep Investigation:** Multi-agent pipeline that clusters failures, detects anomalies, validates API contracts, and recommends release decisions.
- **Release Gate:** Deterministic risk scoring with GO / NO_GO / CONDITIONAL_GO recommendations.
- **Three Analysis Engines:** LLM (full AI), ML (trained classifier), Rules (pattern matching) — with automatic fallback.
- **Live Streaming:** Real-time dashboard during test execution.
- **Search:** Keyword, semantic, and hybrid search across all entities.
- **Test Management:** Full test case, suite, plan, and strategy management.
- **Integrations:** Jira, Splunk, GitHub, OpenShift, Slack, Microsoft Teams, and more.

### Supported Test Frameworks

TestLookup ingests results from: Allure, JUnit XML, TestNG XML, Cucumber JSON, pytest, Robot Framework, NUnit3, xUnit.net, Visual Studio TRX, Mocha, Jest, Cypress, Playwright, and any framework that produces JUnit-compatible XML.

---

## 2. Getting Started

### 2.1 Creating Your Account

1. Navigate to the TestLookup login page.
2. Click **Register** below the login form.
3. Fill in your details: email, username, full name, and password.
4. Click **Create Account**.
5. You will be created with the **Viewer** role (read-only). An admin can promote your role later.
6. On first login, you will be redirected to set a permanent password.

### 2.2 First Login and Password Reset

1. Log in with your registration credentials.
2. You will be automatically redirected to the **Reset Password** page.
3. Enter your new permanent password (minimum 8 characters).
4. Click **Reset Password**.
5. You will be redirected to the Dashboard.

### 2.3 Onboarding Wizard

After your first login, visit **Getting Started** in the sidebar. The onboarding wizard walks you through five steps:

| Step | Action | Where |
|---|---|---|
| 1. Create a Project | Set up your first project with name, description, and optional Jira integration | Projects page |
| 2. Upload a Test Run | Ingest your first test results (via API, webhook, or file upload) | Runs page |
| 3. Connect Jira | Link your Jira instance for defect promotion | Settings > Integrations |
| 4. Connect Telemetry | Configure Splunk or other log sources for AI correlation | Settings > Integrations |
| 5. View Intelligence | See your first AI-generated run analysis | Intelligence Hub |

Each step shows a completion status and date. Steps can be skipped and revisited later.

### 2.4 Quick Login (Development Only)

In development environments, five quick-login buttons are available on the login page for Admin, QA Lead, QA Engineer, Tester, and Viewer roles. These use pre-seeded demo accounts and require no password.

---

## 3. Navigation and Layout

### 3.1 Sidebar Structure

The sidebar is organized into four sections:

**Main Navigation:**
- Dashboard (Overview)
- Test Runs
- Coverage
- Failures
- Trends
- Defects
- Search
- Test Cases
- Live

**AI Agents:**
- AI Pipeline
- Deep Analysis
- Release Gate
- Chat

**Management (QA Lead and Admin only):**
- Projects
- Releases
- Users

**Footer:**
- Settings

### 3.2 Project Selector

The **project selector** appears at the top of the interface. It controls which project's data you see across all pages.

- Select a specific project to scope all data to that project.
- Select **All Projects** to see aggregated data across all your projects.
- Your project selection persists across page navigations and browser sessions.
- Non-admin users only see projects they are members of.

### 3.3 User Menu

Click your avatar or username in the top-right corner to access:
- Your profile information and role.
- **Change Password** option.
- **Logout** action.

---

## 4. Dashboard and Overview

**Route:** `/overview`
**Access:** All roles

The Dashboard is your landing page and provides a high-level view of testing health across your selected project(s).

### 4.1 Key Performance Indicators

Six metric cards at the top show:

| Metric | Description |
|---|---|
| **Total Executions** | Number of test runs in the selected period |
| **Avg Pass Rate** | Average pass rate across all runs |
| **Active Defects** | Number of open/in-progress defects |
| **Flaky Tests** | Number of tests flagged as flaky |
| **New Failures** | New failures detected in the latest runs |
| **Avg Duration** | Average test run execution time |

### 4.2 Time Range Selector

Use the time range selector (7, 14, 30, or 90 days) to adjust the reporting period for all dashboard widgets.

### 4.3 Customizable Widgets

Click the **grid icon** to open the widget picker. Enable or disable widgets to customize your dashboard. Your selections persist across sessions.

Available widgets include:
- Pass rate trend chart.
- Release readiness indicator (GO / CONDITIONAL_GO / NO_GO).
- Top failing tests.
- Recent runs summary.
- Failure category distribution.

---

## 5. Test Runs

**Route:** `/runs`
**Access:** All roles

The Test Runs page is the primary entry point for viewing and managing test execution results.

### 5.1 Browsing Runs

- **Status Filter:** Filter by All, Failed, Passed, or In Progress.
- **Sorting:** Click column headers to sort by build number, branch, project, pass rate, duration, or creation date.
- **Pagination:** Results are paginated (20 per page). Navigate with first/prev/next/last controls.

Each run row shows:
- Build number and branch name.
- Project name.
- Pass rate (color-coded: green >90%, amber 70-90%, red <70%).
- Total test count with pass/fail/skip breakdown.
- Duration.
- Creation timestamp.

### 5.2 Run Detail Page

**Route:** `/runs/:runId`

Click any run to view its details:

- **Summary header:** Build number, status badge (PASSED/FAILED/IN_PROGRESS), pass rate, total tests, duration.
- **Regression Diff Panel:** Shows what changed compared to the baseline (previous stable run):
  - New failing tests (not present in baseline).
  - Resolved failures (failed in baseline, now passing).
  - Persistent failures.
  - Commit range between builds.
- **Test Breakdown Table:** Lists all test cases in the run.
  - Filter by status: FAILED, BROKEN, PASSED, SKIPPED.
  - Columns: test name, class, suite, status, duration, error message (truncated).
  - Click any test to view its full detail page.

### 5.3 Test Case Detail Page

**Route:** `/runs/:runId/tests/:testId`

The detail view for a single test case includes:

- **Status and metadata:** Test name, class, suite, status badge, duration.
- **Error message and stack trace:** Full error output with syntax highlighting.
- **AI Analysis:** Root cause category, confidence score, recommended actions, and evidence.
- **Attachments:** Screenshots, logs, and other artifacts stored in MinIO/S3.
- **Flakiness History:** Historical pass/fail trend for this test across recent runs.

---

## 6. Run Intelligence (AI Analysis)

**Route:** `/runs/:runId/intelligence`
**Access:** All roles (trigger analysis requires QA_ENGINEER+)

Run Intelligence is the core AI feature of TestLookup. It provides a multi-layered, evidence-backed analysis of every test run.

### 6.1 Accessing Run Intelligence

1. Navigate to a test run.
2. Click the **Intelligence** tab or the sparkles icon.
3. If intelligence has not been generated yet, click **Analyze Run** to trigger AI analysis.

### 6.2 Release Decision Banner

At the top of the page, a color-coded banner shows the AI's release recommendation:

| Decision | Color | Meaning |
|---|---|---|
| **GO** | Green | Risk is low. Safe to proceed with release. |
| **CONDITIONAL_GO** | Amber | Moderate risk. Review the conditions before proceeding. |
| **NO_GO** | Red | High risk. Significant issues must be resolved before release. |

### 6.3 Four-Layer Summaries

Run Intelligence provides summaries tailored to four audiences:

| Layer | Audience | Focus |
|---|---|---|
| **Executive** | Engineering Managers, Directors | High-level risk, business impact, release recommendation |
| **Manager** | QA Leads, Release Managers | Blocking issues, resource allocation, timeline impact |
| **Developer** | Software Engineers | Root causes, code changes, regression identification |
| **Team** | QA Engineers, SREs | Specific failure details, investigation steps, remediation |

### 6.4 Criticality Matrix

A visual matrix showing failure categories plotted against confidence scores:
- **X-axis:** Failure category (Product Bug, Infrastructure, Test Data, Automation Defect, Flaky, Unknown).
- **Y-axis:** Confidence level (High, Medium, Low).
- **Cell content:** Number of affected tests.

### 6.5 Failure Clusters

Related failures are grouped into clusters:
- Each cluster shows: category, test count, confidence score, and representative error message.
- **Promote to Defect:** Click to create a Jira ticket from a cluster (if Jira is configured).
- **View Details:** Expand to see all tests in the cluster.

### 6.6 Baseline Diff

Shows what changed compared to the previous baseline run:
- New failures introduced in this run.
- Failures that were resolved.
- Persistent failures that remain.

### 6.7 Role-Based Actions

At the bottom of the intelligence page, role-specific action cards suggest next steps:
- **QA Engineer:** "Investigate these 3 high-confidence failures first."
- **Developer:** "These 2 failures correlate with commit abc123."
- **SRE:** "Infrastructure failures detected on pod xyz."
- **Release Manager:** "3 blocking issues must be resolved before release."

### 6.8 Export and Share

- **Export to PDF:** Generate a formatted intelligence report.
- **Copy to Clipboard:** Copy the executive summary as formatted text.
- **Share Link:** Create a time-limited, token-authenticated link to share the report externally.

### 6.9 Analysis Modes

TestLookup supports three analysis engines. The active mode is configured in Settings > AI Configuration:

| Mode | Speed | Accuracy | Dependencies |
|---|---|---|---|
| **LLM** | ~300ms/test | 85-95% | Running LLM (Ollama, OpenAI, or Gemini) |
| **ML** | ~2ms/test | >85% | Trained model (needs 200+ labeled samples) |
| **Rules** | ~0.2ms/test | 60-75% | None (always available) |
| **Auto** | Varies | Highest available | Uses best available engine with fallback |

The **Auto** mode tries ML first (if a trained model exists), then LLM (if available), then falls back to Rules.

---

## 7. Deep Investigation

**Route:** `/deep-investigate` or `/deep-investigate/:runId`
**Access:** QA_ENGINEER+ to trigger; all roles to view results

Deep Investigation runs a multi-agent AI pipeline that goes beyond basic analysis.

### 7.1 Triggering Deep Investigation

1. Navigate to a run's Intelligence page.
2. Click **Deep Investigate** to launch the multi-agent pipeline.
3. The pipeline runs asynchronously. You can monitor progress on the AI Pipeline page.

### 7.2 What the Pipeline Does

Six specialized AI agents work on the run in parallel:

| Agent | Purpose |
|---|---|
| **Cluster Agent** | Groups related failures by semantic similarity and error patterns |
| **Log Intelligence Agent** | Reconstructs distributed traces and detects log anomalies |
| **Contract Agent** | Validates API request/response payloads against OpenAPI schemas |
| **Flaky Sentinel** | Classifies each failure as new, recurring, or environmental |
| **Test Health Agent** | Scores automation code quality and identifies anti-patterns |
| **Release Risk Agent** | Computes a risk score and produces a GO/NO_GO/CONDITIONAL_GO recommendation |

### 7.3 Viewing Results

The Deep Investigation page shows:
- **Failure Clusters:** Cards showing each cluster with test count, confidence score, and severity.
- **Deep Findings:** Detailed insights from each agent (log anomalies, contract violations, etc.).
- **Defect Promotion:** Click **Promote to Defect** on any cluster to create a Jira ticket with all evidence attached.

### 7.4 Defect Promotion

When promoting a cluster to a defect:
1. Review the pre-filled defect details (title, description, severity, affected tests).
2. Select the target Jira project (defaults to the project's configured Jira key).
3. Optionally edit the description.
4. Click **Create Defect**.
5. The Jira ticket is created with links back to the TestLookup failure evidence.

If Jira is not configured, you can export the defect report as a local JSON or PDF file.

---

## 8. Release Gate

**Route:** `/release-gate` or `/release-gate/:runId`
**Access:** All roles can view; QA_LEAD+ can override

The Release Gate provides a deterministic GO / NO_GO / CONDITIONAL_GO recommendation for release decisions.

### 8.1 Risk Gauge

A visual gauge (0-100) shows the overall risk score:
- **0-19 (Green):** GO — Low risk.
- **20-54 (Amber):** CONDITIONAL_GO — Moderate risk. Review conditions.
- **55-100 (Red):** NO_GO — High risk. Do not release.

### 8.2 Seven-Dimension Risk Scoring

The risk score is computed from seven dimensions:

| Dimension | Description |
|---|---|
| **User Impact** | How many end users are affected by the failures |
| **Environment Sensitivity** | Are failures environment-specific or universal |
| **Reproducibility** | Can failures be consistently reproduced |
| **Regression Likelihood** | Are these regressions from recent code changes |
| **Historical Recurrence** | Have these failures occurred before |
| **Blast Radius** | How many components/services are affected |
| **Diagnosis Confidence** | How confident is the AI in its analysis |

Each dimension contributes a weighted score to the overall risk. Weights are configurable via Release Gate Policies.

### 8.3 QA Lead Override

If you are a QA Lead or Admin, you can override the AI recommendation:
1. Click **Override Decision**.
2. Select the new decision (GO, CONDITIONAL_GO, or NO_GO).
3. Enter a reason for the override (required).
4. Click **Confirm Override**.

All overrides are recorded in the audit trail with timestamp, actor, original decision, new decision, and reason.

### 8.4 Gate History

The Release Gate page shows a history of all gate decisions for the selected run or project, including:
- Timestamp.
- Decision (GO/NO_GO/CONDITIONAL_GO).
- Whether it was AI-recommended or human-overridden.
- Override reason (if applicable).

### 8.5 Export

Export the gate recommendation as a PDF report for stakeholder review or compliance records.

---

## 9. Coverage

**Route:** `/coverage`
**Access:** All roles

The Coverage page shows test suite coverage metrics and trends.

### 9.1 Suite Coverage Table

| Column | Description |
|---|---|
| **Suite Name** | Test suite identifier |
| **Unique Tests** | Number of distinct test cases in the suite |
| **Passed** | Count of passing tests |
| **Failed** | Count of failing tests |
| **Skipped** | Count of skipped tests |
| **Pass Rate** | Visual bar showing the pass/fail ratio |

### 9.2 Time Range

Select a time range (7, 14, 30, or 90 days) to filter the coverage data.

### 9.3 Suite Detail

**Route:** `/coverage/suite`

Click a suite to see detailed per-test coverage data for that suite, including individual test pass rates and historical trends.

---

## 10. Failure Analysis

**Route:** `/failures`
**Access:** All roles

The Failure Analysis page provides categorized views of test failures.

### 10.1 Failure Categories

A pie chart shows the distribution of failures by root cause category:
- **Product Bug** — Real defects in the application.
- **Infrastructure** — Environment, network, or deployment issues.
- **Test Data** — Missing or invalid test data.
- **Automation Defect** — Bugs in the test code itself.
- **Flaky** — Intermittent failures that pass on retry.
- **Unknown** — Failures not yet categorized.

### 10.2 Top Failing Tests

A ranked table of the most frequently failing tests showing:
- Test name and suite.
- Failure count.
- Failure rate (percentage).
- Last failure date.

### 10.3 Flaky Tests

A dedicated section showing tests with intermittent pass/fail behavior:
- Flaky score (calculated from historical inconsistency).
- Total runs vs. failed runs.
- Failure rate percentage.

---

## 11. Trends

**Route:** `/trends`
**Access:** All roles

The Trends page shows historical quality metrics as interactive charts.

### 11.1 Available Charts

| Chart | Type | Shows |
|---|---|---|
| **Daily Breakdown** | Stacked bar | Pass/fail/skip counts per day |
| **Pass Rate Trend** | Line | Pass rate percentage over time |
| **Cumulative Volume** | Area | Total test executions over time |
| **Failure Rate Trend** | Line | Failure rate percentage over time |
| **Broken Tests** | Bar | Count of broken (error, not assertion) tests per day |
| **Flaky Test Trend** | Line | Flaky test count over time |

### 11.2 Customizing Charts

Click the **Layout Grid** icon to open the chart picker. Enable or disable individual charts. Your selections persist in your browser across sessions.

### 11.3 Time Range

Use the time range selector (7, 14, 30, or 90 days) to adjust the reporting window.

---

## 12. Defects

**Route:** `/defects`
**Access:** All roles can view; QA_ENGINEER+ can manage

The Defects page tracks issues that have been promoted from failure clusters.

### 12.1 Defect Table

| Column | Description |
|---|---|
| **Test Name** | Name of the affected test or cluster representative |
| **Suite** | Test suite |
| **Category** | Failure category (Product Bug, Infrastructure, etc.) |
| **Status** | OPEN, IN_PROGRESS, RESOLVED, CLOSED |
| **Jira Link** | Direct link to the Jira ticket (if promoted) |
| **Confidence** | AI confidence score (0-100%) |
| **Created** | When the defect was created |
| **Resolved** | When the defect was resolved (if applicable) |

### 12.2 Filtering

Filter defects by resolution status: Open, In Progress, Resolved, or Closed.

### 12.3 Jira Integration

When Jira is configured, defects are automatically linked to Jira tickets. Click the Jira link to open the ticket in your Jira instance. Resolved Jira tickets feed back into TestLookup's AI training pipeline, improving future analysis accuracy.

---

## 13. Search

**Route:** `/search`
**Access:** All roles

TestLookup provides powerful search capabilities across all entities.

### 13.1 Search Modes

| Mode | Description | Speed | Use Case |
|---|---|---|---|
| **Global** | Searches across all entity types | Fast | General discovery |
| **Keyword** | Full-text match on names and error messages | Fast | Exact term lookup |
| **Semantic** | AI-powered meaning-based search using vector embeddings | Medium | "Find failures similar to X" |
| **Hybrid** | Combines keyword + semantic ranking | Medium | Best of both approaches |

### 13.2 Entity Type Filter

Narrow your search to specific entity types:
- All Types
- Test Cases
- Test Runs
- Test Suites
- Defects
- Flaky Tests
- Releases

### 13.3 Search Results

Results show:
- Entity type badge (color-coded).
- Entity name with highlighted matching terms.
- Relevance score.
- Contextual metadata (suite, run, date).

Click any result to navigate to its detail page.

### 13.4 Similar Failure Search

From any test case detail page, click **Find Similar** to search for historically similar failures using semantic similarity. This helps identify patterns across runs and projects.

---

## 14. Test Case Management

**Route:** `/test-management`
**Access:** QA_ENGINEER+ for write operations; all roles can view

The Test Management module provides comprehensive test artifact management.

### 14.1 Test Cases Tab

- **Create:** Click **New Test Case** to create a case with: name, description, priority, preconditions, steps, expected results, tags.
- **Edit:** Click any test case to modify it.
- **Delete:** Remove test cases (with confirmation).
- **AI Suggestions:** Click the AI icon to get AI-generated improvements for a test case (better descriptions, missing edge cases).
- **Quality Score:** Each test case has an automated quality score based on completeness.
- **Bulk Actions:** Select multiple cases for batch operations.

### 14.2 Test Suites Tab

Organize test cases into logical suites:
- Create suites with name and description.
- Link test cases to suites.
- View suite-level pass rate and coverage.

### 14.3 Test Plans Tab

Group suites into executable plans:
- **Status lifecycle:** Draft > Active > In Progress > Completed.
- Link test items to the plan.
- Track execution status.

### 14.4 Strategy Tab

Define testing strategies:
- Coverage goals.
- Risk-based prioritization.
- Automation vs. manual balance.

### 14.5 Reviews Tab

Request and view AI-assisted reviews of test cases:
- Automated quality assessment.
- Suggestions for improvements.
- Manual review tracking.

### 14.6 Audit Log Tab

Full change history for all test management artifacts:
- Who changed what, when, and what the old/new values were.
- Filterable by entity type and date range.

---

## 15. Live Execution Monitoring

**Route:** `/live`
**Access:** All roles

Monitor test executions in real-time as they happen.

### 15.1 Live Dashboard

The live page shows:
- **Active Runs Counter:** Number of currently executing test runs.
- **Tests In Progress:** Total tests currently running.
- **Live Pass Rate:** Real-time pass rate across active sessions.
- **WebSocket Status:** Connection state (Live, Connecting, Disconnected).

### 15.2 Active Sessions Table

| Column | Description |
|---|---|
| **Project** | Project name |
| **Session** | Build number or session identifier |
| **Test Count** | Total tests in the run |
| **Progress** | Visual progress bar with percentage |
| **Duration** | Elapsed time |

### 15.3 Event Feed

A real-time scrolling feed showing the last 200 events:
- Test completed (PASSED in green, FAILED in red, BROKEN in orange, SKIPPED in gray).
- Session started / completed.
- Error events.

### 15.4 How Live Streaming Works

1. Your CI pipeline uses the TestLookup Reporter SDK to stream events during execution.
2. Events flow through Redis Streams for high throughput.
3. The Live page connects via WebSocket for real-time updates.
4. When a run completes, the AI pipeline is automatically triggered.

Data refreshes every 5 seconds as a baseline, with more frequent updates when the WebSocket is connected.

### 15.5 Quick Start: Connect a Test Runner

The simplest way to stream live test execution is with a **project-scoped API key** and the TestNG or JUnit listener.

#### Prerequisites

1. An ADMIN creates a project-scoped API key (see Section 25.4).
2. Download the Java SDK fat JAR from the Live page's "All client SDKs" dropdown (or build with `make build-java-sdk`).
3. Add the JAR to your test classpath.

#### TestNG — Zero-Code Setup (Recommended)

Add suite parameters directly in your `testng.xml` — no Java code changes required:

```xml
<suite name="My Suite">
  <parameter name="testlookup.url" value="http://localhost:8000"/>
  <parameter name="testlookup.apiKey" value="qai_..."/>
  <parameter name="testlookup.projectId" value="your-project-uuid"/>
  <listeners>
    <listener class-name="io.testlookup.testng.TestLookupListener"/>
  </listeners>
  <test name="Regression Tests">
    <classes>
      <class name="com.example.MyTest"/>
    </classes>
  </test>
</suite>
```

Run your tests normally — the listener automatically:
- Creates a live session when the suite starts
- Records every test result (pass/fail/skip/broken) with duration, class name, error message, and stack trace
- Closes the session when the suite finishes, triggering the AI analysis pipeline

Optional suite parameters: `testlookup.build`, `testlookup.branch`, `testlookup.commit`.

#### Environment Variables (Works with TestNG and JUnit 5)

```bash
export TESTLOOKUP_URL=http://localhost:8000
export TESTLOOKUP_API_KEY=qai_...
export TESTLOOKUP_PROJECT_ID=your-project-uuid
export TESTLOOKUP_BUILD=build-42       # optional
export TESTLOOKUP_BRANCH=main          # optional
mvn test
```

The fat JAR auto-registers listeners for both JUnit 5 and TestNG via `META-INF/services` — no code changes needed.

#### Python — pytest Plugin

```bash
export TESTLOOKUP_URL=http://localhost:8000
export TESTLOOKUP_API_KEY=qai_...
export TESTLOOKUP_PROJECT_ID=your-project-uuid
pytest
```

Or add a `testlookup.yaml` to your project root:
```yaml
server:
  url: "http://localhost:8000"
auth:
  api_key: "qai_..."
project:
  id: "your-project-uuid"
```

#### Configuration Precedence

For all SDKs, configuration is resolved in this order (highest wins):
1. **TestNG suite parameters** (Java only)
2. **Constructor / Builder arguments** (programmatic)
3. **JVM system properties** (`-Dtestlookup.apiKey=...`)
4. **Environment variables** (`TESTLOOKUP_API_KEY=...`)
5. **testlookup.yaml config file**
6. **Built-in defaults**

---

## 16. AI Chat

**Route:** `/chat`
**Access:** TESTER+

The AI Chat lets you have conversational interactions about your test data.

### 16.1 Starting a Conversation

1. Click **New Session** in the left panel.
2. Type your question in the input box.
3. The AI responds using context from your recent test runs (last 5 days).

### 16.2 Example Questions

- "Why did test_login fail in the last run?"
- "What are the most common failure categories this week?"
- "Which tests should I prioritize for investigation?"
- "Show me the flakiest tests in the project."
- "What broke in the last release?"
- "Compare the last two runs."

### 16.3 Sessions

- Conversations are saved as sessions visible in the left sidebar.
- Switch between sessions to continue previous conversations.
- Delete sessions you no longer need.

### 16.4 Run Summaries

A sidebar panel shows recent failed runs with pre-computed summaries, providing quick context for your questions.

---

## 17. AI Pipeline Status

**Route:** `/agents` or `/agents/run/:runId`
**Access:** All roles

Monitor the AI analysis pipeline execution in real-time.

### 17.1 Pipeline Stages

The pipeline consists of up to 9 stages:

| Stage | Description | Icon |
|---|---|---|
| **1. Ingestion** | Validate and enrich test data | Database |
| **2. Anomaly Detection** | Compare against baselines, detect regressions and flaky patterns | AlertTriangle |
| **3. Root Cause Analysis** | Run AI agent per failure to classify root causes | Search |
| **4. Summary Generation** | Generate natural language summaries | FileText |
| **5. Defect Triage** | Auto-create defect candidates for Jira promotion | Bug |
| **6. Failure Clustering** | Group related failures semantically | Layers |
| **7. Flaky Sentinel** | Analyze flaky test lifecycles | Zap |
| **8. Test Health** | Detect automation anti-patterns | Heart |
| **9. Release Risk** | Compute risk score and release recommendation | Shield |

### 17.2 Stage Status Colors

| Color | Status | Meaning |
|---|---|---|
| Gray | Pending | Not yet started |
| Blue (pulsing) | Running | Currently executing |
| Green | Completed | Finished successfully |
| Red | Failed | Error during execution |
| Gray (dimmed) | Skipped | Not applicable for this run |

### 17.3 Stage Details

Click any stage card to expand it and see:
- Execution duration.
- Number of items processed.
- Detailed logs and output.
- Error messages (if failed).

---

## 18. Flaky Coach

**Route:** `/flaky-coach`
**Access:** All roles

The Flaky Coach helps you identify, prioritize, and remediate flaky tests.

### 18.1 Quarantine Recommendations

Each flaky test receives one of four recommendations:

| Recommendation | Meaning | Action |
|---|---|---|
| **QUARANTINE** | High flakiness, low reliability | Remove from CI gate; fix immediately |
| **INVESTIGATE** | Moderate flakiness, may be symptomatic | Deep investigation needed |
| **MONITOR** | Low flakiness, might self-resolve | Watch for trends; no immediate action |
| **HEALTHY** | Not flaky | No action needed |

### 18.2 Test Health Metrics

For each test, the Flaky Coach shows:
- **Status History:** Last 10 runs visualized as colored dots (green=pass, red=fail).
- **Failure Rate:** Percentage of runs where the test failed.
- **Impact Score:** How much the flaky test impacts overall quality confidence.
- **Total Runs** and **Failed Runs** counts.
- **Last Failure Date.**

### 18.3 Expanded Details

Click any test row to see:
- Context: why the Flaky Coach made its recommendation.
- Trend: is the flakiness increasing, decreasing, or stable.
- Impact Factor: downstream effects on release confidence.
- Suggested Actions: specific steps to fix or stabilize the test.

---

## 19. Intelligence Hub

**Route:** `/intelligence`
**Access:** All roles

The Intelligence Hub is a launch pad for AI-powered analysis.

### 19.1 What It Shows

- A list of recent test runs, with failed runs prioritized at the top.
- Each run shows build number, branch, project, and pass rate.
- A sparkles icon indicates runs that have AI intelligence available.

### 19.2 How to Use It

1. Browse or search for a run.
2. Click on a run to navigate to its Run Intelligence page.
3. If intelligence hasn't been generated yet, you'll see an option to trigger analysis.

---

## 20. Value Metrics

**Route:** `/value-metrics`
**Access:** All roles

The Value Metrics page quantifies the engineering value delivered by TestLookup.

### 20.1 Key Metrics

| Metric | Description |
|---|---|
| **Time Saved** | Hours saved through automated triage vs. manual investigation |
| **Cost Reduction** | Estimated cost savings from faster triage |
| **Release Velocity** | How TestLookup has improved release cadence |
| **Defect Prevention** | Number of duplicate defects prevented by clustering |

### 20.2 Time Range

Select a reporting period: 7, 30, 90, or 365 days.

### 20.3 Export

Click **Export CSV** to download the metrics data for reports and presentations.

### 20.4 Value Workflow

A visual workflow shows the value chain: Triage > Clustering > Release Protection > Value Realization.

---

## 21. Project Management

**Route:** `/projects`
**Access:** QA_LEAD+ to create/edit; all roles can view their projects

### 21.1 Creating a Project

1. Click **New Project**.
2. Fill in:
   - **Name:** Project display name.
   - **Slug:** Auto-generated URL-friendly identifier (editable).
   - **Description:** Optional project description.
   - **Jira Project Key:** Link to a Jira project (e.g., "PROJ") for defect promotion.
   - **OpenShift Namespace:** Link to an OCP namespace for infrastructure correlation.
3. Click **Create**.

### 21.2 Managing Projects

- **Edit:** Click a project to update its details.
- **Archive:** Soft-delete a project (hidden but not permanently removed).
- **Members:** Manage project membership via the Users page.

---

## 22. Release Management

**Route:** `/releases`
**Access:** QA_ENGINEER+ to create/edit; all roles can view

### 22.1 Creating a Release

1. Click **New Release**.
2. Fill in:
   - **Version:** Release version string (e.g., "v2.5.0").
   - **Target Date:** Planned release date.
   - **Description:** Release notes or context.
3. Click **Create**.

### 22.2 Release Phases

Each release progresses through phases:

| Phase | Description |
|---|---|
| **Planning** | Requirements gathered; release scope defined |
| **Development** | Code changes being made |
| **Code Freeze** | No new features; stabilization only |
| **QA Testing** | Active test execution |
| **UAT** | User acceptance testing |
| **Staging** | Pre-production validation |
| **Production** | Released to production |

Phase status: Pending > In Progress > Completed (or Skipped).

### 22.3 Linking Test Runs

Associate test runs with a release to track quality metrics per release:
- Pass rate per run.
- Aggregate release quality score.
- Release gate recommendation.

---

## 23. Release Gate Policies

**Route:** `/policies`
**Access:** QA_LEAD+ to manage; all roles can view

Release Gate Policies define the rules that determine GO / NO_GO / CONDITIONAL_GO recommendations.

### 23.1 Creating a Policy

1. Click **New Policy**.
2. Fill in name, description, and project scope (project-specific or system default).
3. Add rules:
   - **Flaky Recurrence Limit:** Maximum number of recurring flaky tests allowed.
   - **Open Defect Limit:** Maximum open defects before blocking release.
   - **Dimension Ceiling:** Maximum allowed score for any risk dimension (e.g., "User Impact must be below 80").
   - **Override Restrictions:** Rules about who can override and under what conditions.
4. Configure dimension weights (must sum to 1.0).
5. Click **Save as Draft** or **Publish**.

### 23.2 Policy Simulation

Enter a run ID in the simulation panel to see how the policy would evaluate that run. This helps you tune thresholds before publishing.

### 23.3 Policy Precedence

Policies are evaluated in this order:
1. **Project-specific policy** (if one exists for the project).
2. **System default policy** (project_id is NULL).
3. **Hardcoded thresholds** (if no policy exists at all): GO < 20, NO_GO >= 55, pass_rate_minimum = 90%.

---

## 24. Service Ownership

**Route:** `/ownership`
**Access:** QA_LEAD+ to manage; all roles can view

Service Ownership rules map test failures to responsible teams.

### 24.1 Creating Ownership Rules

1. Click **New Rule**.
2. Configure:
   - **Match Type:** suite_name, class_name, or test_name.
   - **Pattern:** Glob pattern (e.g., `com.example.payment.*`).
   - **Service:** The service or component name.
   - **Team:** The responsible team.
   - **Contact:** Team contact (email or Slack channel).
   - **Priority:** Numeric priority (higher number = evaluated first).
3. Click **Save**.

### 24.2 How Ownership Resolution Works

When a failure is detected:
1. Rules are evaluated highest-priority-first using glob matching.
2. If no rule matches, falls back to the project's `component_owner_map` (legacy).
3. If still no match, falls back to the test case's `owner` field (from Allure labels).
4. If nothing matches, the failure is assigned to "Unassigned."

For failure clusters, majority voting across member tests determines the owning team.

---

## 25. User Management

**Route:** `/users`
**Access:** QA_LEAD to view; ADMIN to manage

### 25.1 User Roles

TestLookup uses a hierarchical role system:

| Role | Capabilities |
|---|---|
| **VIEWER** | Read-only access to all dashboards and reports |
| **TESTER** | View + chat + live execution monitoring |
| **QA_ENGINEER** | Tester + trigger analysis + manage test cases + create API keys + manage defects |
| **QA_LEAD** | QA Engineer + manage projects, releases, policies, users + override release gates |
| **ADMIN** | Full access including system settings, integrations, SSO, and user management |

### 25.2 Users Tab

- View all users with their role, status (active/inactive), and email.
- **Add User:** Admin creates a user with a temporary password (shown once — share securely).
- **Invite User:** Send an invitation email with a registration link.
- **Change Role:** Promote or demote a user's role.
- **Activate/Deactivate:** Disable a user's access without deleting their account.

### 25.3 Project Access Tab

Manage per-project membership:
- Assign users to specific projects.
- Set project-level roles (a user can be QA_ENGINEER in one project and VIEWER in another).
- Remove users from projects.

### 25.4 API Keys Tab

API keys provide programmatic access to the TestLookup API:
- **Generate Key:** Creates a scoped personal access token. The raw key is shown only once — copy it immediately.
- **Key Hint:** The key is displayed as the first 8 characters + "..." for identification.
- **Revoke:** Delete an API key to immediately disable it.
- **Minimum Role:** QA_ENGINEER or higher can create API keys.

#### Project-Scoped API Keys (ADMIN)

ADMINs can create **project-scoped** API keys that restrict access to a single project. This is the recommended approach for CI/CD pipelines and live test execution:

- **Project-scoped key:** Can only submit test results and create live sessions for the bound project. Any request targeting a different project is rejected with 403.
- **User-scoped key (default):** Inherits the owning user's project permissions — can access any project the user is a member of.

**Creating a project-scoped key via API:**
```bash
curl -X POST http://localhost:8000/api/v1/keys \
  -H "Authorization: Bearer <admin-jwt>" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "CI Pipeline - My Project",
    "project_id": "<project-uuid>",
    "scopes": ["test:write"],
    "expires_days": 90
  }'
```

The response includes the raw key (shown once) — configure it as `TESTLOOKUP_API_KEY` in your CI environment or `testng.xml`.

---

## 26. Settings

**Route:** `/settings`
**Access:** ADMIN only (for most settings)

### 26.1 AI Configuration

**Route:** `/settings/ai`

Configure the AI analysis engine:

| Setting | Description | Options |
|---|---|---|
| **Analysis Mode** | Which engine classifies failures | Auto, LLM, ML, Rules |
| **LLM Provider** | AI model provider | Ollama, OpenAI, Gemini, LM Studio, vLLM |
| **LLM Model** | Specific model name | e.g., qwen2.5:7b, gpt-4o, gemini-pro |
| **Temperature** | Model creativity (lower = more deterministic) | 0.0 - 2.0 (default: 0.1) |
| **Max Tokens** | Maximum response length | 256 - 32768 (default: 4096) |
| **Embedding Provider** | Vector embedding model | Ollama, OpenAI |
| **Embedding Model** | Specific embedding model | e.g., nomic-embed-text |
| **Confidence Threshold** | Minimum confidence to accept AI results | 0-100% |
| **Timeout** | Maximum analysis time per request | 30-1800 seconds |
| **Offline Mode** | Air-gapped mode (Ollama only, no internet) | On/Off |
| **Deep Investigation** | Enable multi-agent pipeline | On/Off |
| **Fine-Tuning Pipeline** | Enable continuous model improvement | On/Off |
| **Cloud API Keys** | OpenAI / Google API keys (stored encrypted) | Masked input |

### 26.2 Storage and Data

**Route:** `/settings/storage`

View and configure data infrastructure:
- **PostgreSQL:** Connection status.
- **MongoDB:** Connection status.
- **Redis:** Connection status.
- **Object Storage:** MinIO / S3 / Local filesystem configuration.
- **ChromaDB:** Vector store host, port, and collection.

### 26.3 Integrations

**Route:** `/settings/integrations`

Configure third-party service connections:

| Integration | Configuration Fields | Purpose |
|---|---|---|
| **Jira** | Domain, email, default project key, API token | Defect promotion and tracking |
| **Splunk** | Base URL, API token | Log correlation for AI analysis |
| **OpenShift** | API URL, namespace, service account token | Infrastructure event correlation |
| **Slack** | Webhook URL, default channel | Notifications and digests |
| **Microsoft Teams** | Webhook URL | Notifications |
| **GitHub** | Repository (org/repo), personal access token | Commit correlation, CI integration |

Each integration has a **Test Connection** button to validate the configuration before saving.

### 26.4 Notifications

**Route:** `/settings/notifications`

Configure how TestLookup notifies you about events:

**SMTP Configuration:**
- Host, port, username, password.
- From address, TLS mode (TLS/STARTTLS).
- Enable/disable toggle and test email button.

**Per-Channel Configuration (Email, Slack, Teams):**
- Select which event types trigger notifications:
  - Run Failed
  - Run Passed
  - High Failure Rate (configurable threshold: 10-100%)
  - AI Analysis Complete
  - Quality Gate Failed
  - Flaky Test Detected
- Override notification recipients per channel.

**Notification History:**
- View recent notifications with delivery status (sent/failed).
- Mark individual or all notifications as read.

### 26.5 SSO and Identity Management

**Route:** `/settings/sso`

Configure enterprise single sign-on:

**SSO Configuration Tab:**
- Display name, IdP entity ID, IdP SSO URL, IdP certificate (PEM format).
- SP entity ID and ACS URL (provided by TestLookup).
- Group attribute mapping for automatic role assignment.
- Enforcement mode:
  - **OPTIONAL:** Users can choose SSO or password login.
  - **SSO_REQUIRED:** Only SSO login is allowed (admin password fallback available if enabled).
- Default role for new SSO users (VIEWER, TESTER, QA_ENGINEER, or QA_LEAD).
- Test Connection button to validate the SSO configuration.

**SCIM Tokens Tab:**
- Generate tokens for automated user provisioning from identity providers (Okta, Azure AD, etc.).
- View token hints and last-used timestamps.
- Revoke tokens.

**Identity Events Tab:**
- Audit trail of all identity-related events: logins, provisioning, role changes.
- Filter by event type, actor, and date range.

**Sync Status Tab:**
- Federated user count, last SSO login, last SCIM sync.
- Recent failures and events in the last 24 hours.

### 26.6 Digests and Saved Views

**Route:** `/settings/digests`

**Digest Subscriptions:**
- Schedule automated quality reports delivered by email, Slack, or Teams.
- Schedule types: DAILY, WEEKLY, PER_RUN, PER_RELEASE, PER_SUITE.
- Trigger filters: all runs, failed only, or degraded only.
- Pause, resume, or delete subscriptions.
- **Preview Digest:** Generate a preview of what the digest looks like before saving.

**Saved Views:**
- Save named filter presets for quick access to specific data views.
- Share views with the team or keep them personal.
- Set a default view that loads automatically.

### 26.7 Integration Health

**Route:** `/settings/integration-health`

Monitor the health of all configured integrations:

**Current Status Tab:**
- Real-time health status for each provider: Healthy (green), Degraded (amber), Down (red), Auth Error, Timeout.
- Response latency.
- Consecutive failure count.

**Trends Tab (7-day):**
- Uptime percentage.
- Health / degraded / down counts.
- Average latency.

**Probe History Tab:**
- Per-provider history with status, latency, and auth validation results.

**Probe All Now:** Click to manually trigger health probes for all integrations.

Health probes run automatically every 15 minutes via a background job.

### 26.8 Audit Dashboard

**Route:** `/settings/audit`

Unified audit trail across the entire system:

**Audit Events Tab:**
- Filter by category: Access, Settings, Test Management, Identity, Report, Notification, Release.
- Filter by date range: 7, 30, or 90 days.
- Each event shows: timestamp, actor, action, category, and details.
- Sensitive values (passwords, tokens, secrets) are automatically redacted.

**Project Observability Tab:**
- Per-project metrics: total runs, pass rate, failed runs, AI analyses, release decisions, total tests, audit events.

**Export CSV:** Download the audit trail for compliance or analysis.

### 26.9 AI Evaluation

**Route:** `/settings/ai-eval`

Monitor and improve AI analysis quality:

**Dashboard Tab:**
- Human-AI agreement rate (how often users agree with AI classifications).
- Quality drift detection (is accuracy changing over time).
- Recent evaluation runs with precision, recall, F1, and accuracy.
- Model version history.

**Datasets Tab:**
- Create evaluation datasets from user feedback (correct/incorrect ratings).
- Seed golden datasets for consistent benchmarking.
- Run evaluations against datasets.

**Evaluation Gate Tab:**
- Pre-release gate runner for AI model changes.
- Task types: classification, root cause analysis, duplicate detection, release decision.
- Accuracy and regression thresholds.

### 26.10 Performance

**Route:** `/settings/performance`

View and tune performance budgets:

**Latency Budgets:**
- Target p50, p95, and p99 latencies per operation.
- Color-coded status (within budget / exceeding budget).

**Search Configuration:**
- Index batch size, incremental limit, query timeout, max results.
- Pool sizes and worker concurrency.

**Scale Scenarios:**
- Pre-defined scenarios: Small Team, Mid-Enterprise, Large Enterprise.
- Each shows expected projects, runs/day, tests/run, and concurrent users.

---

## 27. Client SDK and Test Reporters

### 27.1 Python Reporter SDK

TestLookup provides a Python client SDK (`testlookup_reporter.py`) for streaming test results from your CI pipeline.

**Installation:**
```bash
pip install testlookup-reporter
```

**Usage:**
```python
from testlookup_reporter import TestLookupReporter

reporter = TestLookupReporter(
    base_url="https://testlookup.company.com",
    token="<your-api-key>",
    project_id="<project-uuid>"
)

async with reporter.session(build_number="build-42") as session:
    await session.record("test_login", "PASSED", duration_ms=120)
    await session.record("test_checkout", "FAILED", duration_ms=5000,
                         error_message="AssertionError: expected 200 got 500")
```

**Features:**
- Async batching: flushes every 50 events or 100ms (whichever comes first).
- Retry with exponential backoff (up to 5 retries).
- Supports 10,000+ concurrent executions.
- Thread-safe with `asyncio.Queue`.

### 27.2 Pytest Plugin

The SDK includes a pytest plugin for automatic test result streaming:

```bash
pytest \
  --testlookup-url=https://testlookup.company.com \
  --testlookup-token=<your-api-key> \
  --testlookup-project=<project-uuid> \
  --testlookup-build=build-42
```

### 27.3 Java Reporter SDK

TestLookup provides a Java client SDK with auto-discovery listeners for JUnit 5 and TestNG.

**Installation:**
```bash
# Download the fat JAR from the Live page or build locally:
make build-java-sdk
# Output: client/java/target/testlookup-reporter-1.0.0-all.jar

# Add to Maven (after local install):
mvn install:install-file -Dfile=testlookup-reporter-1.0.0-all.jar \
  -DgroupId=io.testlookup -DartifactId=testlookup-reporter \
  -Dversion=1.0.0 -Dclassifier=all -Dpackaging=jar
```

**TestNG — Zero-Code (Recommended):**

Add the listener and configuration directly in `testng.xml`:

```xml
<suite name="My Suite">
  <parameter name="testlookup.url" value="http://localhost:8000"/>
  <parameter name="testlookup.apiKey" value="qai_..."/>
  <parameter name="testlookup.projectId" value="your-project-uuid"/>
  <listeners>
    <listener class-name="io.testlookup.testng.TestLookupListener"/>
  </listeners>
  <test name="API Tests">
    <classes>
      <class name="com.example.ApiTest"/>
    </classes>
  </test>
</suite>
```

No Java code changes required. The listener automatically creates a live session, records all test results in real-time, and closes the session when the suite finishes.

**JUnit 5 — Auto-Discovery:**

The fat JAR includes a `META-INF/services` descriptor that auto-registers the `TestLookupExtension`. Just add the JAR to the test classpath and configure via environment variables:

```bash
export TESTLOOKUP_URL=http://localhost:8000
export TESTLOOKUP_API_KEY=qai_...
export TESTLOOKUP_PROJECT_ID=your-project-uuid
mvn test
```

**Programmatic Usage (API Key):**
```java
import io.testlookup.TestLookupReporter;
import io.testlookup.TestLookupReporter.*;

TestLookupReporter reporter = new TestLookupReporter.Builder()
    .baseUrl("http://localhost:8000")
    .apiKey("qai_...")                     // project-scoped API key
    .projectId("your-project-uuid")
    .build();

try (LiveSession session = reporter.startSession(
        SessionOptions.builder()
            .buildNumber("build-42")
            .branch("main")
            .build())) {

    session.record("test_login", TestStatus.PASSED, 120);
    session.record("test_cart",  TestStatus.FAILED, 340,
        RecordOptions.builder()
            .error("AssertionError: expected 200")
            .suiteName("CheckoutTests")
            .build());
}
// session.close() called automatically — triggers AI analysis
```

**Programmatic Usage (JWT Token):**
```java
TestLookupReporter reporter = new TestLookupReporter.Builder()
    .baseUrl("http://localhost:8000")
    .token("<jwt-access-token>")
    .projectId("your-project-uuid")
    .build();
```

**Features:**
- Auto-discovery for JUnit 5 and TestNG (zero-code via ServiceLoader).
- TestNG suite parameter configuration (no env vars needed).
- API key authentication (project-scoped keys for CI/CD).
- Async batching: flushes every 50 events or 100ms.
- Retry with exponential backoff (up to 5 retries).
- Thread-safe with `LinkedBlockingQueue`.
- Fat JAR with relocated Jackson (no classpath conflicts).

### 27.4 Go Client

A Go client is available at `client/go/testlookup/client.go` for Go-based test frameworks.

### 27.5 Webhook Ingestion

For frameworks that produce result files (JUnit XML, Allure JSON), upload directly:

```bash
curl -X POST https://testlookup.company.com/api/v1/ingest/file \
  -H "X-API-Key: qai_..." \
  -F "file=@results/junit.xml" \
  -F "project_id=<project-uuid>" \
  -F "build_number=build-42"
```

### 27.6 REST API

The full REST API is documented at `https://your-instance:8000/docs` (Swagger UI). Key endpoints:

| Method | Endpoint | Description |
|---|---|---|
| POST | /api/v1/auth/login | Authenticate and get JWT |
| GET | /api/v1/runs | List test runs |
| GET | /api/v1/runs/:id | Get run details |
| GET | /api/v1/runs/:id/intelligence | Get AI analysis |
| POST | /api/v1/analyze | Analyze a single test case |
| POST | /api/v1/deep-investigate/:runId | Trigger deep investigation |
| GET | /api/v1/release-readiness/:runId | Get release gate decision |
| GET | /api/v1/search?q=term | Search across entities |
| GET | /api/v1/analytics/flaky-tests | Get flaky test leaderboard |

---

## 28. MCP Server (AI Assistant Integration)

TestLookup includes a Model Context Protocol (MCP) server that lets AI assistants (Claude Desktop, IDE plugins, CI bots) interact with your test data.

### 28.1 Starting the MCP Server

**Stdio mode (for Claude Desktop):**
```bash
make mcp-start
```

**SSE mode (for web clients and CI):**
```bash
make mcp-sse    # Starts on http://localhost:8002/sse
```

### 28.2 Available Tools (20)

| Group | Tools |
|---|---|
| **Auth** | login, health_check |
| **Projects** | list_projects, get_project, create_project |
| **Runs** | list_test_runs, get_run_details, list_test_cases, get_test_case |
| **Metrics** | get_dashboard_metrics, get_test_trends |
| **Analytics** | get_flaky_tests, get_failure_categories, get_top_failing_tests, get_coverage_report, get_defects, get_ai_analysis_summary |
| **Analysis** | trigger_ai_analysis, search_tests |
| **Release** | check_release_readiness |

### 28.3 Prompt Workflows (6)

Pre-built conversation templates:

| Prompt | Purpose |
|---|---|
| **investigate_failure** | Full root-cause investigation for a failing test |
| **release_readiness_report** | Executive go/no-go report |
| **weekly_quality_digest** | Weekly quality summary for team sharing |
| **flakiness_investigation** | Deep-dive on flaky tests with remediation plan |
| **defect_triage_session** | Structured defect triage with prioritization |
| **suite_health_check** | Health report for a specific test suite |

### 28.4 Configuration for Claude Desktop

Add to your Claude Desktop MCP settings:
```json
{
  "mcpServers": {
    "testlookup": {
      "command": "python",
      "args": ["mcp/server.py", "--transport", "stdio"],
      "env": {
        "TESTLOOKUP_URL": "http://localhost:8000",
        "TESTLOOKUP_USERNAME": "admin@testlookup.dev",
        "TESTLOOKUP_PASSWORD": "Admin@123"
      }
    }
  }
}
```

---

## 29. Keyboard Shortcuts

| Shortcut | Action |
|---|---|
| Ctrl+F (Cmd+F on Mac) | Focus global search |
| Ctrl+, (Cmd+, on Mac) | Open Settings |
| Ctrl+B (Cmd+B on Mac) | Toggle Sidebar |
| Ctrl+N (Cmd+N on Mac) | New Project |
| Ctrl+O (Cmd+O on Mac) | Open Run File |
| Ctrl+I (Cmd+I on Mac) | Import Results |
| Ctrl+Shift+A (Cmd+Shift+A) | Trigger Analysis |
| Ctrl+Shift+C (Cmd+Shift+C) | Open Chat |
| F11 (Ctrl+Cmd+F on Mac) | Toggle Full Screen |

---

## 30. Troubleshooting

### 30.1 Common Issues

**"Analysis is taking too long"**
- Check if your LLM provider (Ollama) is running.
- Reduce the AI timeout in Settings > AI Configuration.
- Switch to Rules or ML mode for faster results.
- The analysis router automatically falls back: LLM > ML > Rules.

**"No intelligence available for this run"**
- Intelligence must be triggered manually or automatically after ingestion.
- Check that the AI pipeline completed on the Agent Status page.
- Verify LLM connectivity in Settings > Integration Health.

**"Semantic search returns no results"**
- ChromaDB must be running for semantic search.
- Run a reindex from Settings > AI Configuration or use the search reindex endpoint.
- Fall back to keyword search if ChromaDB is unavailable.

**"ML mode shows 'Not Trained' badge"**
- ML mode requires at least 200 labeled samples before training activates.
- Label more analyses as correct/incorrect via the feedback buttons.
- Training runs nightly via Celery Beat. You can trigger it manually.

**"Cannot create Jira ticket"**
- Verify Jira integration in Settings > Integrations.
- Use the **Test Connection** button to validate credentials.
- Check Integration Health for recent failures.
- Ensure the Jira project key in your TestLookup project matches a real Jira project.

**"Notifications not arriving"**
- Check SMTP configuration in Settings > Notifications.
- Send a test notification to verify connectivity.
- Check notification preferences are enabled for the desired event types.
- Check Notification History for delivery failures.

**"Login fails after SSO enforcement"**
- When SSO_REQUIRED mode is active, only ADMIN users can use password login (if admin fallback is enabled).
- Non-admin users must use SSO.
- Contact your admin to check SSO configuration.

### 30.2 Health Checks

Check system health at these endpoints:
- `/health/live` — Basic liveness (is the server running?).
- `/health/ready` — Readiness (are all dependencies connected?).
- `/health/details` — Detailed health with per-dependency status.

### 30.3 Log Access

- Backend logs: `docker compose logs backend` or `make dev-logs`.
- Worker logs: `docker compose logs worker`.
- Seed data logs: `make dev-logs-seed`.
- Frontend: check the browser developer console (F12).

---

## 31. Glossary

| Term | Definition |
|---|---|
| **Run** | A single execution of a test suite, producing pass/fail results for individual test cases. |
| **Test Case** | A single test within a run, with a name, status, duration, and optional error details. |
| **Run Intelligence** | AI-generated analysis of a test run, including summaries, failure clusters, and release recommendations. |
| **Deep Investigation** | A multi-agent AI pipeline that performs clustering, log analysis, contract validation, and release risk assessment. |
| **Release Gate** | A decision point that determines whether a release is safe to proceed based on test results and AI analysis. |
| **Failure Cluster** | A group of related test failures identified by the AI as having a common root cause. |
| **Defect** | An issue promoted from a failure cluster, optionally linked to a Jira ticket. |
| **Flaky Test** | A test that intermittently passes and fails without code changes. |
| **Analysis Mode** | The engine used for test classification: LLM (AI model), ML (trained classifier), Rules (pattern matching), or Auto (best available). |
| **Criticality Matrix** | A visualization mapping failure categories against confidence levels. |
| **Risk Score** | A numeric score (0-100) representing the risk of releasing based on current test results. |
| **GO / NO_GO / CONDITIONAL_GO** | Release gate decisions. GO = safe, NO_GO = unsafe, CONDITIONAL_GO = review before proceeding. |
| **Baseline** | The reference run (usually the last stable release) against which regressions are detected. |
| **Pipeline** | The multi-stage AI analysis process (ingestion > analysis > clustering > risk assessment). |
| **Sidecar** | A background service process that handles API requests and AI processing. |
| **SWR** | Stale-While-Revalidate — the data fetching strategy used for automatic background data refresh. |
| **MCP** | Model Context Protocol — a standard for AI assistants to interact with external tools and data. |
| **Ollama** | An open-source local LLM runtime that enables AI analysis without cloud APIs. |
| **ChromaDB** | A vector database used for semantic search (finding similar failures by meaning). |
| **Celery** | A distributed task queue used for background AI analysis and scheduled jobs. |

---

**Document Owner:** Product Team
**Last Updated:** 2026-04-07
