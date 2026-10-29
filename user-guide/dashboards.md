# Dashboards & analytics (moved)

This page moved into the in-app user guide, which is the single source for using TestLookup. Open **? → Documentation** in the app and pick the topic, or read the source in this repository:

| Topic | In the app | Source |
|---|---|---|
| Home, Trends, Coverage, Explorer, what each number counts, why two pages can disagree | `/docs/dashboards` | [dashboards.md](../frontend/src/content/guide/dashboards.md) |
| Reading, filtering from and exporting any chart; limits | `/docs/charts` | [charts.md](../frontend/src/content/guide/charts.md) |
| The Summary report (window and latest modes, PDF and Excel exports, the analysis report), Value metrics | `/docs/reports` | [reports.md](../frontend/src/content/guide/reports.md) |
| Search | `/docs/search` | [search.md](../frontend/src/content/guide/search.md) |

Operator-side settings behind these pages (digests that attach the analysis report, retention) are in [Administration & settings](administration.md).

The two sections below are the reference for cross-run insights and expected runs: what each check reads, its thresholds, and what it does not do. How to use them in the app: [Insights in the Inbox](../frontend/src/content/guide/inbox.md#insights), [Insights on the dashboards](../frontend/src/content/guide/dashboards.md#insights) and [Expected runs in Settings](../frontend/src/content/guide/administration.md#expected-runs).

## Insights

**Insights** are patterns across runs that a single run cannot show. They appear on **Overview**, in a card below the main sections, and in the **Insights** tab of the **Inbox** (`/my-failures?tab=insights`). Both list the same insights and need one project to be selected. A background check runs every hour, at minute 40, for each active project where the `cross_run_insights` flag is on. The flag is on by default ([feature flags](../docs/operations/feature-flags.md)). Insights are rules over results TestLookup already stored. They make no AI call, and they send no notification: the per-run "became slower" and "newly flaky" notifications already cover those changes.

### The four checks

| Check | What it reads | When it fires | Severity |
|---|---|---|---|
| **Perf drift**, suites | The duration findings stored for each run of the last 7 days: the run page's **Did anything get slower?** A suite is slower when the median of its last 5 runs is at least 1.5 × the median of the 20 runs before them, on the same branch | The suite's newest run in the 7 days was found slower | High from 2 × slower, else Medium |
| **Perf drift**, tests | The same stored findings: a test spikes when it takes 3 σ above its baseline of 10 or more runs | The test spiked in at least 3 runs in the 7 days, the latest within 48 hours | High from 6 σ or 5 runs, else Medium |
| **Flake rising** | The flaky-test rule of the **Flaky tests** page, counted per suite for this week and for the week before. A test is flaky when it has at least 3 results, fails 5–95 % of the time, and changes status at least twice | The suite has at least 3 flaky tests this week, at least twice last week's count, and at least 2 more | High at 6 or more and at least 3 × last week's, else Medium |
| **Coverage drop** | Two nightly coverage snapshots (**Coverage › History**): the newest, and the one 7 to 9 days before it | The tests exercised fell by at least 10 % and by at least 5 tests | High from a 25 % drop, else Medium |
| **AI budget** | This month's state of the LLM cost budget (**Settings › LLM cost budget**) | The spend reached the soft warning, or the hard cap | High at the cap, else Medium |

Per-test drift needs the `perf_regression_detection` flag. Without it, only suites are checked, and the card says so. A suite needs 5 runs in each of the two weeks before **Flake rising** judges it. The newest coverage snapshot must be at most 2 days old, and the earlier one must have exercised at least 20 tests. **AI budget** needs the `llm_cost_budget` flag and a budget with a hard cap. Each check keeps at most 20 insights per evaluation, the most severe first.

### What an insight is

An insight is one **episode**: one check firing on one suite, test, project or budget. It opens the first time the check fires. Every hour that the check still fires, it is **confirmed**: its numbers are refreshed and it shows "confirmed 12 min ago". It **resolves** once, in one of two ways:

- **Cleared**: a later check measured the subject and found it quiet.
- **Not seen for 3 days**: it was not confirmed for 72 hours. Per-test drift always ends this way, because the stored findings name the tests that spiked, not the tests that were fine.

A resolved insight never reopens. If the same subject fires again later, that is a new insight. The **24 h** / **7 d** switch lists the insights confirmed in that window, open ones first, then by severity.

### Not measured is not "all clear"

A check that cannot judge says why, under the list. For example: "Coverage drop — not measured: There is no coverage snapshot from a week before the newest one." A check that cannot judge never closes an insight. The list shows a ✓ only for a check that ran and found nothing. When a check last ran more than 3 hours ago, the line adds "checked 5 h ago". A check that failed says so and is tried again the next hour.

### Dismiss

A QA engineer or above can **Dismiss** an insight. This hides it for everyone in the project for the rest of its episode. A dismissed insight that is still firing stays dismissed. There is no undo. **Show dismissed (n)** in the Inbox lists dismissed insights with who dismissed them, and each dismissal is recorded in the project's Activity log. A new episode of the same subject shows again.

## Expected runs

An **expected run cadence** says: "a run of this suite (or any run of the project) arrives by this local time on these days, in this time zone, with this much grace". QA leads and administrators set cadences in **Settings › Project › Expected runs** (`/settings/expected-runs`). Other roles can read them. A project can have up to 20 cadences, at most one per suite and one for "any run".

- **Days and time.** Pick weekdays (Mon–Sun), an expected-by time, an IANA time zone such as `Europe/Berlin`, and a grace of 0–720 minutes (60 by default).
- **Cron (advanced).** Five fields, `minute hour * * day-of-week` (`0` = Sunday), at most 24 expected times a day. Day-of-month and month must be `*`.

How a cadence is checked:

- **Windows.** Each expected-by time is a deadline. Its window runs from the previous deadline + grace to this deadline + grace, so every run counts for exactly one window. A run 20 minutes late, inside the grace, still counts for its own day.
- **What counts.** A window is **met** when at least one run of the suite arrived in it, whatever its result. "Arrived" means when TestLookup received the run. The window is **missed** when none arrived. It is **pending** until its deadline + grace has passed.
- **When.** A check every 15 minutes (minutes 07, 22, 37 and 52) records each window that ended. It never decides the same window twice.
- **Daylight saving.** A deadline at a time that does not exist, or that occurs twice on a daylight-saving day, takes the later of the two instants. Daylight saving can therefore move a deadline later but never earlier, so it never causes a false miss.
- **Pause.** **Pause** until a date at most 90 days ahead, with a reason. Windows whose deadline falls inside the pause are **paused**, not missed, and Trends shows the pause and who set it. **Resume** makes the next deadline expected again. A pause that runs out simply stops applying.
- **History stays.** Windows already recorded are never rewritten. An edited cadence applies from the moment it is saved, so nothing earlier counts as missed under the new definition.
- **Observe only.** A cadence is TestLookup's own expectation. Nothing here starts, stops or pauses your CI.

Where cadences show:

- **Trends › Volume.** The callout reads **Expected runs missed** with the exact count, and one line per cadence, such as `nightly-regression: 2 expected runs missed (Tue, Wed)`. It reads **Expected runs paused** when a cadence is paused. Without a cadence, a gap in the runs reads **Run gap in this window** with the silent days and `No expected cadence configured — Configure`. It no longer guesses a nightly schedule.
- **Coverage.** In the score explanation, **Run cadence** is met ÷ expected (`4 of 5 expected runs`) when a cadence is configured. Otherwise the dimension is **Days with runs**, the share of days that had a run.

**An expected run was missed** is a notification you opt in to in **My notifications**, by email, Slack or Teams. It is off by default. Each check sends one message per cadence, listing every window newly missed. A window is never announced twice, and paused windows are not counted. The bell shows the message without a link, and the e-mail links to Trends.
