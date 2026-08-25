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
| **Overview** | What is the current state? | Project-scoped; empty until a project is selected in some panels |
| **Runs** | What has executed recently? | Defaults to 30 days; `days=0` for all time via the API |
| **Trends** | Is this getting better or worse? | Needs history — sparse data makes trends meaningless |
| **Coverage** | What is being exercised? | Coverage of *executed tests*, not code coverage |
| **Failure analysis** | What is failing and why? | Grouped by category and cluster |
| **Flaky analytics** | Which tests are unreliable? | Nothing below 5 observations — see [Flaky tests](/docs/flaky) |
| **Intelligence hub** | Cross-cutting AI output | Depends on the pipeline having run |
| **Release gate** | Is this release ready? | See [Releases and gates](/docs/releases) |
| **My failures** | What is assigned to me? | Has a Mine / Team scope toggle |

> **Tip.** On **My failures**, most auto-assignments go to a synthetic project QA-lead account. If the page looks empty for an administrator, switch the scope toggle to **team**.

## Reading a metric honestly

For any number on any dashboard, ask:

- **What is counted?** Executions, unique tests, or runs — these differ a lot.
- **Over what window?** A 7-day and a 30-day pass rate are different measurements.
- **Unique across the window, or latest run only?** Summary counts distinguish these; they are not interchangeable.
- **Which project?** All Projects mixes suites with different cadences.

## Coverage means executed, not code

Coverage here is about which tests ran and what they exercised **according to the reports you sent**. It is not line or branch coverage, and it cannot be — TestLookup never sees your source.

## Empty states

| Empty view | Most likely cause |
|---|---|
| Everything empty | No project selected, or All Projects with no accessible data |
| Empty but runs exist | Time window excludes them |
| Trends flat/absent | Not enough history |
| Flaky list empty | No test has reached 5 observations, or none scores highly |
| Intelligence empty | Analysis has not run, or produced no qualifying output |

## Related

- [Concepts and terminology](/docs/concepts)
- [Troubleshooting](/docs/troubleshooting)
