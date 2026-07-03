# Working with runs

Runs are the unit everything else hangs off. This guide covers the run list's investigation tools — especially **compare** and **bisect-from-green**, the fastest route from "the build went red" to "this change broke it".

## The Runs page (`/runs`)

Each row is one execution with its verdict, pass/fail counts, suite, build, and a real timestamp. Around the list you get window-level context (average pass rate, builds failed, last green build) and per-run **Actions**:

- **Compare to previous** — diff this run against the one before it.
- **Bisect from last green** — jump straight to a comparison between the *last green run* and the *latest failed* one (details below).
- **Deep all failed (N)** — send all the run's failures into a [Deep Investigation](ai-features.md#deep-investigation-deep-investigate-deep-investigaterunid) in one click.

Counts here are **executions** — see the [discrepancy cheat-sheet](dashboards.md#reading-discrepancies-cheat-sheet) for how that relates to unique-test counts elsewhere.

## Run detail (`/runs/:id`)

Everything about one run: per-test rows (status, duration, **failure signature**, error + stack trace), filters, and per-test drill-down (`/runs/:id/tests/:testId`) with cross-run history and — for step-based tests — the ["Cross-Run Step Flakiness" panel](flaky-tests.md#where-flakiness-shows-up). `/runs/:id/intelligence` is the run's AI report.

Two behaviors worth knowing:

- **Live runs are readable immediately** — while a run is still streaming, per-test rows are served from the live buffer, so the detail page matches what **Live** shows instead of appearing empty until finalization.
- **Failures link into triage** — a failing row connects to its cluster, verdict, and assignee (see [Triaging failures](triaging-failures.md)).

## Comparing runs (`/runs/compare`)

The compare view diffs two runs — **Left (baseline)** vs right — and leads with what changed: **new failures** (passed in the baseline, fail now), fixed tests, and **key differences** (duration, counts). Scope with **choose a suite** when the runs span several.

You can reach it three ways: **Compare to previous** on a run row, manually via the URL (`/runs/compare?mode=manual&left=<runId>&right=<runId>&suite=<name>` — shareable), or through **bisect**.

## Bisect from green — the regression shortcut

When a suite goes red, the question is *what changed since it was last green*. **Bisect from last green** builds the compare for you: baseline = the suite's last green run, right = the latest failed run, suite filter = the failing run's suite. The **new failures** list is then precisely the set of tests that broke in the window where the regression landed — cross-reference the commits between those two builds and the culprit is usually obvious.

Bisect needs both a green and a failed run for the suite in the window; if the button is missing, there's no green baseline to anchor on (widen the window).

## A red-build routine

1. **Runs** → the failed run → is it one cluster or many? (One usually means one cause.)
2. **Bisect from last green** → read the new-failures list.
3. Genuinely new failures → find the change between the two builds; file the defect from the cluster.
4. Failures that alternate with green runs → the [flaky workflow](flaky-tests.md) instead.
5. Big/messy run → **Deep all failed** and let the investigation cluster it for you.
