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
| **Home** (`/overview`, titled **Dashboard**) | What is the current state? | A **Release readiness** banner, then **Total executions**, **Avg pass rate**, **New failures · 24h**, **Flaky tests** and **Active defects**; each card opens the page behind it. A project with no runs yet shows the first-run guide instead |
| **Runs** | What has executed recently? | Defaults to 30 days; `days=0` for all time via the API. See [Runs and run detail](/docs/runs) |
| **Live** | What is running right now? | Only streamed runs. See [Watching runs live](/docs/live) |
| **Trends** | Is this getting better or worse? | Needs history — sparse data makes trends meaningless |
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
- **Panels** — one panel, one per suite, or one per release. Release panels need one project selected: a release belongs to one project. The pickers only offer combinations the API accepts; a refused option says why.
- **At most 12 panels**, the suites or releases with the most executions in the window. When there are more, the page says how many; runs with no suite are not shown as a panel.
- **Y-scale** — *Shared* puts every panel on one scale, so heights compare across panels; *Own* fits each panel to its own values, which shows shape but not size. Rates always share 0–100%. Each panel states which scale it is on.
- A shared **count** scale is the largest value of the panels loaded so far. Panels load as you scroll to them, so the scale can widen when a lower panel is larger, and the panel says so ("from 5 of 12 panels").

The configuration is in the URL, so a link opens the same view. Inside one project, **Views** saves it, with the window and the top-bar release, as a named view.

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
