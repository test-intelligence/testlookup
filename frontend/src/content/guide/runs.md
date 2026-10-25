# Runs and run detail

A **run** is one execution of a suite: a CI job, a local `pytest`, a TestNG session. Every other page is built from runs, so this is where most investigations start. This page covers the run list, one run's page, a single test result, comparing two runs, and bisecting a regression.

## The run list

**Runs** in the sidebar opens `/runs`, titled **Test Runs**. Its tabs are **History** (this page), **Live** (runs streaming right now, see [Watching runs live](/docs/live)) and **Compare**.

**Filters.** The window picker (24h to 90d), a status filter (**All statuses**, **Failed**, **Passed**, **In progress**), a suite filter, a **search** box (a build number, branch or CI job, or the start of a commit hash), a **Branch** select (with one project selected) and an **AI verdict** select (**Any verdict**, **Go**, **Conditional**, **No-Go**, **No verdict**). The search, branch and verdict are kept in the address (`/runs?q=239&branch=main&verdict=NO_GO`), so a reload or a shared link restores them, and old `/intelligence?branch=main` links open the filtered list. When nothing matches, the table says **No runs match these filters.** with **Clear filters**.

**The status banner** sums up the window: **Pipeline broken**, **Mixed**, **Pipeline healthy** or **Pending**, with the **Last green** run, the **Red streak** (how many runs in a row have failed) and the **Avg pass rate**. When the window holds both a green run and a failing one, the banner offers **Bisect from last green** (see [Bisect from last green](#bisect-from-last-green)).

**The table** shows 25 runs per page, newest first:

| Column | Shows |
|---|---|
| **Build** | `Run #<n>`, linking to the run's page. Runs that came from a file upload carry an **Uploaded** badge |
| **Signature** | For a failed build, a short chip naming the error most of its failing tests share (hover for the error's first line): builds with the same chip failed the same way. **—** for a passed or in-progress run, and for a failed build older than the newest 50 failed builds of the window |
| **Status** | Passed, failed or in progress |
| **AI verdict** | **Go**, **Conditional** or **No-Go** from the run's analysis (the page reads them all in one request), or **—** when there is none (hover for why: the run has not been analysed, or it was analysed but its summary is not built yet, which opening the run's analysis does). Click it to open the run's **Analysis** tab |
| **Tests** / **Pass rate** | The run's counts |
| **Timing** | When it ran and how long it took: the **wall clock** ("9.0s wall") when the run reported one, otherwise the **summed** test time ("17.4s summed"); hover for both. Click the header to sort |
| **Actions** | Icon buttons: **Re-trigger standard pipeline**, **Trigger deep investigation**, and **Compare to the previous run** of the same suite |

The first two actions, and the bulk controls (tick rows, then **Bulk trigger** or **Bulk deep**), need the `QA_ENGINEER` role or above. The **⋯** menu offers **Trigger all failed (N)** and **Deep all failed (N)** for every failed run in the window, and **Re-trigger all matching (N)** for every run the current filters match. Each bulk action is one request, tracked as a [bulk job](#bulk-jobs) with its own progress panel.

**Failure signatures** (next to the page title, when the window has a failed build, or a row's signature chip) opens a section above the runs table that groups the window's failed builds by the error most of their failing tests share. Each group is titled by that error's first line (**No message recorded** when the failures carry none), lists its builds, and says when one test fails with that error in all of them. Two builds with the same pass and fail counts but different errors are different groups. With more than 50 failed builds in the window, the newest 50 are grouped and the section says so. Its **Close panel** button closes it.

Two disclosures at the bottom explain the banner:

- **How this verdict is computed** — the pipeline-health gauge and its four parts: build success (40%), signature diversity (15%: how many different errors the failed builds share; **—** while the signatures load, and the gauge then uses the other three), fix velocity (25%) and metadata coverage (20%).
- **Build history** — the last 14 builds, build velocity, and a **Last green build** callout with **Start bisect**.

**Upload report** is the primary button when uploading is enabled; see [Getting results in](/docs/ingestion#upload-a-report-from-the-app).

> **Note.** Counts on this page are **executions**: a suite run five times counts five times. Coverage and the summary report count unique tests across the window, so their totals are smaller. Both are right; they answer different questions. See [Dashboards and metrics](/docs/dashboards#reading-a-metric-honestly).

## Bulk jobs

Every bulk action on **Runs** — **Bulk trigger**, **Bulk deep**, **Trigger all failed**, **Deep all failed** and **Re-trigger all matching** — is one request: the server queues the pipelines as one **bulk job**, and a panel opens above the table. The job stays in the address (`?bulk_job=<id>`), so a reload keeps the panel.

- The first line is what was asked: `N requested · M queued · K already running · J not found`. A run whose pipeline is already queued or running is not queued a second time; it is counted as already running. For **Re-trigger all matching** the line says how many runs matched; at most the newest 2,000 are queued.
- The second line is the progress, counted from the pipelines themselves each time the panel refreshes (every few seconds while the job is active): completed, passed, failed, running and not started, with a bar of how many have started.
- **Cancel** (for the person who started the job, while it is active) asks you to confirm, then stops every pipeline of the job that has not started yet. Pipelines already running carry on; each keeps its own **Stop** on **Pipeline runs** (**Open Pipeline runs**).
- **Close** hides the panel.

When the job finishes, the runs list refreshes once. A queued pipeline that never started (after a cancel or a worker restart, for example) is counted as **not started**, never as failed. The job's request record is kept for 24 hours; after that the panel says `Counted from the pipelines: the job's request record has expired.` and shows only what the pipelines record. If the job cannot be tracked at all, the panel says so and the pipelines still run.

**Re-trigger all matching (N)** re-runs the standard pipeline for every run the current filters match — the project, the global release, the window, status, suite, search, branch and AI verdict — exactly the runs the table lists, newest first. N is the list's total, and a confirmation names the count before anything is queued.

**Bulk deep** and **Deep all failed** are refused as a whole, with the reason shown in the panel's place, while deep investigation is off under **Settings › AI** or the analysis mode is **Rules**: nothing is queued.

Only the person who started a job, or an administrator, can open or cancel it. Starting and cancelling a job are recorded in the project's activity.

## One run's page

Click a build to open `/runs/<run id>`, titled **Run #<build number>**. Under the title: the run's status, suite and release, and its timing: **Wall clock** (or **not reported** when the report carried no timestamps), **Test time (summed)** and, when both are known, **Parallelism**. See [Concepts](/docs/concepts#definitions) for the three terms. A run that no release claims reads **Unattributed** where the release would be: the [attribution ladder](/docs/releases#getting-runs-into-a-release) did not link it (hover for why). Click **Unattributed** to give the run a release by name, creating the release if it does not exist yet; the pencil next to an existing release (**Change release**) moves the run to another. Both need the `QA_LEAD` role.

The **⋯** menu:

| Action | Does |
|---|---|
| **Trigger pipeline** | Queues the standard analysis pipeline again; its report appears under **Evidence**. `QA_ENGINEER` or above |
| **Deep investigation** | Queues a deep investigation and opens the **Analysis** tab. `QA_ENGINEER` or above |
| **Refresh AI analysis** | Recomputes the Analysis tab's report from the stored results, instead of showing the cached copy. It does not re-run the pipeline |
| **Export PDF** | The run report as a PDF |
| **Download evidence bundle** | The run's evidence as a file |
| **Export AI history** | Every stored AI analysis of the run, the differences between consecutive versions and each test's versions, as `run-<build>-ai-history.json` (see [AI history](#ai-history)) |
| **Compare to previous run** | Opens Compare against the previous run of the same suite **on the same branch**: the newest run that finished before this one, whatever its outcome. The server picks it, so the Runs list, this menu and the Analysis tab's **vs previous run** always open the same pair. A run without suite attribution has no previous run |
| **Release gate** | The release-gate view for this run (`/release-gate/<run id>`) |
| **Ask AI about this run** | Opens [Ask AI](/docs/ask-ai) with the question "What failed in this build, and why?" written for you. Shown only when Ask AI is available |

The page has five tabs. The tab is in the address (`?tab=`), so a link can open a specific one.

### Tests

Every test result in the run. Filter chips (**All**, **failed**, **broken**, **passed**, **skipped**, and **unrecognised** when the report had statuses TestLookup could not map) and a suite filter narrow the list. Columns: test name, suite, status, duration and failure category.

Click a row to open the test beside the list, with **Previous failure** / **Next failure** to step through the failures on the page and **Open full page** for the test result page. The address carries the open test (`?test=`), so a refresh or a shared link opens it again, and **Back** closes it. Ctrl- or ⌘-click opens the full page directly.

While a run is still streaming, the list is read from the live buffer, so it matches what **Live** shows. If a live run shows no tests, **Recover from buffer** asks the server to rebuild them.

### Analysis

The run's AI analysis report. Old links to `/runs/<id>/intelligence` and `/deep-investigate/<id>` open here.

- **vs previous run**: this run's AI analysis against the previous run's (the same pair as **Compare to previous run**): **Compared with #58 (Auth, main, 3 hours earlier)**, the verdict change (**Verdict GO -> NO_GO**), the suggested root causes that are new, resolved or persisting, and the tests whose AI conclusion changed (click one to open it). When the two runs were analysed by different engines (rules on one, a language model on the other) a note says so first: a category change may then reflect the engine, not the product. The summary under it is labelled **AI narrative** when a language model wrote it, or **deterministic summary** when it was built without one (offline, or over the cost budget). **Change baseline** compares with another run of the suite instead (`?ai_baseline=`). When nothing changed the card is one line; it also says when there is no previous run on the branch, or when one of the two runs has no AI analysis.
- **AI history**: which analysis you are looking at, for example **v2 · LLM llama3.1 · about 2 hours ago**. See [AI history](#ai-history) below.
- **The verdict banner**: **Go · ship cleared**, **Conditional Go · proceed with mitigation**, **No-Go · ship blocked** or **Pending · awaiting evidence**, with the risk score, the number of blocking issues and the AI confidence. **Decision trail** shows how the verdict was reached, step by step.
- **The review banner**, when the report is waiting for or has had a human review. See [Your inbox and approvals](/docs/inbox#approvals).
- **What failed**: each failure, with **Open test case**, **Decision trail** and **File defect** (`QA_ENGINEER` or above).
- **Proposed clusters**: the run's failures grouped by likely shared cause. **Analyze failures** starts a deep investigation of this run; it is disabled when the run has no failures, when one is already running, or for roles below `QA_ENGINEER`. See [Root cause (AI)](/docs/failure-analysis#root-cause-ai-deep-investigation).
- Two collapsed sections: **How this score is computed**, and **Evidence behind the verdict** (the pipeline stages that ran, the test outcome, recommended actions by role with your own role's row first and marked **· your role** — QA for testers, QA engineers and QA leads, Release Manager for administrators — the AI confidence, failure categories, step flips and provenance).

The verdict is computed, not written by a model; see [Releases and gates](/docs/releases#how-it-is-produced). The stages behind it are described in [AI agents and the pipeline](/docs/ai-agents).

### AI history

Every time the pipeline analyses a run (**Trigger pipeline**, or the analysis that follows ingestion), TestLookup keeps that analysis as a new **version** instead of overwriting the last one: the verdict and risk, the failure categories and clusters, each analysed test's category, confidence and suggested root cause, and how it was produced (rules, ML or a language model, which model, whether it fell back). Single-test re-analyses, corrections and bulk reclassifications are kept per test (the test's **History** tab shows them).

- The first select picks the version to view. **Current** is the live analysis above; an older version replaces it with a read-only **stored snapshot** (its verdict, the run summary written with it, categories, clusters and per-test conclusions, and how it was produced), with **Back to current** and, when that version published a decision report, **Open decision report v<n>**.
- **Compare with…** opens what changed between two versions, or between a version and **Current data**: the engine change first, then the verdict, categories, failure clusters (added, removed, resized), the tests whose conclusion changed (category or confidence moves of 10 points or more first; a reworded root cause on its own is listed apart), and the recommended actions.
- The choice is in the address (`?ai_version=`, `?ai_compare=`), and it moves the **Evidence** tab's **Report version** with it: picking a version shows the decision report that version published, and picking a report version there selects the version that published it. Old `?report_version=` links open the version that published that report.

"First analysis of this run" means only one analysis exists so far. How many versions are kept, and for how long, is set on [Retention and purge](/docs/administration#retention-and-purge); **Export AI history** in the ⋯ menu downloads them all.

### Changes

**What changed since the last good run**: the pass-rate change, **New failures** and **Resolved** tests against the most recent passing run (the baseline), a list of the new failures, and the commits that landed before this run. Buttons open Compare against the last good build, against the previous run, or empty (**Open Compare**). If no passing run exists to compare with, the tab says so.

**The commits** are the project's own: the range fetched through the project's GitHub or GitHub Enterprise integration (**Settings › GitHub**), the same commits the **Suspects** panel shows, or the commits your CI sent with the run (`Commits supplied with the run (no GitHub call).`). The heading names the build the range starts after, `Commits since build #238 (3)`, and each commit's short hash links to it on GitHub. That build is usually the last green run of the branch; when the branch has none, the range starts at the last completed run and a line says so: `Counted from the last completed run, not the last green one: some of these may predate the failure.` The range is fetched when the tab is first opened, at most once every 6 hours per run, and never while offline mode is on.

When no commits can be listed, one line says why:

| Line | What to do |
|---|---|
| `Commit list unavailable: offline mode (AI_OFFLINE_MODE=true).` | Nothing: offline mode blocks GitHub. CI can still send the commits with the run |
| `Commit list unavailable: GitHub checks are turned off for this project.` | An administrator turns on `github_checks` (**Feature flags**) |
| `Commit list unavailable: this project has no GitHub integration.` | **Connect GitHub in Settings › GitHub** |
| `Commit list unavailable: this project's GitHub integration is turned off.` | Turn it on in **Settings › GitHub** |
| `Commit list unavailable: this run's repository is not the project's GitHub repository.` | The run came from another repository; check the repository in **Settings › GitHub** |
| `Commit list unavailable: no GitHub token is stored for this project.` | Save a token in **Settings › GitHub** |
| `Commit list unavailable: this run carries no commit hash.` | Send the commit with the run (the SDKs and CLI read it from CI) |
| `Commit list unavailable: no earlier run of this branch carries a commit hash to compare with.` | Nothing yet: the next run has this one to compare with |
| `Commit list not resolved yet.` | Open the tab again shortly |
| `Commit list unavailable: GitHub did not answer the comparison. It is tried again after 6 hours.` | Check the project's row on **Integration health** |
| `No commits between build #238 and this run.` | Nothing: the same commit was tested twice |

### Evidence

The detail behind the analysis. Old links to `/agents/run/<id>` open here.

- **Decision intelligence** — the agentic decision report, with a **Report version** picker when the run has been analysed more than once. The picker and the Analysis tab's [AI history](#ai-history) move together. Absent when no decision report exists for the run.
- **The pipeline's AI report** — **View AI report** to read it, and **Agent stages** to see each stage the pipeline ran. When the run has several pipeline runs, a **Pipeline** picker chooses between them.

"No agent pipeline has analysed this run yet." means no pipeline has run; **Trigger pipeline** in the ⋯ menu starts one.

### Timing

How long the run took and where the time went (`?tab=timing`).

- **Execution time** — **Wall clock**, **Test time (summed)**, **Parallelism** (summed ÷ wall clock) and the setup / teardown share, with where the figures come from (the report's own run span, or its tests' start and finish times).
- **Setup, test and teardown** — the time the run's tests spent in each phase, when the report recorded them.
- **Timeline** — one row per worker, one bar per test, coloured by status, so you can see what ran in parallel and where a worker sat idle. Click a bar (or a row of **Slowest tests**) to open that test beside the tab, as on **Tests**. A run with more than 2,000 timed tests shows the 2,000 longest and says so.
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

- **Latest suite comparison** — choose a suite and **Compare latest** to diff its two most recent runs. The section shows how many runs the suite has and its latest one (`12 runs in suite · latest Run #431 · main`); **Pick a different baseline** switches to a manual comparison of that suite and opens the left picker. Needs one project selected.
- **Manual historical comparison** — choose **Left run (baseline)** and **Right run (target)**, optionally limited to one suite, then **Compare**. Each picker searches every run you can see, in every release, by build number, branch, CI job or the start of a commit hash, newest first and 20 at a time (**Load older** for more); or **Paste run ID** and **Use**. A pasted ID that is not a run you can open reads **Run not found or not accessible.**

The result leads with what changed: **Pass rate**, **Failed tests**, **New failures** and **Duration** for each side, an **AI Comparison Report** (key differences, new risks, actions and, when the two runs' AI analyses differ, **AI analysis changes**), an **AI analysis** section, and **Status changes** — how many tests moved from one status to another.

**AI analysis** puts the two runs' AI analyses side by side (**Baseline** on the left, **Target** on the right): the verdict and risk, the engine and model that produced each (**deterministic** when no language model did), the failure categories and the top suggested root causes. Under them, what changed between the two analyses, the same way as the Analysis tab's **vs previous run**, and the label of the narrative (**AI narrative** or **deterministic summary**). Two runs of different projects are not compared. Click a flow to filter the table to those tests. Filter chips narrow the table to **New failures**, **Regressed**, **Still failing**, **Duration spikes**, **Fixed**, **New tests**, **Removed** or **Renamed**.

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
