# Step-by-step workflows

Task recipes. Each names the role it needs and what success looks like.

## Ingest and inspect a run

**Role:** any with project access · **Prerequisite:** a project and an API key

1. Send results — see [Getting results in](/docs/ingestion).
2. Open **Runs** and find your `build_number`.
3. Open the run; check totals match what your suite reported.

**Expected:** per-test rows with statuses and durations.
**If not:** check the time window (30-day default), the project selector, and that the report parsed to more than zero results.

## Investigate a failed test

**Role:** `TESTER` or higher

1. Open the run → the failing test.
2. Read the **evidence** first — error text and stack trace.
3. Read the **category**, then the suggested root cause.
4. Check **similar failures** — has this happened before?
5. Decide, and record a correction if the category is wrong.

**Expected:** you know whether this is new, recurring, or environmental.
**Decision point:** flipping between pass and fail? That is [flakiness](/docs/flaky), not a failure to debug.

## Review and correct an AI classification

**Role:** `QA_ENGINEER` or higher

1. Open the failure and read the evidence behind the category.
2. If the evidence does not support it, record the correct category.
3. Your correction is stored alongside the original.

**Expected:** the trail shows both what the system said and what you decided.

## Identify and manage a flaky test

**Role:** `QA_ENGINEER` or higher

1. Open the flaky views.
2. **Check the confidence band before the score.** Below 5 observations there is no score at all.
3. Look at the actual outcome history — flipping, or consistently failing?
4. Consider environment disagreement before blaming the test.
5. Record a quarantine recommendation if warranted.

**Expected:** a decision with evidence.
**Important:** quarantine here is a record. **It does not change your suite** — that change is yours to make.

## Find similar historical failures

**Role:** any with project access

1. From a failure, open similar failures; or use global search.
2. Compare the evidence, not just the wording.

**If results look shallow:** semantic search may be unavailable and search may have fallen back to keyword matching. Ask an administrator.

## Promote a failure to a defect

**Role:** `QA_ENGINEER` or higher

1. Open the cluster.
2. Review severity and evidence.
3. Promote. Where an issue tracker is configured, a ticket can be filed.

**Expected:** a tracked defect carrying its evidence.
**Note:** automatic promotion during the pipeline is off unless enabled.

## Create and evaluate a release

**Role:** `QA_LEAD` or higher to override

1. Create the release and associate runs.
2. Open the release gate.
3. Read **blocking issues and conditions** — not just the GO / NO_GO word.
4. Override if you disagree; the override is recorded.

**Expected:** a recommendation with reasoning you can act on or overrule.

## Generate and share a summary report

**Role:** `QA_ENGINEER` or higher

1. Open the run or release.
2. Generate the report.
3. **Review the contents before sharing** — reports carry error text and stack traces from your tests.

## Configure an AI provider

**Role:** `ADMIN`

1. **Settings → AI configuration**.
2. Choose provider and model; save credentials in the deployment's secret configuration, never in a ticket.
3. Run one analysis and check the routing trail shows the mode you expect.

**Expected:** the routing record shows `llm` rather than a fallback reason.

## Reindex search

**Role:** `ADMIN`

1. Trigger a reindex from administration.
2. Reindexing is not instant; check index health afterwards.

## Diagnose ingestion or analysis failure

**Role:** `QA_ENGINEER` or higher; `ADMIN` for logs

1. Did the run appear at all? No → ingestion. Yes → analysis.
2. Ingestion: check format support, the file is non-empty, and the response was 202.
3. Analysis: check whether the run is `partial` — the pipeline ran but did not verify.
4. Check the stage records to see which stage fell back and why.

See [Troubleshooting](/docs/troubleshooting).

## Related

- [Getting started](/docs/getting-started)
- [How decisions are made](/docs/decisions)
