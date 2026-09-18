# Agent Invocations API

[Documentation home](../../README.md) · [Regeneration](../../handoff/maintenance.md)

Generated from tracked source by `scripts/generate_handoff_reference.py`. Do not edit by hand. The baseline and validation limits are recorded in [verification](../../handoff/verification.md).

## GET `/api/v1/agents/invocations/{invocation_id}`

Get Invocation

Poll one invocation: status, attempts, output and review state, read from its run.

Source: [backend/app/routers/agent_invoke.py:581](../../../backend/app/routers/agent_invoke.py#L581).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_invocation_access.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
invocation_id: uuid.UUID, db: AsyncSession=Depends(get_db), _: User=Depends(require_invocation_access())
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "get_invocation_api_v1_agents_invocations__invocation_id__get",
  "parameters": [
    {
      "in": "path",
      "name": "invocation_id",
      "required": true,
      "schema": {
        "format": "uuid",
        "title": "Invocation Id",
        "type": "string"
      }
    },
    {
      "in": "header",
      "name": "X-API-Key",
      "required": false,
      "schema": {
        "anyOf": [
          {
            "type": "string"
          },
          {
            "type": "null"
          }
        ],
        "title": "X-Api-Key"
      }
    }
  ],
  "responses": {
    "200": {
      "content": {
        "application/json": {
          "schema": {
            "$ref": "#/components/schemas/AgentInvocationResponse"
          }
        }
      },
      "description": "Successful Response"
    },
    "422": {
      "content": {
        "application/json": {
          "schema": {
            "$ref": "#/components/schemas/HTTPValidationError"
          }
        }
      },
      "description": "Validation Error"
    }
  },
  "security": [
    {
      "JWT": []
    }
  ]
}
```

## POST `/api/v1/agents/invocations/{invocation_id}/cancel`

Cancel Invocation

Cancel an invocation cooperatively, exactly as its pipeline run is cancelled.

A run with no live worker stops now; a running one is asked to stop and
terminalises at its next stage boundary. Cancellation is sticky: an
automatic retry never brings it back. Cancellation before the worker
creates a run is stored on the invocation and rechecked under a row lock.

Source: [backend/app/routers/agent_invoke.py:798](../../../backend/app/routers/agent_invoke.py#L798).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_invocation_access.<locals>._check`, `require_role.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
invocation_id: uuid.UUID, db: AsyncSession=Depends(get_db), current_user: User=Depends(require_role(UserRole.QA_ENGINEER)), _: User=Depends(require_invocation_access())
```

Direct handler error branches (dependency/service errors can add others):

```python
HTTPException(status_code=409, detail={**base, 'message': 'Invocation has already been cancelled'})
HTTPException(status_code=409, detail={**base, 'message': 'Invocation has already finished', **outcome.as_dict()})
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "cancel_invocation_api_v1_agents_invocations__invocation_id__cancel_post",
  "parameters": [
    {
      "in": "path",
      "name": "invocation_id",
      "required": true,
      "schema": {
        "format": "uuid",
        "title": "Invocation Id",
        "type": "string"
      }
    },
    {
      "in": "header",
      "name": "X-API-Key",
      "required": false,
      "schema": {
        "anyOf": [
          {
            "type": "string"
          },
          {
            "type": "null"
          }
        ],
        "title": "X-Api-Key"
      }
    }
  ],
  "responses": {
    "202": {
      "content": {
        "application/json": {
          "schema": {
            "$ref": "#/components/schemas/AgentInvocationResponse"
          }
        }
      },
      "description": "Successful Response"
    },
    "422": {
      "content": {
        "application/json": {
          "schema": {
            "$ref": "#/components/schemas/HTTPValidationError"
          }
        }
      },
      "description": "Validation Error"
    }
  },
  "security": [
    {
      "JWT": []
    }
  ]
}
```

## GET `/api/v1/agents/invocations/{invocation_id}/events`

Stream Invocation Events

Progress of one invocation as server-sent events, until its run finishes.

Source: [backend/app/routers/agent_invoke.py:612](../../../backend/app/routers/agent_invoke.py#L612).

Dependency chain: `require_invocation_stream_ticket.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
invocation_id: uuid.UUID, request: Request, _: uuid.UUID=Depends(require_invocation_stream_ticket())
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "stream_invocation_events_api_v1_agents_invocations__invocation_id__events_get",
  "parameters": [
    {
      "in": "path",
      "name": "invocation_id",
      "required": true,
      "schema": {
        "format": "uuid",
        "title": "Invocation Id",
        "type": "string"
      }
    },
    {
      "in": "query",
      "name": "ticket",
      "required": true,
      "schema": {
        "maxLength": 128,
        "minLength": 16,
        "title": "Ticket",
        "type": "string"
      }
    }
  ],
  "responses": {
    "200": {
      "content": {
        "application/json": {
          "schema": {}
        }
      },
      "description": "Successful Response"
    },
    "422": {
      "content": {
        "application/json": {
          "schema": {
            "$ref": "#/components/schemas/HTTPValidationError"
          }
        }
      },
      "description": "Validation Error"
    }
  }
}
```

Handler return expressions (source excerpts, not an inferred wire schema):

```python
StreamingResponse(invocation_event_stream(invocation_id, request), media_type='text/event-stream', headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no', 'Connection': 'keep-alive'})
```

Contract limit: at least one response has an unstructured schema. Read the linked handler/serializer for emitted fields; the empty schema is not a promise of an empty JSON object.

## POST `/api/v1/agents/invocations/{invocation_id}/events/ticket`

Issue Invocation Stream Ticket

A single-use ticket for ``GET .../events`` (EventSource cannot send Authorization).

Source: [backend/app/routers/agent_invoke.py:593](../../../backend/app/routers/agent_invoke.py#L593).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_invocation_access.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
invocation_id: uuid.UUID, db: AsyncSession=Depends(get_db), current_user: User=Depends(require_invocation_access())
```

Direct handler error branches (dependency/service errors can add others):

```python
HTTPException(status_code=503, detail='Stream tickets are unavailable; poll links.self instead')
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "issue_invocation_stream_ticket_api_v1_agents_invocations__invocation_id__events_ticket_post",
  "parameters": [
    {
      "in": "path",
      "name": "invocation_id",
      "required": true,
      "schema": {
        "format": "uuid",
        "title": "Invocation Id",
        "type": "string"
      }
    },
    {
      "in": "header",
      "name": "X-API-Key",
      "required": false,
      "schema": {
        "anyOf": [
          {
            "type": "string"
          },
          {
            "type": "null"
          }
        ],
        "title": "X-Api-Key"
      }
    }
  ],
  "responses": {
    "200": {
      "content": {
        "application/json": {
          "schema": {
            "$ref": "#/components/schemas/StreamTicketResponse"
          }
        }
      },
      "description": "Successful Response"
    },
    "422": {
      "content": {
        "application/json": {
          "schema": {
            "$ref": "#/components/schemas/HTTPValidationError"
          }
        }
      },
      "description": "Validation Error"
    }
  },
  "security": [
    {
      "JWT": []
    }
  ]
}
```

## POST `/api/v1/agents/invocations/{invocation_id}/retry`

Retry Invocation

Retry a failed invocation: a new attempt of the same invocation (E1.2).

The pipeline retry rules apply to the invocation's run. It is refused with
409 while the run is in progress, after a clean finish or review rejection,
or at the attempt ceiling; the last three carry ``links.rerun`` to invoke
the agent again. Review rejection is terminal even if configuration changed.

A retry resumes the SAME run, so the frozen plan (this agent and its
dependencies only) is kept. When the agent configuration changed since the
run started, resuming would replay work authorised under the old
configuration, and a rerun needs a new run -- which is a new invocation --
so that is 409 with ``links.rerun`` too. An invocation whose run never
started is dispatched again.

Source: [backend/app/routers/agent_invoke.py:626](../../../backend/app/routers/agent_invoke.py#L626).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_invocation_access.<locals>._check`, `require_role.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
invocation_id: uuid.UUID, db: AsyncSession=Depends(get_db), current_user: User=Depends(require_role(UserRole.QA_ENGINEER)), _: User=Depends(require_invocation_access())
```

Direct handler error branches (dependency/service errors can add others):

```python
HTTPException(status_code=409, detail={**base, 'message': 'Invocation finished successfully; nothing to retry', 'status': public_status(current), 'links': rerun})
HTTPException(status_code=409, detail={**base, 'message': 'Invocation has not started yet'})
HTTPException(status_code=409, detail={**base, 'message': 'Invocation is still in progress', 'status': public_status(current)})
HTTPException(status_code=409, detail={**base, 'message': 'Invocation was cancelled; invoke the agent again instead', 'reason': 'cancelled', 'status': 'failed', 'links': rerun})
HTTPException(status_code=409, detail={**base, 'message': 'Invocation was cancelled; invoke the agent again instead', 'reason': 'cancelled', 'status': public_status(current), 'links': rerun})
HTTPException(status_code=409, detail={**base, 'message': 'Invocation was rejected in review; invoke the agent again instead', 'reason': 'review_rejected', 'status': public_status(current), 'links': rerun})
HTTPException(status_code=409, detail={**base, 'message': 'The agent configuration changed since this invocation ran; invoke the agent again to run under the current configuration', 'reason': 'agent_config_changed', 'links': rerun})
HTTPException(status_code=409, detail={**base, 'message': 'The agent configuration changed since this invocation ran; invoke the agent again to run under the current configuration', 'reason': plan.reason, 'links': rerun})
HTTPException(status_code=409, detail={**base, 'message': f'Invocation reached its attempt ceiling ({attempt}/{max_attempts}); invoke the agent again instead', 'attempt': attempt, 'max_attempts': max_attempts, 'links': rerun})
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "retry_invocation_api_v1_agents_invocations__invocation_id__retry_post",
  "parameters": [
    {
      "in": "path",
      "name": "invocation_id",
      "required": true,
      "schema": {
        "format": "uuid",
        "title": "Invocation Id",
        "type": "string"
      }
    },
    {
      "in": "header",
      "name": "X-API-Key",
      "required": false,
      "schema": {
        "anyOf": [
          {
            "type": "string"
          },
          {
            "type": "null"
          }
        ],
        "title": "X-Api-Key"
      }
    }
  ],
  "responses": {
    "202": {
      "content": {
        "application/json": {
          "schema": {
            "$ref": "#/components/schemas/AgentInvocationResponse"
          }
        }
      },
      "description": "Successful Response"
    },
    "422": {
      "content": {
        "application/json": {
          "schema": {
            "$ref": "#/components/schemas/HTTPValidationError"
          }
        }
      },
      "description": "Validation Error"
    }
  },
  "security": [
    {
      "JWT": []
    }
  ]
}
```

## POST `/api/v1/agents/{agent_id}/invoke`

Invoke Agent

Invoke one agent on a stored test run.

``mode=async`` (the default) answers 202 with a poll link. ``mode=sync`` on a
sync-eligible agent waits up to ``AGENT_INVOKE_SYNC_WAIT_SECONDS`` for the run
to finish: 200 with the result if it did, 202 if it is still going. A full
pool of sync slots is 503 with ``Retry-After``. ``mode=sync`` on any other
agent runs async.

The project is derived from the test run; ``project_id`` in the body is an
assertion that must match it. An invocation of the same agent on the same
run that is still in progress is returned as-is with 200. With an
``Idempotency-Key``, a repeated request returns the invocation it created
(E1.3).

Source: [backend/app/routers/agent_invoke.py:897](../../../backend/app/routers/agent_invoke.py#L897).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_role.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
agent_id: str, body: AgentInvokeRequest, response: Response, db: AsyncSession=Depends(get_db), current_user: User=Depends(require_role(UserRole.QA_ENGINEER)), idempotency_key: Annotated[Optional[str], Header(alias='Idempotency-Key', min_length=8, max_length=128, pattern='^[A-Za-z0-9._:-]+$', description='Client-generated key (UUID or ULID). The same key with the same request returns the invocation it created (200); with a different request, 422; while the first request is still being handled, 409. Scoped to your user, project, and agent route.')]=None
```

Direct handler error branches (dependency/service errors can add others):

```python
HTTPException(status_code=400, detail="project_id does not match the test run's project")
HTTPException(status_code=403, detail=refusal)
HTTPException(status_code=404, detail='TestRun not found')
HTTPException(status_code=409, detail='A request with this Idempotency-Key is still being handled; retry shortly', headers={'Retry-After': '1'})
HTTPException(status_code=409, detail={'message': 'This agent is already running on the test run under a different request; retry this Idempotency-Key after it finishes', 'reason': 'agent_already_running', 'invocation_id': str(existing['id']), 'links': existing.get('links', {})}, headers={'Retry-After': '1'})
HTTPException(status_code=409, detail={'message': f'The stored configuration of {agent_id} no longer validates. Fix it with PUT /api/v1/projects/{run.project_id}/agent-configs/{agent_id}', 'errors': exc.errors})
HTTPException(status_code=422, detail=_KEY_REUSED)
HTTPException(status_code=422, detail={'message': 'config_overrides may only tighten the project agent configuration', 'errors': exc.reasons})
HTTPException(status_code=503, detail='All synchronous invocation slots are busy; retry shortly or invoke with mode=async', headers={'Retry-After': '2'})
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "invoke_agent_api_v1_agents__agent_id__invoke_post",
  "parameters": [
    {
      "in": "path",
      "name": "agent_id",
      "required": true,
      "schema": {
        "title": "Agent Id",
        "type": "string"
      }
    },
    {
      "description": "Client-generated key (UUID or ULID). The same key with the same request returns the invocation it created (200); with a different request, 422; while the first request is still being handled, 409. Scoped to your user, project, and agent route.",
      "in": "header",
      "name": "Idempotency-Key",
      "required": false,
      "schema": {
        "anyOf": [
          {
            "maxLength": 128,
            "minLength": 8,
            "pattern": "^[A-Za-z0-9._:-]+$",
            "type": "string"
          },
          {
            "type": "null"
          }
        ],
        "description": "Client-generated key (UUID or ULID). The same key with the same request returns the invocation it created (200); with a different request, 422; while the first request is still being handled, 409. Scoped to your user, project, and agent route.",
        "title": "Idempotency-Key"
      }
    },
    {
      "in": "header",
      "name": "X-API-Key",
      "required": false,
      "schema": {
        "anyOf": [
          {
            "type": "string"
          },
          {
            "type": "null"
          }
        ],
        "title": "X-Api-Key"
      }
    }
  ],
  "requestBody": {
    "content": {
      "application/json": {
        "schema": {
          "$ref": "#/components/schemas/AgentInvokeRequest"
        }
      }
    },
    "required": true
  },
  "responses": {
    "200": {
      "content": {
        "application/json": {
          "schema": {
            "$ref": "#/components/schemas/AgentInvocationResponse"
          }
        }
      },
      "description": "Completed sync result or idempotent replay"
    },
    "202": {
      "content": {
        "application/json": {
          "schema": {
            "$ref": "#/components/schemas/AgentInvocationResponse"
          }
        }
      },
      "description": "Successful Response"
    },
    "409": {
      "description": "Invocation conflict or request still in flight"
    },
    "422": {
      "description": "Invalid input or Idempotency-Key reuse"
    },
    "503": {
      "description": "Synchronous invocation capacity is full; Retry-After is returned",
      "headers": {
        "Retry-After": {
          "description": "Seconds before retrying the request",
          "schema": {
            "type": "integer"
          }
        }
      }
    }
  },
  "security": [
    {
      "JWT": []
    }
  ]
}
```
