# TestLookup agent API: curl reference

Generated from the OpenAPI schema by `python -m app.services.agent_api_docs` (run in `backend/`). Do not edit by hand: CI fails when this file and the schema disagree. The same requests are in the Postman collection `agents.postman_collection.json`.

Every request except the event stream authenticates with `Authorization: Bearer $TOKEN` (a JWT). A project API key works too: send `X-API-Key: <key>` instead.

Variables used below:

```bash
export AGENT_ID=agent.summary.v1
export BASE_URL=http://localhost:8000
export INVOCATION_ID=
export PROJECT_ID=
export REVIEW_ID=
export TEST_RUN_ID=
export TICKET=
export TOKEN=
```

## Catalog

### GET /api/v1/agents/catalog

Discover the agents (E1.1): every capability in the registry.

```bash
curl -s -X GET "$BASE_URL/api/v1/agents/catalog" \
  -H "Authorization: Bearer $TOKEN"
```

### GET /api/v1/agents/catalog/{agent_id}

One agent, with its generated input wrapper and JSON Schemas (E1.1).

```bash
curl -s -X GET "$BASE_URL/api/v1/agents/catalog/$AGENT_ID" \
  -H "Authorization: Bearer $TOKEN"
```

## Invocations

### GET /api/v1/agents/invocations/{invocation_id}

Poll one invocation: status, attempts, output and review state, read from its run.

```bash
curl -s -X GET "$BASE_URL/api/v1/agents/invocations/$INVOCATION_ID" \
  -H "Authorization: Bearer $TOKEN"
```

### POST /api/v1/agents/invocations/{invocation_id}/cancel

Cancel an invocation cooperatively, exactly as its pipeline run is cancelled.

```bash
curl -s -X POST "$BASE_URL/api/v1/agents/invocations/$INVOCATION_ID/cancel" \
  -H "Authorization: Bearer $TOKEN"
```

### GET /api/v1/agents/invocations/{invocation_id}/events

Progress of one invocation as server-sent events, until its run finishes.

```bash
curl -sN -X GET "$BASE_URL/api/v1/agents/invocations/$INVOCATION_ID/events?ticket=$TICKET"
```

### POST /api/v1/agents/invocations/{invocation_id}/events/ticket

A single-use ticket for ``GET .../events`` (EventSource cannot send Authorization).

```bash
curl -s -X POST "$BASE_URL/api/v1/agents/invocations/$INVOCATION_ID/events/ticket" \
  -H "Authorization: Bearer $TOKEN"
```

### POST /api/v1/agents/invocations/{invocation_id}/retry

Retry a failed invocation: a new attempt of the same invocation (E1.2).

```bash
curl -s -X POST "$BASE_URL/api/v1/agents/invocations/$INVOCATION_ID/retry" \
  -H "Authorization: Bearer $TOKEN"
```

### POST /api/v1/agents/{agent_id}/invoke

Invoke one agent on a stored test run.

```bash
curl -s -X POST "$BASE_URL/api/v1/agents/$AGENT_ID/invoke" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: $(uuidgen)" \
  --data @- <<EOF
{
  "project_id": "$PROJECT_ID",
  "input": {
    "agent_id": "$AGENT_ID",
    "payload": {
      "test_run_id": "$TEST_RUN_ID"
    }
  },
  "mode": "async",
  "config_overrides": {
    "model": {
      "tier": "slm"
    }
  }
}
EOF
```

## Reviews

### GET /api/v1/projects/{project_id}/reviews

The project's review queue, newest first. ``?state=pending_review`` for the open queue.

Optional query parameters: `state`, `limit`.

```bash
curl -s -X GET "$BASE_URL/api/v1/projects/$PROJECT_ID/reviews" \
  -H "Authorization: Bearer $TOKEN"
```

### GET /api/v1/reviews/{review_id}

Get Review

```bash
curl -s -X GET "$BASE_URL/api/v1/reviews/$REVIEW_ID" \
  -H "Authorization: Bearer $TOKEN"
```

### POST /api/v1/reviews/{review_id}/accept

Accept an AI report. Its run becomes ``passed``.

```bash
curl -s -X POST "$BASE_URL/api/v1/reviews/$REVIEW_ID/accept" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  --data @- <<EOF
{
  "notes": "Checked against the failing test logs."
}
EOF
```

### POST /api/v1/reviews/{review_id}/reject

Reject an AI report with a reason code. Its run becomes ``failed``.

```bash
curl -s -X POST "$BASE_URL/api/v1/reviews/$REVIEW_ID/reject" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  --data @- <<EOF
{
  "reason_code": "unsupported_claim",
  "notes": "The cited stack trace belongs to another test."
}
EOF
```
