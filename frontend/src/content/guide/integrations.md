# API, CLI, SDKs and MCP

Programmatic access, and which interface to reach for.

| Interface | Reach for it when |
|---|---|
| **REST API** | Anything scripted; the interface everything else wraps |
| **CLI** | CI steps and terminal work |
| **SDKs** | Sending results from application or test code |
| **MCP server** | Letting an AI client query TestLookup |

## Authentication

Two credentials:

- **API key** — for automation. Header `X-API-Key`. Create your own under **My API keys** (`QA_ENGINEER` or higher), or `POST /api/v1/keys`; an administrator can issue a project-bound streaming key under **Admin → Project → Streaming API keys**. **Shown once** — store it immediately. See [Getting results in](/docs/ingestion#api-keys).
- **Session / bearer token** — for interactive use, from signing in. An API key is never sent as a bearer token; use the `X-API-Key` header.

Ingestion endpoints accept either, so you can try a call from a signed-in browser before wiring CI.

> **Warning.** Never commit a key, paste it into a ticket, or echo it in a build log. Rotate anything that leaks. Keys carry the permissions of the user that created them.

## REST API

The full reference is the OpenAPI/Swagger UI at **`/api-docs`** on your deployment. It is generated from the running code, so it is always current — prefer it over any hand-written endpoint list.

Endpoints you will use first:

```bash
# Send results
POST /api/v1/ingest            # JSON batch
POST /api/v1/ingest/file       # multipart file upload
POST /api/v1/stream/ingest     # streaming / live

# Read back
GET  /api/v1/runs?project_id=<uuid>&size=20&days=30
GET  /api/v1/keys              # manage API keys
```

Conventions worth knowing:

- Ingestion returns **202 Accepted** — queued, not stored.
- Paged lists — runs (`/api/v1/runs`), search (`/api/v1/search`), defects (`/api/v1/analytics/defects`) and the test-management cases, plans and audit log — take `page` and `size` (**not** `limit`) and return `{items, total, page, size, pages}`.
- Other lists are not paged: `/api/v1/releases` returns every match as `{items, total}`, and `/api/v1/projects` and `/api/v1/keys` return a plain array.
- Run listings default to **30 days**; `days=0` means all time.
- Listings exclude runs from deleted projects.

## CLI

A terminal client over the same API. Use it in CI steps where a shell is easier than a language SDK. It needs the same API key and base URL.

The `analytics` command reads the same chart data the pages draw:

```bash
testlookup analytics trends --project <id> --days 30 --by suite -o table
testlookup analytics chart --metric pass_rate --group-by day --group-by release -o csv
```

Output is a table, CSV or JSON. Chart reads are limited to 120 a minute per user; on a 429 the command says so and asks you to retry later.

The CLI is installed from the repository (`pip install ./cli`); it is not published to a package index. Its full command reference, including `upload`, `runs`, `reviews` and the quarantine-aware CI gate `ci-verdict`, is in the repository's CLI README and operator guide (`cli/README.md`, `user-guide/cli-sdk-mcp.md`).

## SDKs

Client libraries live under `client/` in the repository: **Python** (a pytest plugin and a live-streaming reporter), **Java** (a TestNG listener and a JUnit 5 extension), **JavaScript/TypeScript** (Jest and Mocha reporters) and **Go**. Use one when you want to stream results from inside your test run rather than upload a report afterwards; the run then shows on [Runs → Live](/docs/live) as it happens. The Live page's **Connect a test runner** section downloads them, and each SDK's README in the repository covers its setup.

## MCP server

The Model Context Protocol server lets an AI client (Claude Desktop, Claude Code, Cursor and others) query TestLookup as a set of tools — asking about runs, failures, flaky tests and release gates in natural language — and, with the right role, act: propose a quarantine, correct a classification, file a Jira issue. It runs as its own service and speaks stdio and SSE. The list of tools, resources and prompts is in the repository's MCP README (`mcp/README.md`).

Its `get_chart_data` tool returns one chart's data: a metric (executions by default), up to two grouping dimensions, and optional project, releases, suites and window. A point with no data comes back as `y: null` with `measured: false`, never 0, so a client can tell "nothing to measure" from "zero". When rate limited, the tool returns `retry_after`.

> **Important.** An MCP client reaches your test data with the permissions of the account it signs in as: a username and password for a local (stdio) server, or the access token the client presents to a networked (SSE) server. Give each AI client its own account with only the role it needs, and remember that anything it reads may be sent to whatever model backs that client. See [Security and privacy](/docs/security).

## Webhooks and issue trackers

Where configured, TestLookup can notify external systems and file issues. All of it is opt-in, set up by QA leads and administrators under **Admin → Integrations**, and blocked while the deployment's offline mode is on (the default):

- **Jira** — one-click issues from a failure, with duplicate detection and status mirrored back. See [Investigating failures](/docs/failure-analysis#promoting-to-a-defect) and [Settings and administration](/docs/administration#jira-splunk-openshift-slack-and-teams).
- **GitHub and GitLab** — a sticky pull-request or merge-request comment and a check run or commit status per run. Off by default (`github_checks` and `gitlab` feature flags). See [Settings and administration](/docs/administration#github-and-gitlab).
- **Outbound webhooks** — signed events (`run.completed`, `defect.promoted`, `defect.create_requested`, `release.decided`, `flaky.quarantined`, `quota.exceeded`) to your own endpoints. Off by default (`outbound_webhooks` feature flag). `defect.promoted` fires once per defect when a defect promoted from a failure cluster takes effect: at promotion when it needs no approval, or when a QA lead approves it in [Inbox › Approvals](/docs/inbox#defects). See [Settings and administration](/docs/administration#outbound-webhooks).
- **Slack, Teams and email** — notifications and digests. See [Settings and administration](/docs/administration#notifications-and-webhooks).

> **Note.** Jira ticket creation requires Jira to be explicitly enabled. It is off by default, and a defect can exist in TestLookup without any external ticket.

## Prometheus

The **Prometheus** card under **Admin → Integrations** points the deep investigation's **Log intelligence** stage at the application metrics of the system you test (not TestLookup's own monitoring). An administrator saves **Enabled**, a **Base URL** and, optionally, a **Bearer token** (stored encrypted and shown only as **(set)**, or **(set — Vault)** for a Vault reference); QA leads can read the card and press **Test connection**, which probes the **saved** settings.

- **Metric templates.** Up to 12 PromQL templates, each with an id, a label and a unit, and `{service}` where the service name goes. Four defaults (p95 latency, 5xx error rate, memory working set, CPU usage) apply until you save your own; **Use the defaults** returns to them. The stage only ever asks for these templates by id, never with PromQL of its own, and a service name is checked and escaped before it is substituted.
- **What you see.** For the first few failure clusters that name a service and a failure time, the run's **Evidence** tab shows, under **Specialist evidence → Log intelligence**, a sparkline per metric and a sentence such as "p95 latency for checkout rose 3.1× in the failure window". A JSON ingest result may carry `service_name` so the stage knows which service failed.
- **When it cannot ask, it says so.** "Prometheus lookup disabled: offline mode" (offline mode refuses every query), "metrics unavailable: Prometheus is not configured", "… Prometheus timed out", "… Prometheus refused the token (HTTP 401)". Not configured never reads as healthy, and a timeout never reads as "no data".

The connection also has its own row on [Settings and administration](/docs/administration#integration-health) › Integration health. `GET` / `PUT /api/v1/settings/integrations` carry the `prometheus_*` fields (the token is never returned: `prometheus_token_set`), and `POST /api/v1/integration-health/probe?provider=prometheus` probes it.

## Errors

| Status | Means | Do |
|---|---|---|
| 202 | Accepted for processing | Verify it landed |
| 400 | Malformed or empty payload | Check the file/body is non-empty and valid |
| 401 | Bad or missing credential | Check the `X-API-Key` header |
| 403 | Authenticated, not authorised | Check project membership and role |
| 404 | Not found or not visible to you | Check the id and project scope |
| 422 | Validation failed | Read the response body; it names the field |
| 429 | Too many requests | Wait the `Retry-After` seconds, then retry |

## Related

- [Getting results in](/docs/ingestion)
- [Administration](/docs/administration)
