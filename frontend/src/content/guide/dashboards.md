# Dashboards and metrics

What each view counts, and — just as important — what an empty one means.

> **Important.** TestLookup does not invent numbers to fill a chart. An empty panel means *no qualifying data in this scope and window*, not zero quality. Read the scope before reading the number.

## The three things that decide what you see

Every dashboard is filtered by all three at once:

1. **Project scope** — a specific project, or All Projects.
2. **Time window** — most views default to a recent window; run listings default to **30 days**.
3. **Record eligibility** — runs from deleted projects are excluded everywhere.

If a page looks wrong, check these three before anything else. Together they explain the large majority of "the data is missing" reports.

## The views

| View | Answers | Notes |
|---|---|---|
| **Home** (`/overview`, titled **Dashboard**) | What is the current state? | A **Release readiness** banner, then **Total executions**, **Avg pass rate**, **New failures · 24h**, **Flaky tests** and **Active defects**; each card opens the page behind it. Under the banner, **How this verdict is decided** names the policy that decides it (`Decided by Payments gate v4 (project policy): Go at 95% or more, Conditional from 90%, No-Go below 90%.`; with no published policy, `Decided by the built-in rule (…)` and that rule: Go at 95% or more with no open defects and at most 5 flaky tests, Conditional from 85% with at most 5 open defects), links to it with **Open policies**, and lists any hard cap that forced No-Go (`Open critical (P0) defects: 2, cap 0`). A project with no runs yet shows the first-run guide instead |
| **Runs** | What has executed recently? | Defaults to 30 days; `days=0` for all time via the API. See [Runs and run detail](/docs/runs) |
| **Live** | What is running right now? | Only streamed runs; the pass rate is flagged by the policy bands. See [Watching runs live](/docs/live) |
| **Trends** | Is this getting better or worse? | Needs history — sparse data makes trends meaningless. The pass-rate chart's dashed line is the project policy's GO line (`GO ≥ 95%` under the default bands) and the shaded band below it the No-Go zone (`No-Go < 90%`); the sentence above the chart says how far the window's pass rate is from the GO line and whose bands those are. See [Which line is the target](/docs/releases#which-line-is-the-target) |
| **Coverage** | What is being exercised? | Coverage of *executed tests*, not code coverage |
| **Failures** | What is failing and why? | Grouped by category and cluster. See [Investigating failures](/docs/failure-analysis) |
| **Flaky tests** | Which tests are unreliable? | Nothing below 5 observations — see [Flaky tests](/docs/flaky) |
| **Release gate** | Is this release ready? | See [Releases and gates](/docs/releases) |
| **Inbox** | What is assigned to me, and what is waiting for my approval? | See [Your inbox and approvals](/docs/inbox) |

The AI verdict for each run is a column of the **Runs** table, and the full analysis is the run's **Analysis** tab. There is no separate AI dashboard; the old `/intelligence` address opens **Runs**.

> **Tip.** In the **Inbox**, most auto-assignments go to a synthetic project QA-lead account. If it looks empty for a QA lead or administrator, switch the scope to the team view. See [Your inbox and approvals](/docs/inbox).

## Explorer

**Trends → Explorer** (`/explore`) draws one metric over time as small multiples. See [Charts and visual analysis](/docs/charts) for reading, zooming and exporting its charts.

- **Metric** — any of the sixteen the chart API serves: counts, rates and durations.
- **X axis** — day or week. Every panel shares the same time axis.
- **Lines** — one line, or one per environment, branch, status, test and so on. With one panel per suite, a test split shows that suite's tests; otherwise the 7 tests with the most executions are drawn and the rest fold into "Other".
- **Panels** — one panel, or one per suite, release, branch, environment, status, failure category or ingestion source. Release panels need one project selected: a release belongs to one project. The pickers only offer combinations the API accepts; a refused option says why — for example **Runs** cannot be split by status or failure category, because one run holds tests of several statuses and would be counted in more than one panel.
- **At most 12 panels**, the values with the most executions in the window. When there are more, the page says how many. Runs with no value for the panels' dimension (no branch, no environment, …) are not shown as a panel, and the page says so ("Runs with no branch are not shown."). Status panels read **Passed**, **Failed**, **Broken**, **Skipped**; a status or failure-category panel counts test executions, so its pass rate is that panel's own share.
- **Y-scale** — *Shared* puts every panel on one scale, so heights compare across panels; *Own* fits each panel to its own values, which shows shape but not size. Rates always share 0–100%. Each panel states which scale it is on.
- A shared **count** scale is the largest value of the panels loaded so far. Panels load as you scroll to them, so the scale can widen when a lower panel is larger, and the panel says so ("from 5 of 12 panels").
- **Customise** (beside **Views**) gives the grid a **Title** (a heading over the panels, up to 80 characters) and sets **Lines per panel** — 3, 5 or 7, the busiest first and the rest as **Other** (pick **Lines** first). The options no series chart offers (pie, stacked rates, a log scale, more than 7 lines) are listed with the reason.

The configuration — the title and lines per panel included — is in the URL, so a link opens the same view. Inside one project, **Views** saves it, with the window and the top-bar release, as a named view.

## Reading a metric honestly

For any number on any dashboard, ask:

- **What is counted?** Executions, unique tests, or runs — these differ a lot.
- **Over what window?** A 7-day and a 30-day pass rate are different measurements.
- **Unique across the window, or latest run only?** Summary counts distinguish these; they are not interchangeable.
- **Which project?** All Projects mixes suites with different cadences.

## When two pages disagree

| Symptom | Likely cause |
|---|---|
| Two pages show different totals | Different window (check each page's window picker), or the Summary report is in **Latest** mode while the other page counts the whole window. See [Reports and outputs](/docs/reports#the-summary-report) |
| Executions far above unique tests | Both are right: Runs counts executions; Coverage and the Summary report count unique tests across the window |
| A suite's numbers differ from one run's | Suite pages aggregate the window; a run's page is one run |
| Everything empty | No project selected, or the window predates your data |

## Coverage means executed, not code

Coverage here is about which tests ran and what they exercised **according to the reports you sent**. It is not line or branch coverage, and it cannot be — TestLookup never sees your source.

- **The coverage map** draws at most 500 tiles a level. A level with more items keeps the 498 largest and combines the rest into **Other (n)**; open it for the next items of the same level (the breadcrumb reads `Other · page 2`, and the level's own crumb takes you back). A suite whose tests the last suite sync marked for review shows `n need review`; see [Test management](/docs/test-management#suites).
- **Last run.** Each row of **Suite coverage breakdown** shows how long ago the suite last ran (`2h`, `2d`, `14d`), linking to that run. It turns amber when the suite has gone longer without a run than the project usually waits between runs, and its tooltip says how often that is.
- **History** (the third tab, `/coverage?tab=history`) draws **Coverage history**: the tests in the catalogue and the share of them exercised in the 30 days ending each day. A snapshot is taken every night at 00:05 UTC (the first night also fills in the 30 days before it), so the line starts with your first snapshot — before it the tab says `No coverage snapshot yet: the first is taken at 00:05 UTC.` — and a night without a snapshot is a gap. A snapshot covers the whole project: the page's release and suite filters do not apply to it. Notes pinned on the timeline are drawn here too (see [Notes on the timeline](/docs/charts#notes-on-the-timeline)).
- **Fix tagging** (QA engineers and above, one project) on the **Untagged executions** callout lists the tests whose executions carried no suite label, with their executions, last execution and current catalogue suite, 50 at a time. Tick tests, pick a suite and **Move** them: they are filed under that suite in the catalogue (Test Management, Suites, the coverage map). A QA lead can also make that suite the default for new unlabelled tests. A test that is not in the catalogue yet cannot be moved until its run is finalised.

> **Important.** Moving a test changes the **catalogue**, not its executions. Past and future executions keep the suite label your test reporter sends, so the untagged count only drops once the reporter sends one. To label future runs, set the suite in the reporter; see [Getting results in](/docs/ingestion#key-behaviours).

## Value by team

**Reports → Value**, **By team** (`/value-metrics?view=team`, one project) splits the window's estimated hours saved by the team that owns each test — the same owner the failure pages name, from the project's ownership rules and component map. One row per team, then **Unassigned** for tests no rule matches: tests, pass rate (skipped tests are not evaluated), flaky tests, defects, the mean time to resolve them (**—** when none was resolved) and hours saved. The triage and dedup hours follow the tests in each failure cluster (a cluster spanning two teams is shared between them); the hours for runs that a quarantine unblocked belong to a run, not a test, so the footer lists them as **Not attributable to a team**, and the teams plus that line add up to the headline.

A QA lead's team names link to that team's rules on **Ownership** (`/ownership?team=…`). A project without ownership rules shows `No ownership rules yet` instead, with **Set up ownership** for QA leads.

## Empty states

| Empty view | Most likely cause |
|---|---|
| Everything empty | No project selected, or All Projects with no accessible data |
| Empty but runs exist | Time window excludes them |
| Trends flat/absent | Not enough history |
| Flaky list empty | No test has reached 5 observations, or none scores highly |
| A run's Analysis tab empty | Analysis has not run on that run, or produced no qualifying output |

## Related

- [Charts and visual analysis](/docs/charts)
- [Concepts and terminology](/docs/concepts)
- [Troubleshooting](/docs/troubleshooting)
