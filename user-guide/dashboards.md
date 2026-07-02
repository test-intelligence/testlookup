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

## Value Metrics (`/value-metrics`)

The ROI page: **defects auto-grouped**, **duplicate tickets prevented**, triage time saved, flaky tests identified, **automated go/no-go assessments**, and AI reports generated — the numbers for the "what is this tool saving us" conversation.

## Search (`/search`)

Global search across runs, tests, suites, and defects, with an **entity scope** filter. The page shows **index freshness / index ready** status — if results look stale, that's the tell (admins can configure the search index from here).

## Reading discrepancies (cheat sheet)

| Symptom | Likely cause |
|---|---|
| Two pages show different totals | Different window, or window-vs-latest aggregation (see Summary Report) |
| Executions ≫ unique tests | Both are correct: runs page counts executions; Coverage counts unique tests |
| Suite counts differ from a run's own numbers | Suite pages aggregate the window; the run page is one run |
| Everything empty | No project selected, or the window predates your data |
