# Watching runs live

**Runs → Live** (`/live`, titled **Live Execution**) shows test runs while they are still executing, test by test, as a reporter SDK streams them. Use it to watch a long suite without waiting for the report file at the end.

A run only appears here if it is **streamed**. A report file uploaded after the suite finishes shows up on **Runs → History** instead, already complete. See [Getting results in](/docs/ingestion#streaming-while-tests-run) for the difference.

## What the page shows

**The toolbar**: a window picker for completed sessions (24h, 7d, 14d or 30d; runs that are still active always show), a suite filter, a **LIVE** badge that pulses while a run is streaming, the time of the last update, and the connection status: **Live**, **Connecting…**, **Reconnecting…** or **Error**.

**The status strip** replaces the usual KPI tiles:

- **Current** — how many runs are active and how many tests are in flight. With nothing active it says **No active runs** and when the last one completed.
- How many tests have run across how many sessions.
- **Pass rate** across those sessions, flagged when it falls below the 90% target.
- A pass/fail bar with the passed, failed, broken and skipped counts.

**Sessions** lists each streaming run (and recently completed ones) with its build, suite, status, test count, passes, failures, outcome, release and timing, 25 to a page. **All**, **Running** and **Has failures** filter the list, and **Search runs…** finds one by name. Click a build to open that run's page.

A session marked **idle** has sent nothing for over a minute. It is cleaned up automatically: a reporter that died mid-run does not leave a run "in progress" forever.

**Pipeline events** is a feed of the most recent 200 events (**Run started**, **Test result**, **Run completed**, warnings), filterable to errors or run events only.

## How it updates

The page listens on a live connection for the selected project and also polls every few seconds as a fallback, so it keeps working through a dropped connection. The live connection needs **one project selected**; in **All Projects** the page still lists sessions but updates by polling only.

When a run completes, its session closes, the run is finalised, and the analysis pipeline starts on it, exactly as for an uploaded report.

## Connect a test runner

The collapsed **Connect a test runner** section at the bottom of the page is a quick start for streaming:

1. **All client SDKs** downloads a reporter: **Python (.zip)**, **Java (.jar)**, **JavaScript/TS (.zip)** or **Go (.zip)**.
2. Pick your language tab (**Python**, **Java**, **JavaScript**, **Go**) and follow the steps. The snippets already contain this server's URL and the selected project's id.
3. Run your tests. The run appears in **Sessions** as soon as the first event arrives.

The reporter needs an API key from a user with the `QA_ENGINEER` role or above; see [Getting results in](/docs/ingestion#api-keys). Full SDK setup, including the configuration file and CI usage, is in the repository's CLI, SDKs and MCP guide (`user-guide/cli-sdk-mcp.md`).

## When a live run looks wrong

| Symptom | Likely cause |
|---|---|
| Nothing appears while tests run | The runner uploads a report at the end instead of streaming, or the reporter cannot reach the server or is using a key without access to this project |
| Connection status stays **Reconnecting…** | **All Projects** is selected, or a proxy in front of TestLookup blocks WebSocket connections. The page still updates by polling |
| A session sits at **idle** | The runner stopped sending. It is closed automatically; the run keeps the results that arrived |
| The run's page shows no tests while it is running | Open the run's **Tests** tab and use **Recover from buffer** |

## Related

- [Runs and run detail](/docs/runs)
- [Getting results in](/docs/ingestion)
- [API, CLI, SDKs and MCP](/docs/integrations)
