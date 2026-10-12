# Troubleshooting

Symptoms first, with the check to run for each.

## Start with the three scopes

Most "missing data" reports are one of these:

1. **Project scope** — is the selector on the right project, or on All Projects?
2. **Time window** — run listings default to **30 days**.
3. **Deleted project** — soft-deleted projects vanish from listings and their runs stop appearing.

Check these before anything else.

## Ingestion

**There is no Upload report button on Runs.**
An administrator has turned the `manual_upload` feature flag off. Send results through the API, CLI or an SDK, or ask for the flag to be turned back on. If the button is there but greyed out, your role is below `QA_ENGINEER`.

**Upload or API call answers 403 "Requires at least QA_ENGINEER role".**
Sending results needs the `QA_ENGINEER` role in that project, and an API key acts with its owner's role.

**Upload returns 202 but no run appears.**
Still queued, or the report parsed to zero results. Wait and refresh; confirm the report actually contains test cases; confirm the format is supported.

**Upload rejected as empty (400).**
A zero-byte file — usually a wrong path in `-F file=@…`. Check it locally with `ls -l`.

**Run appears with no tests.**
The format was detected but no cases were found. Open the report; a summary-only file has nothing to ingest.

**Wrong format detected.**
Detection reads content markers. A file missing its distinctive marker falls through to the JUnit default. Send with an explicit format or convert to JUnit XML.

**Suite names look wrong.**
Suite comes from the report where present and is inferred otherwise; some frameworks declare it only at session level. See [Getting results in](/docs/ingestion).

## Analysis

**No analysis on a run.**
Analysis runs after finalisation on its own queue. Check the run finalised; check whether analysis is queued rather than failed.

**Run is marked `partial`.**
The pipeline ran but its **verification did not pass**, so the decision report was withheld. This is deliberate and visible. Check the stage records for which stage fell back and why.

**Explanations are generic.**
Likely no AI provider configured, so analysis fell back to rules. Check the routing record — it names the resolved mode and the reason.

**An agent timed out.**
Its declared fallback applies and is recorded. The run continues; the stage is marked as having fallen back.

## Flaky classification

**A test is flagged that is not flaky.**
Check the confidence band first — below 5 observations there is no score at all, and a `low` band is weak evidence. Look at the raw history: flipping, or consistently failing? Check whether environment disagreement is driving it.

**A known flaky test is not flagged.**
It may not have reached 5 observations, or your framework may retry internally and report only the final result — hiding the flips.

## Dashboards

**Everything is empty.**
Project scope or time window. See the top of this page.

**Trends are missing.**
Not enough history. Trends need runs over time.

**The Inbox is empty.**
Failures are assigned to suite owners first and then spread across the project's QA leads, often onto a synthetic default QA-lead account. QA leads and administrators: switch **Scope** to **team**. Everyone else: your suites may have another owner; see [Your inbox and approvals](/docs/inbox#how-failures-get-assigned).

**A chart says "Waiting to retry".**
Too many chart requests in a minute, usually many tabs or a script on the same account. The chart retries by itself; if it gives up, press **Retry**.

**A chart shows "—" or "Not measured".**
There was nothing to measure for that point, for example a day with only skipped tests. It is not zero. See [Charts and visual analysis](/docs/charts).

**"View in 3D" shows the 2D chart with a note.**
The browser does not offer WebGL 2, or graphics acceleration is off. The 2D chart has the same points.

## AI features

**Ask AI is missing from the sidebar.**
Ask AI needs analysis in **Auto** or **LLM** mode and the `ask_ai_chat` flag on. In **Rules** or **ML** mode it is hidden; an administrator changes the mode under **Admin → AI → AI configuration**. See [Ask AI](/docs/ask-ai#when-ask-ai-is-available).

**Ask AI says the project's AI budget for the month is used up.**
The project reached its LLM cost budget. An administrator can raise it under **Admin → AI → LLM cost budget**.

**Root cause (AI) refuses to run.**
Deep investigation needs the `QA_ENGINEER` role and an analysis mode other than **Rules**.

**An export or shared link is refused because the report is pending review.**
Your administrator has made human review of AI reports mandatory. A QA lead accepts the report under **Inbox → Approvals**, then try again.

## Workflow pages that say they are off

**"Quarantine is off for this project".**
Quarantine proposals need the `flaky_auto_quarantine` feature flag, which ships off. An administrator turns it on under **Settings → System → Feature flags**.

**Outbound webhook "Test" says no delivery was enqueued.**
Outbound webhooks need the `outbound_webhooks` flag on and offline mode off.

**"Release compliance pack feature is disabled".**
Compliance packs need the `release_compliance_pack` flag.

**"Failed to update phase" on a release.**
Release phase enforcement (`release_phase_gate_enforcement`) is on, and the phase's gate is not GO. See [Releases and gates](/docs/releases#phases).

## Search

**Search does not find a failure I know exists.**
Global search matches keywords, not meaning. Search for a distinctive part of the error message, the test name or the build number. See [Search and discovery](/docs/search).

**Recently ingested data is not searchable.**
The run list reads the database; search reads the index. Indexing lags slightly.

## Permission and access problems

**403 on something you expect to see.**
Project membership, not just role. Membership is the isolation boundary.

**401 from the API.**
Missing or expired credential. The header is `X-API-Key`.

**A feature flag seems to do nothing.**
`enabled_global` is the master switch; `enabled_projects` only *narrows* an enabled flag, and `rollout_percent` must be non-zero. Setting only the project list leaves it off everywhere.

## Reports

**No decision report.**
If the run is `partial`, the report was withheld because verification failed. That is the intended behaviour.

**A report looks stale.**
Reports record a moment. Re-running analysis creates a new one rather than updating the old.

## Related

- [Getting started](/docs/getting-started)
- [Administration](/docs/administration)
