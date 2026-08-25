# Troubleshooting

Symptoms first, with the check to run for each.

## Start with the three scopes

Most "missing data" reports are one of these:

1. **Project scope** — is the selector on the right project, or on All Projects?
2. **Time window** — run listings default to **30 days**.
3. **Deleted project** — soft-deleted projects vanish from listings and their runs stop appearing.

Check these before anything else.

## Ingestion

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

**My failures is empty.**
Most auto-assignments go to a synthetic project QA-lead account. Switch the scope toggle to **team**.

## Search

**Similar failures look shallow.**
Semantic search may be unavailable and search may have fallen back to keyword matching. Ask an administrator to check index health.

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
