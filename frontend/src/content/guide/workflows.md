# Step-by-step workflows

Task recipes. Each names the role it needs and what success looks like.

## Ingest and inspect a run

**Role:** `QA_ENGINEER` or higher to send results; anyone with project access to read them · **Prerequisite:** a project, and an API key for CI

1. Send results — **Runs → Upload report** in the app, or the API, CLI or an SDK; see [Getting results in](/docs/ingestion).
2. Open **Runs** and find your `build_number`.
3. Open the run; check totals match what your suite reported. See [Runs and run detail](/docs/runs).

**Expected:** per-test rows with statuses and durations.
**If not:** check the time window (30-day default), the project selector, and that the report parsed to more than zero results.

## Investigate a failed test

**Role:** `TESTER` or higher

1. Open the run → the failing test (or start from your **Inbox**).
2. Read the **evidence** first — error text and stack trace.
3. Read the **category**, then the suggested root cause (**Analyse Root Cause** if there is none yet).
4. Check the test's **History** tab — has this happened before? Is it flipping?
5. Decide, record a correction if the category is wrong, and set the failure's status in your **Inbox**.

**Expected:** you know whether this is new, recurring, or environmental.
**Decision point:** flipping between pass and fail? That is [flakiness](/docs/flaky), not a failure to debug.

## Review and correct an AI classification

**Role:** `QA_ENGINEER` or higher

1. Open the failed test and read the evidence behind the category.
2. If the evidence does not support it, **Correct** → pick the right category → **Record correction**. If it does, **Confirm**.
3. Your correction is stored alongside the original, and applied the next time the same test is analysed.

**Expected:** the trail shows both what the system said and what you decided.

## Review an AI report

**Role:** `QA_LEAD` or higher

1. **Inbox → Approvals**, filter **AI reports**.
2. **Open report** and read it against its evidence on the run's **Analysis** tab.
3. **Accept**, or **Reject** with a reason; then confirm.

**Expected:** the report's banner reads **Reviewed and accepted** or **Rejected by a reviewer**. See [Your inbox and approvals](/docs/inbox#ai-reports).

## Identify and manage a flaky test

**Role:** `QA_ENGINEER` or higher to investigate; `QA_LEAD` to quarantine

1. Open **Flaky tests** → **Detected** and click the test.
2. **Check how much history there is before trusting the number.** Below 5 observations there is no flakiness score at all.
3. Look at the actual outcome history — flipping, or consistently failing?
4. Consider environment disagreement before blaming the test, and check **Cross-run step flakiness** for the step that flips.
5. If warranted, a QA lead clicks **Propose quarantine** (needs the `flaky_auto_quarantine` flag on for the project), and a QA lead approves it under **Proposed**.

**Expected:** a decision with evidence.
**Important:** quarantine here is a record. **It does not change your suite** — that change is yours to make.

## Find a failure you have seen before

**Role:** any with project access

1. Search for a distinctive part of the error message, the test name or the build number (top-bar search, then the **Tests** or **Runs** scope).
2. Open the test and read its **History** tab.
3. Or ask [Ask AI](/docs/ask-ai) "has this failed before?" — it looks up the test's history and earlier analyses.

**If results look shallow:** global search matches keywords only; try different words from the error. See [Search and discovery](/docs/search).

## Promote a failure to a defect

**Role:** `QA_ENGINEER` or higher

1. Open the run's **Analysis** tab and find the cluster (or **Failures** → the test → **Jira**).
2. Review the severity and the evidence.
3. **File defect** → check the pre-filled fields → **Promote to Defect**. Give a Jira project key to file a ticket as well, or leave it blank for a TestLookup-only defect.

**Expected:** a tracked defect carrying its evidence, on **Failures → Defects**.
**Note:** automatic promotion during the pipeline is off unless enabled. See [Investigating failures](/docs/failure-analysis#promoting-to-a-defect).

## Create and evaluate a release

**Role:** `QA_LEAD` or higher

1. **Releases → New release**: name, version, status, planned date.
2. Get runs into it: send them with the release name (**Release** in the upload form, `--release`, the SDK setting), or **Link Run** on the release's page.
3. Add phases (**Add Phase**) and move each through **In Progress** to **Completed**.
4. Open the **Release gate** for the release's latest run. Read **blocking issues and conditions** — not just the GO / NO_GO word.
5. **Override decision** if you disagree, with a reason; the override is recorded.
6. When every phase is done, **Mark as Released**.

**Expected:** a recommendation with reasoning you can act on or overrule.

## Generate and share a summary report

**Role:** any with project access; `QA_ENGINEER` or higher for the analysis report

1. **Reports → Summary**, pick one project, the window, and **Latest run per suite** or **All runs in window**.
2. **⋯ → Export PDF** or **Export Excel** (large exports run in the background and stay downloadable for 7 days), or **Analysis report (1d / 7d)** for the single-file HTML report.
3. To share one run's decision with people who cannot sign in: **Release gate** for that run → **⋯ → Share report** copies a read-only link that expires after 7 days.
4. **Review the contents before sharing** — reports carry error text and stack traces from your tests.

See [Reports and outputs](/docs/reports).

## Configure an AI provider

**Role:** `ADMIN` to change; a `QA_LEAD` can open the page and read it

1. **Admin → AI → AI configuration** (`/settings/ai`).
2. Under **Analysis Engine**, pick **Auto** or **LLM (AI Agent)**.
3. Under **LLM Provider**, choose the provider and model. Check **Model Availability & Fallback Chain**: every required model should read **Installed**; **Re-check** probes again.
4. A hosted provider's key goes under **Cloud API Keys** (stored encrypted) or in the deployment's secret configuration, never in a ticket. A hosted provider only works with offline mode off.
5. **Save Configuration**, then run one analysis (**⋯ → Trigger pipeline** on a run) and check the routing trail shows the mode you expect.

**Expected:** the routing record shows `llm` rather than a fallback reason.

## Reindex search

**Role:** `QA_LEAD` or higher for one project; `ADMIN` for every project

The search index rebuilds itself every hour, so a reindex is only needed after bulk changes or when results look stale. There is no button for it; call the API:

```bash
curl -X POST -H "X-API-Key: $TESTLOOKUP_API_KEY" \
  "$TESTLOOKUP_URL/api/v1/search/reindex?project_id=<your-project-uuid>"
```

Without `project_id` it reindexes every project, which needs an administrator signed in (not a project-bound key). Reindexing runs in the background; the Search page's **Index freshness** shows when it has caught up.

## Diagnose ingestion or analysis failure

**Role:** `QA_ENGINEER` or higher; an operator for server logs

1. Did the run appear at all? No → ingestion. Yes → analysis.
2. Ingestion: check format support, the file is non-empty, the response was 202, and your role is `QA_ENGINEER` or higher.
3. Analysis: open the run's **Evidence** tab. No pipeline yet? **⋯ → Trigger pipeline**. Is the pipeline marked `partial` or **DEGRADED**? The pipeline ran but did not verify, or a stage fell back.
4. Open **Agent stages** to see which stage fell back and why.

See [Troubleshooting](/docs/troubleshooting).

## Related

- [Getting started](/docs/getting-started)
- [How decisions are made](/docs/decisions)
