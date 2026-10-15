# Runs and run detail

A **run** is one execution of a suite: a CI job, a local `pytest`, a TestNG session. Every other page is built from runs, so this is where most investigations start. This page covers the run list, one run's page, a single test result, comparing two runs, and bisecting a regression.

## The run list

**Runs** in the sidebar opens `/runs`, titled **Test Runs**. Its tabs are **History** (this page), **Live** (runs streaming right now, see [Watching runs live](/docs/live)) and **Compare**.

**Filters.** The window picker (24h to 90d), a status filter (**All statuses**, **Failed**, **Passed**, **In progress**) and a suite filter.

**The status banner** sums up the window: **Pipeline broken**, **Mixed**, **Pipeline healthy** or **Pending**, with the **Last green** run, the **Red streak** (how many runs in a row have failed) and the **Avg pass rate**. When the window holds both a green run and a failing one, the banner offers **Bisect from last green** (see [Bisect from last green](#bisect-from-last-green)).

**The table** shows 25 runs per page, newest first:

| Column | Shows |
|---|---|
| **Build** | `Run #<n>`, linking to the run's page. Runs that came from a file upload carry an **Uploaded** badge |
| **Signature** | The run's failure signature |
| **Status** | Passed, failed or in progress |
| **AI verdict** | **Go**, **Conditional** or **No-Go** from the run's analysis, or **—** when there is none yet (hover for why). Click it to open the run's **Analysis** tab |
| **Tests** / **Pass rate** | The run's counts |
| **Timing** | When it ran and how long it took: the **wall clock** ("9.0s wall") when the run reported one, otherwise the **summed** test time ("17.4s summed"); hover for both. Click the header to sort |
| **Actions** | Icon buttons: **Re-trigger standard pipeline**, **Trigger deep investigation**, and **Compare to the previous run** of the same suite |

The first two actions, and the bulk controls (tick rows, then **Bulk trigger** or **Bulk deep**), need the `QA_ENGINEER` role or above. The **⋯** menu offers **Trigger all failed (N)** and **Deep all failed (N)** for every failed run in the window.

**Failure signatures** (next to the page title, when any failure cluster exists) opens a side panel listing the failure signatures in the window.

Two disclosures at the bottom explain the banner:

- **How this verdict is computed** — the pipeline-health gauge and its four parts: build success (40%), signature diversity (15%), fix velocity (25%) and metadata coverage (20%).
- **Build history** — the last 14 builds, build velocity, and a **Last green build** callout with **Start bisect**.

**Upload report** is the primary button when uploading is enabled; see [Getting results in](/docs/ingestion#upload-a-report-from-the-app).

> **Note.** Counts on this page are **executions**: a suite run five times counts five times. Coverage and the summary report count unique tests across the window, so their totals are smaller. Both are right; they answer different questions. See [Dashboards and metrics](/docs/dashboards#reading-a-metric-honestly).

## One run's page

Click a build to open `/runs/<run id>`, titled **Run #<build number>**. Under the title: the run's status, suite and release, and its timing: **Wall clock** (or **not reported** when the report carried no timestamps), **Test time (summed)** and, when both are known, **Parallelism**. See [Concepts](/docs/concepts#definitions) for the three terms. **Set release** (or the pencil next to an existing release, **Change release**) attaches the run to a release by name, creating the release if it does not exist yet; this needs the `QA_LEAD` role.

The **⋯** menu:

| Action | Does |
|---|---|
| **Trigger pipeline** | Queues the standard analysis pipeline again; its report appears under **Evidence**. `QA_ENGINEER` or above |
| **Deep investigation** | Queues a deep investigation and opens the **Analysis** tab. `QA_ENGINEER` or above |
| **Refresh AI analysis** | Recomputes the Analysis tab's report from the stored results, instead of showing the cached copy. It does not re-run the pipeline |
| **Export PDF** | The run report as a PDF |
| **Download evidence bundle** | The run's evidence as a file |
| **Compare to previous run** | Opens Compare against the previous run of the same suite |
| **Release gate** | The release-gate view for this run (`/release-gate/<run id>`) |
| **Ask AI about this run** | Opens [Ask AI](/docs/ask-ai) with the question "What failed in this build, and why?" written for you. Shown only when Ask AI is available |

The page has five tabs. The tab is in the address (`?tab=`), so a link can open a specific one.

### Tests

Every test result in the run. Filter chips (**All**, **failed**, **broken**, **passed**, **skipped**, and **unrecognised** when the report had statuses TestLookup could not map) and a suite filter narrow the list. Columns: test name, suite, status, duration and failure category.

Click a row to open it in a side panel, with **Previous failure** / **Next failure** to step through the failures on the page and **Open full page** for the test result page. Ctrl- or ⌘-click opens the full page directly.

While a run is still streaming, the list is read from the live buffer, so it matches what **Live** shows. If a live run shows no tests, **Recover from buffer** asks the server to rebuild them.

### Analysis

The run's AI analysis report. Old links to `/runs/<id>/intelligence` and `/deep-investigate/<id>` open here.

- **The verdict banner**: **Go · ship cleared**, **Conditional Go · proceed with mitigation**, **No-Go · ship blocked** or **Pending · awaiting evidence**, with the risk score, the number of blocking issues and the AI confidence. **Decision trail** shows how the verdict was reached, step by step.
- **The review banner**, when the report is waiting for or has had a human review. See [Your inbox and approvals](/docs/inbox#approvals).
- **What failed**: each failure, with **Open test case**, **Decision trail** and **File defect** (`QA_ENGINEER` or above).
- **Proposed clusters**: the run's failures grouped by likely shared cause. **Analyze failures** starts a deep investigation of this run; it is disabled when the run has no failures, when one is already running, or for roles below `QA_ENGINEER`. See [Root cause (AI)](/docs/failure-analysis#root-cause-ai-deep-investigation).
- Two collapsed sections: **How this score is computed**, and **Evidence behind the verdict** (the pipeline stages that ran, the test outcome, recommended actions for your role, the AI confidence, failure categories, step flips and provenance).

The verdict is computed, not written by a model; see [Releases and gates](/docs/releases#how-it-is-produced). The stages behind it are described in [AI agents and the pipeline](/docs/ai-agents).

### Changes

**What changed since the last good run**: the pass-rate change, **New failures** and **Resolved** tests against the most recent passing run (the baseline), a list of the new failures, and the commits since the baseline when the run carried a commit range. Buttons open Compare against the last good build, against the previous run, or empty (**Open Compare**). If no passing run exists to compare with, the tab says so.

### Evidence

The detail behind the analysis. Old links to `/agents/run/<id>` open here.

- **Decision intelligence** — the agentic decision report, with a **Report version** picker when the run has been analysed more than once. Absent when no decision report exists for the run.
- **The pipeline's AI report** — **View AI report** to read it, and **Agent stages** to see each stage the pipeline ran. When the run has several pipeline runs, a **Pipeline** picker chooses between them.

"No agent pipeline has analysed this run yet." means no pipeline has run; **Trigger pipeline** in the ⋯ menu starts one.

### Timing

How long the run took and where the time went (`?tab=timing`).

- **Execution time** — **Wall clock**, **Test time (summed)**, **Parallelism** (summed ÷ wall clock) and the setup / teardown share, with where the figures come from (the report's own run span, or its tests' start and finish times).
- **Setup, test and teardown** — the time the run's tests spent in each phase, when the report recorded them.
- **Timeline** — one row per worker, one bar per test, coloured by status, so you can see what ran in parallel and where a worker sat idle. Click a bar to open that test. A run with more than 2,000 timed tests shows the 2,000 longest and says so.
- **Workers**, **Suites** — per worker and per suite: tests, wall clock and summed time.
- **Slowest tests** — the 20 slowest tests, each with its duration baseline when it has one.
- **Duration regressions** — tests and suites slower than their usual time, the same findings as the **Changes** tab.

When a report format does not record start times (a JUnit file without timestamps, for example), the tab says so and shows durations only: no timeline, no empty chart, and the wall clock is **not reported** rather than the summed time. Project-wide execution time is on [Trends › Execution time](/docs/charts#execution-time).

## One test result

Open a test from the Tests tab (**Open full page**) to reach `/runs/<run id>/tests/<test id>`. It shows the suite, class, status, duration and tags, then:

- **Error and stack trace** as recorded.
- **AI root cause**, for failed and broken tests only. **Analyse Root Cause** runs the analysis on demand; the result shows the suggested root cause, actions for your role and all recommended actions.
  - **Confirm** records that the analysis was right; **Correct** opens **Correct classification**, where you pick the right category (Flaky, Product Bug, Infrastructure, Test Data, Automation Defect) and **Record correction**. Corrections are kept alongside the original and are applied the next time the same test is analysed. See [Investigating failures](/docs/failure-analysis#correcting-a-classification).
  - When the result needs human review, a review control (**Pending Human Review**, **Reviewed**, **Defect filed**, **False positive**, **Reproducible**) records what a person concluded.
  - **Create Jira Defect** files a ticket when the project has a Jira project key configured and the result does not need review first. Once filed, it becomes **View Jira Ticket**.
- Three tabs: **History** (this test's recent runs, oldest to newest, its flakiness, and **Cross-run step flakiness**: which step flips between pass and fail across runs, for step-based tests), **Steps**, and **Details** (result id, run, time, severity, feature, owner, failure category).

## Comparing two runs

**Runs → Compare** (`/runs/compare`) diffs two runs. Two ways to pick them:

- **Latest suite comparison** — choose a suite and **Compare latest** to diff its two most recent runs. Needs one project selected.
- **Manual historical comparison** — choose **Left run (baseline)** and **Right run (target)**, optionally limited to one suite, then **Compare**. The pickers list the 100 newest runs.

The result leads with what changed: **Pass rate**, **Failed tests**, **New failures** and **Duration** for each side, an **AI Comparison Report** (key differences, new risks, actions), and **Status changes** — how many tests moved from one status to another. Click a flow to filter the table to those tests. Filter chips narrow the table to **New failures**, **Regressed**, **Still failing**, **Duration spikes**, **Fixed**, **New tests**, **Removed** or **Renamed**.

The comparison is in the address (`/runs/compare?mode=manual&left=<run id>&right=<run id>&suite=<suite>`), so you can share it.

## Bisect from last green

When a suite goes red, the useful question is what changed since it was last green. **Bisect from last green** (in the Runs status banner, or **Start bisect** under **Build history**) opens Compare with the suite's last green run on the left and the latest failed run on the right, filtered to the failing run's suite. The **New failures** list is then exactly the set of tests that broke between those two builds; the commits between them are the suspects.

Bisect needs a green run and a failing run in the current window. If there is no green run, the page says "No green run found in this window — widen the window to enable bisect."; widen the window.

## A red-build routine

1. **Runs** → the failed run → **Analysis**. Is it one cluster or many? One cluster usually means one cause.
2. **Bisect from last green** and read the **New failures**.
3. Genuinely new failures: find the change between the two builds, then file a defect from the failure (see [Investigating failures](/docs/failure-analysis#promoting-to-a-defect)).
4. Failures that alternate with green runs: treat them as [flaky](/docs/flaky) instead.
5. A large, messy run: **Analyze failures** on the Analysis tab, or **Deep all failed**, and let the investigation group them.

## Related

- [Getting results in](/docs/ingestion)
- [Watching runs live](/docs/live)
- [Investigating failures](/docs/failure-analysis)
- [Dashboards and metrics](/docs/dashboards)
