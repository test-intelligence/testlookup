# Flaky tests

A **flaky test** is one whose result changes without the thing under test changing. TestLookup does not guess at that from a single run — it scores tests from observed history, and refuses to score at all when the history is too thin.

Everything below is read from `services/flaky_score_service.py`. The numbers are the ones the code uses.

## The score is four signals, weighted

Each signal is already a rate between 0 and 1. They are combined with a **weighted mean** — deliberately the dullest possible combiner, because anything cleverer would be unfalsifiable at this data volume and impossible to explain to the person whose test it just scored.

| Signal | What it measures | Weight |
|---|---|---|
| `result_volatility` | Pass/fail flips per adjacent run pair | **0.45** |
| `retry_rate` | Share of runs needing an in-run retry | **0.25** |
| `environment_instability` | Outcome disagrees across environments | **0.20** |
| `duration_variance` | Coefficient of variation of runtime | **0.10** |

Result volatility carries the most weight because a pass/fail flip on unchanged code is the closest thing to direct evidence of flakiness. The other three corroborate; none is diagnostic alone. **A slow test is not a flaky test** — which is why duration carries the least.

> **Note.** Weights are stored on each score row. Changing them later does not silently reinterpret history you have already collected.

## Confidence is separate from the score, on purpose

A test seen 4 times and a test seen 400 times can both produce a score of 0.5. Collapsing those into one number is exactly how a thin-history guess comes to look like a measurement. So confidence is a **separate band, derived only from observation count**:

| Observations | Confidence | What is reported |
|---|---|---|
| Fewer than **5** | `none` | **No score at all** — the evidence floor |
| 5 – 9 | `low` | Score, flagged as weakly evidenced |
| 10 – 19 | `medium` | Score |
| 20 or more | `high` | Score |

`MIN_OBSERVATIONS = 5`. Below it the payload carries a reason such as *"only 3 observation(s); 5 required"* rather than a number.

> **Below five observations you get "insufficient", not zero.** A new test is not a stable test, and reporting 0.0 would read as evidence of stability that nobody has. The components observed so far are still shown, so you can see what little is known.

> **Important.** "No flaky tests" and "not enough data to say" are different answers. TestLookup reports them differently, and you should read them differently.

## Worked examples

These use result volatility alone — the dominant signal — to show the shape. A real score also folds in retries, environment disagreement and duration spread.

| Outcome history | Adjacent flips | Reads as |
|---|---|---|
| `PASS PASS PASS PASS PASS` | 0 of 4 | **Stable.** Volatility 0. |
| `FAIL FAIL FAIL FAIL FAIL` | 0 of 4 | **Consistently failing — not flaky.** Volatility 0. Something is broken; that is a different problem. |
| `PASS FAIL PASS FAIL PASS` | 4 of 4 | **Strong flaky candidate.** Volatility 1.0. |
| `PASS PASS FAIL PASS PASS` | 2 of 4 | **Weak candidate.** Volatility 0.5, and with only 5 observations the confidence band is `low`. |
| `PASS FAIL PASS` | 2 of 2 | **Not scored.** Only 3 observations — below the floor of 5. |

The third and fourth rows are the point of the whole feature: **a test that always fails is not flaky.** Flakiness is about *inconsistency*, and a permanently red test is perfectly consistent.

## Duration variance is clamped

Duration uses the coefficient of variation (standard deviation ÷ mean). At or above `DURATION_CV_CEILING = 1.0` the runtime is treated as fully unstable. Above 1.0 the standard deviation already exceeds the mean, which is an extreme spread; clamping stops one pathological run from dominating the score.

## Scope and isolation

- Scores are computed **per project**. Nothing crosses a project boundary.
- Each scoring pass reads at most `MAX_SCORING_ROWS = 200_000` rows per project — a memory ceiling on the sweep, not a statistical choice.
- Scoring runs as a background sweep over active projects, so a newly ingested run does not update scores instantly.

## The Flaky tests page

**Flaky tests** in the sidebar (`/flaky`) works on one project at a time; in **All Projects** it asks you to pick one. Old links to `/flaky-coach` and `/quarantine` open here. It has four tabs:

| Tab | Shows |
|---|---|
| **Detected** | Every test the detector considers unstable over the last 30 days, with its recent history, failure percentage, a recommendation (**Quarantine**, **Investigate**, **Monitor** or **Healthy**) and its impact. Filter by recommendation |
| **Proposed** | Quarantine proposals waiting for a decision |
| **Quarantined** | Tests currently in quarantine |
| **History** | Released, rejected and expired quarantines |

Click a test on **Detected** for the detail: total and failed runs, when it became flaky, the failure rate with its 95% confidence interval, the ML flakiness confidence, the likely cause, the step that fails (for step-based tests), and suggested stabilisation actions. The test's own page shows **Cross-run step flakiness**: which step flips between pass and fail across runs, so you can fix that step rather than the whole test.

**Refresh** recomputes the detection now (`QA_ENGINEER` or higher). The three quarantine tabs carry an **Experimental** badge.

## Quarantine on the Flaky tests page

> **Off by default.** Quarantine proposals are switched by the `flaky_auto_quarantine` feature flag, which ships **off** for every project. Until an administrator turns it on (**Settings → System → Feature flags**), a QA lead sees "Quarantine is off for this project, so no test can be proposed for it." and **Mute** on the Failures page is disabled. The flag reference is in the repository docs (`docs/operations/feature-flags.md`).

With the flag on, quarantine is a reviewed, audited holding state:

1. **Propose.** A QA lead clicks **Propose quarantine** on a **Detected** test (or **Mute** on the Failures page) and gives a reason. The detector also proposes tests whose verdict is flaky; a test that fails consistently with the same error is a regression and is never proposed.
2. **Decide.** On **Proposed** (or **Inbox → Approvals**), a QA lead clicks **Approve** or **Reject**, then confirms. A proposal nobody decides **expires after 7 days**.
3. **Quarantined.** An approved test is quarantined for **14 days** by default. It still runs and still records results; it is only left out of the release gate's failure signal and out of CI gating with `testlookup ci-verdict`. A row can show **stale** when it has been quarantined beyond its time limit, and **Ready to promote** once it has passed consistently again.
4. **Release.** When the fix lands, **Release** (or **Promote out**, when it is ready to promote) and confirm. The test counts again.

Proposing, approving, rejecting and releasing need `QA_LEAD` or higher; everyone else sees the tables without the decision buttons. **File Jira** on a proposed or quarantined test (`QA_ENGINEER` or higher) opens the Jira dialog described under [Promoting to a defect](/docs/failure-analysis#promoting-to-a-defect). Every transition is recorded in the audit log.

## Quarantine is a recommendation, not an action

TestLookup can recommend quarantining a test and record that a quarantine was requested or approved. **It does not change your test suite.** Nothing here skips, disables, retries or reruns a test — acting on the recommendation is a change you make in your own repository.

What a quarantine does change is how TestLookup *reads* the test's failures: the release gate leaves them out, and a CI pipeline that calls `testlookup ci-verdict` can pass a build whose only failures are quarantined. Setting that up is described in the operator guide in the repository (`user-guide/flaky-tests.md`).

## Limitations, stated plainly

- **A genuine intermittent product bug looks like flakiness.** Both produce pass/fail flips. The score cannot tell them apart; only investigation can.
- **Infrastructure noise inflates it.** A flaky CI agent makes healthy tests look flaky. `environment_instability` exists to surface that, but it is a signal, not a filter.
- **Sparse history under-reports.** A test run twice a month may never reach the floor.
- **Retries can mask flips.** If your framework retries internally and reports only the final result, the flip may never reach TestLookup — which is what `retry_rate` is for, where the report carries it.

## When a classification looks wrong

1. **Check the observation count first.** A `low` band on 5 observations is a weak signal, not a verdict.
2. **Look at the actual history** rather than the score. Is it flipping, or consistently failing?
3. **Check environments.** Disagreement across environments points at the environment, not the test.
4. **Check whether retries are being reported.** A framework that hides retries hides the evidence.
5. **Record your conclusion.** A human judgement stored against the test is worth more than the score.

## Related

- [How decisions are made](/docs/decisions) — where flakiness sits among the other decisions
- [Investigating failures](/docs/failure-analysis) — for tests that are failing, not flipping
- [Troubleshooting](/docs/troubleshooting)
