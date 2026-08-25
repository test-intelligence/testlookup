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

## Quarantine is a recommendation, not an action

TestLookup can recommend quarantining a test and record that a quarantine was requested or approved. **It does not change your test suite.** Nothing here skips, disables, retries or reruns a test — acting on the recommendation is a change you make in your own repository.

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
