# Agent Operations API

[Documentation home](../../README.md) · [Regeneration](../../handoff/maintenance.md)

Generated from tracked source by `scripts/generate_handoff_reference.py`. Do not edit by hand. The baseline and validation limits are recorded in [verification](../../handoff/verification.md).

## GET `/api/v1/agents/active-runs`

Get Active Live Runs

Get all currently monitored live test runs.

Enriches each Redis-state row with ``run_seq`` (per-(project,
primary_suite_name) human-readable run number) by resolving each
run's canonical TestRun.id. ``run_seq`` is null when the run's
TestRun row doesn't exist yet — the Phase 4.5 incremental drain
creates it within ~30s of the first event, so very-new active
sessions fall through to the SDK ``build_number`` on the UI.

Tenant-scoped: a non-ADMIN caller only sees live runs whose project
they can access. ``RedisLiveRunState`` stores ``project_id`` on every
state row, so the filter is in-memory (no DB round trip). Without this,
the endpoint leaked every tenant's live run (slug, build number, counts,
timing) to any authenticated user — the ``/active-runs/{run_id}`` sibling
already gates on ``require_run_access`` but the list did not.

Source: [backend/app/routers/agents.py:1084](../../../backend/app/routers/agents.py#L1084).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`.

Declared Python handler arguments (includes exact role/guard options):

```python
db: AsyncSession=Depends(get_db), current_user: Any=Depends(get_current_active_user)
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "get_active_live_runs_api_v1_agents_active_runs_get",
  "parameters": [
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
  },
  "security": [
    {
      "JWT": []
    }
  ]
}
```

Handler return expressions (source excerpts, not an inferred wire schema):

```python
{'active_runs': active}
```

Contract limit: at least one response has an unstructured schema. Read the linked handler/serializer for emitted fields; the empty schema is not a promise of an empty JSON object.

## GET `/api/v1/agents/active-runs/{run_id}`

Get Live Run State

Get the current state for a single live test run.

Source: [backend/app/routers/agents.py:1140](../../../backend/app/routers/agents.py#L1140).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_run_access.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
run_id: str, _: Any=Depends(require_run_access())
```

Direct handler error branches (dependency/service errors can add others):

```python
HTTPException(404, detail='Live run not found or already completed')
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "get_live_run_state_api_v1_agents_active_runs__run_id__get",
  "parameters": [
    {
      "in": "path",
      "name": "run_id",
      "required": true,
      "schema": {
        "title": "Run Id",
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
  },
  "security": [
    {
      "JWT": []
    }
  ]
}
```

Handler return expressions (source excerpts, not an inferred wire schema):

```python
state
```

Contract limit: at least one response has an unstructured schema. Read the linked handler/serializer for emitted fields; the empty schema is not a promise of an empty JSON object.

## GET `/api/v1/agents/catalog`

List Agent Catalog

Discover the agents (E1.1): every capability in the registry.

Clients read agent ids here instead of hard-coding stage names. Declared
first in this router: the planned ``/agents/{agent_id}/invoke`` route must
come after every literal ``/agents/...`` path (architecture section 3.5).

Source: [backend/app/routers/agents.py:196](../../../backend/app/routers/agents.py#L196).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`.

Declared Python handler arguments (includes exact role/guard options):

```python
_: Any=Depends(get_current_active_user)
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "list_agent_catalog_api_v1_agents_catalog_get",
  "parameters": [
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
            "items": {
              "$ref": "#/components/schemas/AgentCatalogEntry"
            },
            "title": "Response List Agent Catalog Api V1 Agents Catalog Get",
            "type": "array"
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

## GET `/api/v1/agents/catalog/{agent_id}`

Get Agent Catalog Entry

One agent, with its generated input wrapper and JSON Schemas (E1.1).

Source: [backend/app/routers/agents.py:209](../../../backend/app/routers/agents.py#L209).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`.

Declared Python handler arguments (includes exact role/guard options):

```python
agent_id: str, _: Any=Depends(get_current_active_user)
```

Direct handler error branches (dependency/service errors can add others):

```python
HTTPException(status_code=404, detail='Unknown agent')
HTTPException(status_code=422, detail='agent_id must look like agent.<name>.v<version>, for example agent.summary.v1')
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "get_agent_catalog_entry_api_v1_agents_catalog__agent_id__get",
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
            "$ref": "#/components/schemas/AgentCatalogDetail"
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

## POST `/api/v1/agents/defect-command`

Defect Command

Run the Defect Commander on a failure cluster.
Scores the cluster on 7 criticality dimensions, generates a Jira-ready
defect description, and optionally creates a Jira ticket.

Source: [backend/app/routers/agents.py:1341](../../../backend/app/routers/agents.py#L1341).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_role.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
cluster_id: str, run_id: uuid.UUID, project_id: uuid.UUID, project_key: Optional[str]=None, db: AsyncSession=Depends(get_db), current_user: User=Depends(get_current_active_user), _: Any=Depends(require_role(UserRole.QA_ENGINEER))
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "defect_command_api_v1_agents_defect_command_post",
  "parameters": [
    {
      "in": "query",
      "name": "cluster_id",
      "required": true,
      "schema": {
        "title": "Cluster Id",
        "type": "string"
      }
    },
    {
      "in": "query",
      "name": "run_id",
      "required": true,
      "schema": {
        "format": "uuid",
        "title": "Run Id",
        "type": "string"
      }
    },
    {
      "in": "query",
      "name": "project_id",
      "required": true,
      "schema": {
        "format": "uuid",
        "title": "Project Id",
        "type": "string"
      }
    },
    {
      "in": "query",
      "name": "project_key",
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
        "title": "Project Key"
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
  },
  "security": [
    {
      "JWT": []
    }
  ]
}
```

Handler return expressions (source excerpts, not an inferred wire schema):

```python
result
```

Contract limit: at least one response has an unstructured schema. Read the linked handler/serializer for emitted fields; the empty schema is not a promise of an empty JSON object.

## GET `/api/v1/agents/event-log/health`

Get Pipeline Event Log Health

Return write-health details for the pipeline audit event log.

Source: [backend/app/routers/agents.py:1051](../../../backend/app/routers/agents.py#L1051).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_role.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
_: Any=Depends(require_role(UserRole.QA_LEAD))
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "get_pipeline_event_log_health_api_v1_agents_event_log_health_get",
  "parameters": [
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
            "$ref": "#/components/schemas/PipelineEventLogHealthResponse"
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

## GET `/api/v1/agents/pipelines`

List Pipelines

List agent pipeline runs, optionally filtered by test run, project, or status.

Source: [backend/app/routers/agents.py:226](../../../backend/app/routers/agents.py#L226).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`.

Declared Python handler arguments (includes exact role/guard options):

```python
run_id: Optional[uuid.UUID]=Query(None), project_id: Optional[uuid.UUID]=Query(None), status: Optional[str]=Query(None), limit: int=Query(20, ge=1, le=100), db: AsyncSession=Depends(get_db), current_user: Any=Depends(get_current_active_user)
```

Direct handler error branches (dependency/service errors can add others):

```python
HTTPException(403, detail='You do not have access to this project')
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "list_pipelines_api_v1_agents_pipelines_get",
  "parameters": [
    {
      "in": "query",
      "name": "run_id",
      "required": false,
      "schema": {
        "anyOf": [
          {
            "format": "uuid",
            "type": "string"
          },
          {
            "type": "null"
          }
        ],
        "title": "Run Id"
      }
    },
    {
      "in": "query",
      "name": "project_id",
      "required": false,
      "schema": {
        "anyOf": [
          {
            "format": "uuid",
            "type": "string"
          },
          {
            "type": "null"
          }
        ],
        "title": "Project Id"
      }
    },
    {
      "in": "query",
      "name": "status",
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
        "title": "Status"
      }
    },
    {
      "in": "query",
      "name": "limit",
      "required": false,
      "schema": {
        "default": 20,
        "maximum": 100,
        "minimum": 1,
        "title": "Limit",
        "type": "integer"
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
            "items": {
              "$ref": "#/components/schemas/AgentPipelineResponse"
            },
            "title": "Response List Pipelines Api V1 Agents Pipelines Get",
            "type": "array"
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

## POST `/api/v1/agents/pipelines/bulk-trigger`

Bulk Trigger Pipelines

Queue the agent pipeline for many runs in one HTTP call.

Backs the /runs bulk-action UI ("Trigger all FAILED", multi-row select).
Replaces the previous client-side fan-out — for a 500-run trigger this
is one round-trip instead of 500. Tasks are queued in batches of 25 with
a tiny inter-batch sleep so the Celery broker isn't slammed in a single
burst. Celery's existing dedup on (run_id, workflow_type) for 7200s
handles re-triggers gracefully, so this endpoint doesn't need to dedup
itself — repeats just resolve to ``{duplicate: true}`` per task.

Source: [backend/app/routers/agents.py:697](../../../backend/app/routers/agents.py#L697).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_role.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
payload: BulkTriggerRequest, db: AsyncSession=Depends(get_db), current_user: User=Depends(get_current_active_user), _: Any=Depends(require_role(UserRole.QA_ENGINEER))
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "bulk_trigger_pipelines_api_v1_agents_pipelines_bulk_trigger_post",
  "parameters": [
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
          "$ref": "#/components/schemas/BulkTriggerRequest"
        }
      }
    },
    "required": true
  },
  "responses": {
    "202": {
      "content": {
        "application/json": {
          "schema": {
            "$ref": "#/components/schemas/BulkTriggerResponse"
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

## POST `/api/v1/agents/pipelines/trigger`

Trigger Pipeline

Manually trigger the agent pipeline for an existing test run.
Returns 202 Accepted — pipeline runs asynchronously via Celery.

Source: [backend/app/routers/agents.py:297](../../../backend/app/routers/agents.py#L297).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_role.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
payload: TriggerPipelineRequest, db: AsyncSession=Depends(get_db), current_user: User=Depends(get_current_active_user), _: Any=Depends(require_role(UserRole.QA_ENGINEER))
```

Direct handler error branches (dependency/service errors can add others):

```python
HTTPException(404, detail='TestRun not found')
HTTPException(404, detail='Workflow definition not found')
HTTPException(409, detail='Workflow definition is not published')
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "trigger_pipeline_api_v1_agents_pipelines_trigger_post",
  "parameters": [
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
          "$ref": "#/components/schemas/TriggerPipelineRequest"
        }
      }
    },
    "required": true
  },
  "responses": {
    "202": {
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
  },
  "security": [
    {
      "JWT": []
    }
  ]
}
```

Handler return expressions (source excerpts, not an inferred wire schema):

```python
JSONResponse(status_code=200, content={'message': 'Pipeline already in progress', 'pipeline_run_id': str(existing.id), 'status': existing.status, 'public_status': public_status(existing.status), 'attempt': existing.attempt, 'next_retry_at': existing.next_retry_at.isoformat() if existing.next_retry_at else None, 'run_id': str(run.id)})
{'message': 'Pipeline queued', 'task_id': task.id, 'run_id': str(run.id), 'workflow_ref': workflow_ref}
```

Contract limit: at least one response has an unstructured schema. Read the linked handler/serializer for emitted fields; the empty schema is not a promise of an empty JSON object.

## GET `/api/v1/agents/pipelines/{pipeline_id}`

Get Pipeline

Get a single pipeline run with all stage results.

Source: [backend/app/routers/agents.py:281](../../../backend/app/routers/agents.py#L281).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`.

Declared Python handler arguments (includes exact role/guard options):

```python
pipeline_id: uuid.UUID, db: AsyncSession=Depends(get_db), current_user: Any=Depends(get_current_active_user)
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "get_pipeline_api_v1_agents_pipelines__pipeline_id__get",
  "parameters": [
    {
      "in": "path",
      "name": "pipeline_id",
      "required": true,
      "schema": {
        "format": "uuid",
        "title": "Pipeline Id",
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
            "$ref": "#/components/schemas/AgentPipelineResponse"
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

## GET `/api/v1/agents/pipelines/{pipeline_id}/agentic-runtime`

Get Agentic Runtime

Return the versioned unified-runtime projection for one pipeline.

This additive adapter keeps the established timeline API stable while
exposing selected/skipped capabilities, parent-child task identity,
allocated budgets, actual usage and terminal stop reasons in one model.

Source: [backend/app/routers/agents.py:943](../../../backend/app/routers/agents.py#L943).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`.

Declared Python handler arguments (includes exact role/guard options):

```python
pipeline_id: uuid.UUID, db: AsyncSession=Depends(get_db), current_user: Any=Depends(get_current_active_user)
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "get_agentic_runtime_api_v1_agents_pipelines__pipeline_id__agentic_runtime_get",
  "parameters": [
    {
      "in": "path",
      "name": "pipeline_id",
      "required": true,
      "schema": {
        "format": "uuid",
        "title": "Pipeline Id",
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
            "$ref": "#/components/schemas/AgenticRunV1"
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

## POST `/api/v1/agents/pipelines/{pipeline_id}/cancel`

Cancel Pipeline

Cancel a pipeline run (E7.4).

A ``pending`` or ``retry_wait`` run has no live worker and stops here and
now. A ``running`` run is *asked* to stop: the flag is set and the worker
terminalises itself at its next stage boundary, so its in-flight writes
land coherently instead of being orphaned under a row the API already
declared dead.

Cancellation is sticky either way — an automatic retry can never bring a
cancelled run back. See ``app/services/pipeline_cancellation.py`` for why
both orderings of the cancel-vs-retry race end ``failed``.

Source: [backend/app/routers/agents.py:627](../../../backend/app/routers/agents.py#L627).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_role.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
pipeline_id: uuid.UUID, db: AsyncSession=Depends(get_db), current_user: User=Depends(get_current_active_user), _: Any=Depends(require_role(UserRole.QA_ENGINEER))
```

Direct handler error branches (dependency/service errors can add others):

```python
HTTPException(409, detail={'message': 'Pipeline has already finished', 'pipeline_run_id': str(pipeline.id), **outcome.as_dict()})
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "cancel_pipeline_api_v1_agents_pipelines__pipeline_id__cancel_post",
  "parameters": [
    {
      "in": "path",
      "name": "pipeline_id",
      "required": true,
      "schema": {
        "format": "uuid",
        "title": "Pipeline Id",
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
  },
  "security": [
    {
      "JWT": []
    }
  ]
}
```

Handler return expressions (source excerpts, not an inferred wire schema):

```python
{'pipeline_run_id': str(pipeline.id), **outcome.as_dict()}
```

Contract limit: at least one response has an unstructured schema. Read the linked handler/serializer for emitted fields; the empty schema is not a promise of an empty JSON object.

## GET `/api/v1/agents/pipelines/{pipeline_id}/replay`

Get Pipeline Replay

Return a deterministic replay document for audit reconstruction.

Source: [backend/app/routers/agents.py:1066](../../../backend/app/routers/agents.py#L1066).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`.

Declared Python handler arguments (includes exact role/guard options):

```python
pipeline_id: uuid.UUID, db: AsyncSession=Depends(get_db), current_user: Any=Depends(get_current_active_user)
```

Direct handler error branches (dependency/service errors can add others):

```python
HTTPException(404, detail='Pipeline run not found')
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "get_pipeline_replay_api_v1_agents_pipelines__pipeline_id__replay_get",
  "parameters": [
    {
      "in": "path",
      "name": "pipeline_id",
      "required": true,
      "schema": {
        "format": "uuid",
        "title": "Pipeline Id",
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
            "$ref": "#/components/schemas/PipelineReplayResponse"
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

## POST `/api/v1/agents/pipelines/{pipeline_id}/retry`

Retry Pipeline

Retry a finished-but-unsuccessful pipeline run (E7.4).

Two outcomes, decided by configuration rather than by the caller:

* **resume** — the configuration this run froze at start still matches the
  live one, so the same row is replayed and its completed stages are kept.
* **rerun** — the configuration changed (a narrowed tool allowlist, a
  different model, an ``AI_OFFLINE_MODE`` flip), so a NEW run starts with
  ``rerun_of`` pointing here. Replaying checkpoints authorised under the old
  configuration could re-enter a tool the new one forbids, and the operator
  who changed the config is retrying precisely because they want the change
  to take effect.

Refuses (409) when the run is still in progress, when it finished
successfully, was rejected in review, or is at its attempt ceiling. A
review rejection is terminal, even if configuration changed; start a new
run explicitly to produce a fresh proposal. The rejection and ceiling responses
carry ``links.rerun`` so the caller can deliberately start a fresh run
rather than being told only "no".

Source: [backend/app/routers/agents.py:457](../../../backend/app/routers/agents.py#L457).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_role.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
pipeline_id: uuid.UUID, db: AsyncSession=Depends(get_db), current_user: User=Depends(get_current_active_user), _: Any=Depends(require_role(UserRole.QA_ENGINEER))
```

Direct handler error branches (dependency/service errors can add others):

```python
HTTPException(404, detail='TestRun not found for this pipeline')
HTTPException(409, detail={'message': 'Another pipeline for this run is already in progress', 'pipeline_run_id': str(in_flight.id), 'status': in_flight.status, 'public_status': public_status(in_flight.status)})
HTTPException(409, detail={'message': 'Pipeline finished successfully; nothing to retry', 'pipeline_run_id': str(pipeline.id), 'status': current.value, 'public_status': public_status(current), 'links': {'rerun': '/api/v1/agents/pipelines/trigger'}})
HTTPException(409, detail={'message': 'Pipeline is still in progress', 'pipeline_run_id': str(pipeline.id), 'status': current.value, 'public_status': public_status(current)})
HTTPException(409, detail={'message': 'Pipeline was rejected in review; start a new run instead', 'reason': 'review_rejected', 'pipeline_run_id': str(pipeline.id), 'status': current.value, 'public_status': public_status(current), 'links': {'rerun': '/api/v1/agents/pipelines/trigger'}})
HTTPException(409, detail={'message': f'Pipeline reached its attempt ceiling ({attempt}/{max_attempts}). Start a new run instead.', 'pipeline_run_id': str(pipeline.id), 'attempt': attempt, 'max_attempts': max_attempts, 'status': current.value, 'public_status': public_status(current), 'links': {'rerun': '/api/v1/agents/pipelines/trigger'}})
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "retry_pipeline_api_v1_agents_pipelines__pipeline_id__retry_post",
  "parameters": [
    {
      "in": "path",
      "name": "pipeline_id",
      "required": true,
      "schema": {
        "format": "uuid",
        "title": "Pipeline Id",
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
  },
  "security": [
    {
      "JWT": []
    }
  ]
}
```

Handler return expressions (source excerpts, not an inferred wire schema):

```python
RetryPipelineResponse(mode='rerun', pipeline_run_id=None, rerun_of=str(pipeline.id), attempt=1, max_attempts=max_attempts, reason=plan.reason, status=PipelineRunStatus.PENDING.value, public_status=public_status(PipelineRunStatus.PENDING), links={'poll': f'/api/v1/agents/pipelines?run_id={run.id}', 'replaces': f'/api/v1/agents/pipelines/{pipeline.id}'})
RetryPipelineResponse(mode='resume', pipeline_run_id=str(pipeline.id), rerun_of=None, attempt=attempt + 1, max_attempts=max_attempts, reason=plan.reason, status=current.value, public_status=public_status(current), links={'poll': f'/api/v1/agents/pipelines/{pipeline.id}'})
```

Contract limit: at least one response has an unstructured schema. Read the linked handler/serializer for emitted fields; the empty schema is not a promise of an empty JSON object.

## GET `/api/v1/agents/pipelines/{pipeline_id}/stages`

Get Pipeline Stages

Get detailed stage results for a pipeline run.

Source: [backend/app/routers/agents.py:790](../../../backend/app/routers/agents.py#L790).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`.

Declared Python handler arguments (includes exact role/guard options):

```python
pipeline_id: uuid.UUID, db: AsyncSession=Depends(get_db), current_user: Any=Depends(get_current_active_user)
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "get_pipeline_stages_api_v1_agents_pipelines__pipeline_id__stages_get",
  "parameters": [
    {
      "in": "path",
      "name": "pipeline_id",
      "required": true,
      "schema": {
        "format": "uuid",
        "title": "Pipeline Id",
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
            "items": {
              "additionalProperties": true,
              "type": "object"
            },
            "title": "Response Get Pipeline Stages Api V1 Agents Pipelines  Pipeline Id  Stages Get",
            "type": "array"
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

## GET `/api/v1/agents/pipelines/{pipeline_id}/timeline`

Get Pipeline Timeline

Get the full agent timeline with per-stage observability data:
tokens, cost, latency, confidence, evidence count, route rationale,
error taxonomy, fallback status, and alerts.

Source: [backend/app/routers/agents.py:829](../../../backend/app/routers/agents.py#L829).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`.

Declared Python handler arguments (includes exact role/guard options):

```python
pipeline_id: uuid.UUID, db: AsyncSession=Depends(get_db), current_user: Any=Depends(get_current_active_user)
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "get_pipeline_timeline_api_v1_agents_pipelines__pipeline_id__timeline_get",
  "parameters": [
    {
      "in": "path",
      "name": "pipeline_id",
      "required": true,
      "schema": {
        "format": "uuid",
        "title": "Pipeline Id",
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
            "$ref": "#/components/schemas/PipelineTimelineResponse"
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

## POST `/api/v1/agents/regression-watch`

Regression Watch

Run the Regression Watchman on an existing test run.
Returns classification of each failure cluster as:
  - new_regression | known_flaky_recurrence | environmental_anomaly

Source: [backend/app/routers/agents.py:1382](../../../backend/app/routers/agents.py#L1382).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_role.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
run_id: uuid.UUID, project_id: uuid.UUID, db: AsyncSession=Depends(get_db), current_user: User=Depends(get_current_active_user), _: Any=Depends(require_role(UserRole.QA_ENGINEER))
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "regression_watch_api_v1_agents_regression_watch_post",
  "parameters": [
    {
      "in": "query",
      "name": "run_id",
      "required": true,
      "schema": {
        "format": "uuid",
        "title": "Run Id",
        "type": "string"
      }
    },
    {
      "in": "query",
      "name": "project_id",
      "required": true,
      "schema": {
        "format": "uuid",
        "title": "Project Id",
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
  },
  "security": [
    {
      "JWT": []
    }
  ]
}
```

Handler return expressions (source excerpts, not an inferred wire schema):

```python
{'run_id': str(run_id), 'classifications': result}
```

Contract limit: at least one response has an unstructured schema. Read the linked handler/serializer for emitted fields; the empty schema is not a promise of an empty JSON object.

## GET `/api/v1/agents/runs/{run_id}/pipeline-status`

Get Pipeline Status

WF-1: Return the latest pipeline execution status for a run and workflow type.

Enables the frontend to know when deep/offline analysis ran, whether it
completed fully, partially, or failed, without scanning the full timeline.

Source: [backend/app/routers/agents.py:1153](../../../backend/app/routers/agents.py#L1153).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_run_access.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
run_id: str, workflow_type: str=Query(default='deep', pattern='^(offline|deep|live)$'), db: AsyncSession=Depends(get_db), _: Any=Depends(require_run_access())
```

Direct handler error branches (dependency/service errors can add others):

```python
HTTPException(400, detail='Invalid run_id')
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "get_pipeline_status_api_v1_agents_runs__run_id__pipeline_status_get",
  "parameters": [
    {
      "in": "path",
      "name": "run_id",
      "required": true,
      "schema": {
        "title": "Run Id",
        "type": "string"
      }
    },
    {
      "in": "query",
      "name": "workflow_type",
      "required": false,
      "schema": {
        "default": "deep",
        "pattern": "^(offline|deep|live)$",
        "title": "Workflow Type",
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
  },
  "security": [
    {
      "JWT": []
    }
  ]
}
```

Handler return expressions (source excerpts, not an inferred wire schema):

```python
{'pipeline_run_id': None, 'workflow_type': workflow_type, 'status': 'never_run', 'started_at': None, 'completed_at': None, 'error': None, 'stage_summary': {'completed': 0, 'failed': 0, 'skipped': 0, 'pending': 0}}
{'pipeline_run_id': str(pipeline.id), 'workflow_type': pipeline.workflow_type, 'status': pipeline.status or 'pending', 'public_status': public_status(pipeline.status or 'pending'), 'degraded': (pipeline.execution_metadata or {}).get('stage_quality') == 'degraded', 'started_at': pipeline.created_at.isoformat() if pipeline.created_at else None, 'completed_at': pipeline.completed_at.isoformat() if pipeline.completed_at else None, 'error': pipeline.error, 'stage_summary': stage_summary}
```

Contract limit: at least one response has an unstructured schema. Read the linked handler/serializer for emitted fields; the empty schema is not a promise of an empty JSON object.

## GET `/api/v1/agents/runs/{run_id}/summary`

Get Run Summary

Retrieve the AI-generated markdown summary for a test run (all 4 layers if available).

Carries the human-review envelope (E8.3): ``requires_human_review``,
``review`` and the ``X-TestLookup-AI-Generated`` / ``X-TestLookup-Review-State``
headers. A deterministic fallback is ``not_applicable``: no AI wrote it.

Source: [backend/app/routers/agents.py:1228](../../../backend/app/routers/agents.py#L1228).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_run_access.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
run_id: str, response: Response, db: AsyncSession=Depends(get_db), _: Any=Depends(require_run_access())
```

Direct handler error branches (dependency/service errors can add others):

```python
HTTPException(404, detail='No summary found for this run')
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "get_run_summary_api_v1_agents_runs__run_id__summary_get",
  "parameters": [
    {
      "in": "path",
      "name": "run_id",
      "required": true,
      "schema": {
        "title": "Run Id",
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
            "$ref": "#/components/schemas/AgentRunSummaryResponse"
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
