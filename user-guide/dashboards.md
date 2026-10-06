# Dashboards & analytics

The read-side pages: where to look for *how are we doing*, and — just as important — what each number actually counts, so two pages never look like they disagree when they're answering different questions.

## The golden rule: the shared time window

Every analytics page uses the **same global time window** (7 days, 30 days, …): pick it once and it follows you across Overview, Trends, Coverage, Failure Analysis, and the reports. If two pages seem inconsistent, check the window picker *first* — it's the most common explanation.

## Overview (`/overview`)

The landing dashboard: **average pass rate** (a real weighted pass rate — the hero shows honest numbers, not derived confidence), the current release verdict, run counts, **average run duration**, **active defects**, automation coverage, trend, and current blockers. "Last run" is day-granular ("today" / "yesterday" / "N d ago") because the trend is day-bucketed.

## Trends (`/trends`)

Pass-rate and volume over time: **daily breakdown**, **days with runs**, customizable widgets, and an **email report** option. Use it for direction ("are we getting healthier?") rather than point-in-time counts.

## Coverage (`/coverage`, `/coverage/suite`)

Suite-level health: which suites ran, unique tests per suite, pass rates, **coverage gaps**, and **compare to previous window**. Also the home of suite-label hygiene — **bulk-apply suite labels to untagged executions** keeps old runs grouped under the right suites. Counting semantics: Coverage counts **unique tests across the window** per suite.

## Summary Report (`/reports/summary`)

The exportable roll-up (pick a single project). Its **Aggregation mode** is the semantics switch:

- **Window** — unique tests across the whole window; totals **match Coverage**.
- **Latest** — the latest run per suite only; totals are *deliberately smaller* than window mode.

If the summary "doesn't match" another page, it's almost always the aggregation mode or the window — both are shown on the page.

The page also offers **Download analysis report (1d / 7d)** — the self-contained HTML analysis report that daily/weekly email digests can attach (see [Administration → Digests](administration.md)). It bundles the executive summary, runs, failures for investigation, flaky & quarantine, slowest tests, release gate, defects, and ownership into one offline-readable file, using the same window semantics as this page.

## Charts on the report pages

Overview, Trends, Coverage, Failure Analysis, Suite detail, the Summary Report, the Release gate and Run compare draw their charts in one shared frame. The in-app guide, **Docs → Charts and visual analysis** (`/docs/charts`), covers it in full; the essentials:

- **Read the footer first.** It states the scope, "Showing top N of M" when lines were folded into *Other*, and the run and execution totals. **—** means *not measured* (there was nothing to count), never zero; a pass rate leaves skipped results out of both sides, so a day of only skips is a gap.
- **Interact.** Hover for the tooltip; select a legend entry to hide a line (Shift shows it alone); drag the range brush to zoom (the footer, table and export then cover the zoomed days only); **View as table** shows the numbers; **Full screen** enlarges one chart; *Presentation mode* (sidebar footer) enlarges the whole app for screen sharing.
- **Filter the page from a chart.** Shift- or Ctrl-click a suite to set the page's **Test suite** filter (Trends, Coverage, Failures), or a release to set the top-bar release (one project selected).
- **Export a chart.** Every chart's **Export** menu offers PNG, SVG and CSV; each file records the project, release, suite, window, totals, when it was generated and the app version. Whole-report PDF/Excel exports are the Summary Report's (large ones run in the background and stay downloadable for 7 days).
- **What each page adds.** Trends: pass rate by suite (**Customise** to change metric, lines and buckets), the p50/p95 duration band, trend overlays, **Compare** (pick suites and releases in the card; it starts with the three busiest suites) and a suite-by-day heatmap. Coverage: the test coverage map and pass-rate heatmaps. Failure Analysis: failures grouped by message, flake clusters, a drill-down from suite to execution, and the duration-vs-failure-rate scatter with an opt-in **View in 3D**. Run compare: how each test's status changed between two runs.
- **Explorer (`/explore`)** draws one metric as small multiples, one panel per suite or release (up to 12). **Views** on each report page saves the window, release and suite filters per project, privately or shared with the project.
- **Limits:** 8 lines per chart (7 + Other), windows of 1-365 days (per-execution charts up to 90), 2,000 scatter points by default (5,000 max), and 120 chart requests a minute per user (60 for heatmaps, the coverage map, failure groups and the scatter). Past the limit a chart says *Waiting to retry* and retries by itself.

## Value Metrics (`/value-metrics`)

The ROI page: the **engineer-hours-saved headline** (last 30 days, with FTE equivalent and a monthly trend), plus the operational counters — **defects auto-grouped**, **duplicate tickets prevented**, flaky tests identified, **automated go/no-go assessments**, and AI reports generated. The headline's math, per-project tunable assumptions, and honesty caveats are documented in [Value Metrics — the engineer-hours-saved model](value-metrics.md).

## Search (`/search`)

Global search across runs, tests, suites, and defects, with an **entity scope** filter. The page shows **index freshness / index ready** status — if results look stale, that's the tell (admins can configure the search index from here).

## Reading discrepancies (cheat sheet)

| Symptom | Likely cause |
|---|---|
| Two pages show different totals | Different window, or window-vs-latest aggregation (see Summary Report) |
| Executions ≫ unique tests | Both are correct: runs page counts executions; Coverage counts unique tests |
| Suite counts differ from a run's own numbers | Suite pages aggregate the window; the run page is one run |
| Everything empty | No project selected, or the window predates your data |
