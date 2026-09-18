# API guide

[Documentation home](../README.md)

The declared HTTP contract is indexed by the [endpoint index](../reference/api-index.md), its 81 domain pages, [schemas](../reference/schemas.md), and [OpenAPI JSON](../reference/openapi.json). This snapshot contains 550 HTTP operations across 460 paths. Swagger is `/api-docs`, ReDoc `/api-docs/redoc`, and the runtime schema `/api-docs/openapi.json`. `/docs` belongs to the frontend user guide.

Source annotations can overstate implemented behavior: the [summary-report release filter and basis limitations](../pipelines/reporting.md#aggregation-and-evidence) are confirmed examples. Generated descriptions are preserved declarations, not a runtime certification.

## Authentication and scope

Normal protected routes accept a JWT bearer or API key through registration-level dependencies; individual handlers can restrict credential kind/role. Use `Authorization: Bearer <access_token>` or `X-API-Key: <key>` as supported by the route. Login uses OAuth2 form fields `username` and `password`, not a JSON login body. Inspect all HTTP 200 login shapes: MFA challenge/required enrollment is not completed authentication.

Every project-scoped action must satisfy membership, role and key binding. Do not assume that possession of another run UUID grants access. API key scope lists and human-only review actions add restrictions. Webhooks, SCIM, shared reports, SDK downloads, health, WebSocket and stream-ticket routes have distinct auth contracts. The generated dependency chain supplements OpenAPI because middleware/manual header auth is not fully represented by `security` declarations. [Security guide](../architecture/security.md).

## Working examples

The commands below are Bash examples using placeholder values. Use an existing authorized project and credentials. In PowerShell use `curl.exe` with appropriate quoting, or run them in Git Bash/WSL.

```bash
export TL_URL='http://localhost:8000'
export TL_PROJECT='00000000-0000-0000-0000-000000000001'
export TL_API_KEY='replace-with-your-project-key'

curl --fail-with-body "$TL_URL/api/v1/projects" \
  -H "X-API-Key: $TL_API_KEY"

curl --fail-with-body "$TL_URL/api/v1/ingest" \
  -H "X-API-Key: $TL_API_KEY" -H 'Content-Type: application/json' \
  --data-binary @- <<JSON
{"project_id":"$TL_PROJECT","build_number":"docs-example-1","framework":"pytest","environment":"staging","results":[{"test_name":"test_checkout","status":"FAILED","duration_ms":120,"error_message":"Expected 200, received 500"}]}
JSON

curl --fail-with-body "$TL_URL/api/v1/ingest/file" \
  -H "X-API-Key: $TL_API_KEY" \
  -F "project_id=$TL_PROJECT" -F 'build_number=docs-example-2' \
  -F 'format=junit' -F 'file=@results.xml'
```

Accepted ingestion response shape (IDs below are illustrative):

```json
{"status":"accepted","run_id":"00000000-0000-0000-0000-000000000002","task_id":"00000000-0000-0000-0000-000000000003","total_results":1}
```

`total_results` at acceptance is not proof of durable accepted rows. For file uploads follow the returned task ID; JSON batch ingestion has a separate worker path. A just-accepted run may not yet exist for a read.

```bash
curl --fail-with-body "$TL_URL/api/v1/ingest/uploads/$TL_TASK_ID" \
  -H "X-API-Key: $TL_API_KEY"
curl --fail-with-body "$TL_URL/api/v1/runs/$TL_RUN_ID" \
  -H "X-API-Key: $TL_API_KEY"

# Human session login: inspect MFA/challenge fields before using the response.
curl --fail-with-body "$TL_URL/api/v1/auth/login" \
  --data-urlencode "username=$TL_USERNAME" \
  --data-urlencode "password=$TL_PASSWORD"
```

Use the [Reports domain](../reference/api/reports.md) for PDF/evidence/share requests and [Agent Invocations](../reference/api/agent-invocations.md) for exact invocation/subject/idempotency/sync parameters. Do not hard-code capability IDs from a diagram; query the catalog. Binary/streamed exports may not have a complete JSON response model.

## Errors and edge cases

| Status | Common meaning; endpoint details take precedence |
|---|---|
| 400 | Invalid format/empty file/malformed domain operation |
| 401 | Missing/invalid/expired credential, rejected auth/MFA token |
| 403 | Role/project/key/policy refusal |
| 404 | Missing or inaccessible entity, expired upload status, unknown resource |
| 409 | Conflicting state/version/duplicate or report distribution refusal |
| 413 | Upload/form/archive size restriction |
| 422 | FastAPI/Pydantic validation or explicitly rejected typed payload |
| 429 | Rate/lockout limit; honor `Retry-After` |
| 503 | Admission/storage/provider availability or feature-disabled operation |

FastAPI errors commonly use `{"detail": ...}`; `detail` may be a string, object or validation list. SCIM uses its own error format and binary/SSE responses have different bodies. The domain pages include direct handler `HTTPException` branches, but service/dependency/middleware errors can add outcomes. [Unstructured contract inventory](../reference/api-contract-gaps.md) marks endpoints where OpenAPI cannot fully document emitted fields.

Pagination/filter names and maximum limits are endpoint-specific; read their parameter schemas. Repeated query arrays use `?field=a&field=b`, not Axios-style brackets. Time ranges, release filters and environment semantics affect denominators. Retrying an arbitrary POST is unsafe unless the endpoint supplies an idempotency contract; ingestion identity is not a global API idempotency key.

## Streaming and operational routes

`/ws/live/{project_id}` expects the first client frame `{"type":"auth","token":"<JWT>"}`. Optional `last_event_id` uses Redis stream ID syntax (`digits-digits`). Malformed authentication closes with 4400; timeout/failed authentication can close with 4401, and membership validation applies. Live-session SSE at `/api/v1/stream/sse/{project_id}` uses a `token` query parameter and optional `Last-Event-ID` header; it is distinct from the invocation ticket protocol. `/ws/events/{run_id}` is an authenticated producer POST despite the prefix. Stream APIs handle sessions/events and SSE. Invocation SSE requires a ticket created with normal authorization; tickets are short-lived and single-use, so reconnect flows need fresh authority. [Non-OpenAPI routes](../reference/api-contract-gaps.md).

`/health/live` checks the process, `/health/ready` checks critical dependencies, `/health/version` reports build identity, and `/health/details` reports dependency details while potentially returning 200 degraded. `/metrics` is conditional on metrics configuration and excluded from the exported OpenAPI. Health success alone does not prove worker delivery, correct model installation or migration compatibility.
