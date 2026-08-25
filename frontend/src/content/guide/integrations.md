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

- **API key** — for automation. Header `X-API-Key`. Create at **Settings → API keys** or `POST /api/v1/keys`. **Shown once** — store it immediately.
- **Session / bearer token** — for interactive use, from signing in.

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
- List endpoints page with `page` and `size` (**not** `limit`) and return `{items, total, page, size, pages}`.
- Run listings default to **30 days**; `days=0` means all time.
- Listings exclude runs from deleted projects.

## CLI

A terminal client over the same API. Use it in CI steps where a shell is easier than a language SDK. It needs the same API key and base URL.

## SDKs

Client libraries live under `client/` in the repository. Use one when you want to send results from inside your test code rather than shelling out to curl.

## MCP server

The Model Context Protocol server lets an AI client query TestLookup as a tool — asking about runs and failures in natural language. It runs as its own service and speaks stdio and SSE.

> **Important.** An MCP client reaches your test data. Give it a credential scoped to what it should see, and remember that anything it reads may be sent to whatever model backs that client. See [Security and privacy](/docs/security).

## Webhooks and issue trackers

Where configured, TestLookup can notify external systems and file issues. Both are opt-in and administrative — see [Administration](/docs/administration).

> **Note.** Jira ticket creation requires Jira to be explicitly enabled. It is off by default, and a defect can exist in TestLookup without any external ticket.

## Errors

| Status | Means | Do |
|---|---|---|
| 202 | Accepted for processing | Verify it landed |
| 400 | Malformed or empty payload | Check the file/body is non-empty and valid |
| 401 | Bad or missing credential | Check the `X-API-Key` header |
| 403 | Authenticated, not authorised | Check project membership and role |
| 404 | Not found or not visible to you | Check the id and project scope |
| 422 | Validation failed | Read the response body; it names the field |

## Related

- [Getting results in](/docs/ingestion)
- [Administration](/docs/administration)
