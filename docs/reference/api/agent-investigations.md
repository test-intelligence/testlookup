# Agent Investigations API

[Documentation home](../../README.md) · [Regeneration](../../handoff/maintenance.md)

Generated from tracked source by `scripts/generate_handoff_reference.py`. Do not edit by hand. The baseline and validation limits are recorded in [verification](../../handoff/verification.md).

## GET `/api/v1/investigations/{investigation_id}`

Get Investigation

InvestigationDetail (pinned wire shape). The UI polls this.

Source: [backend/app/routers/agent_investigations.py:219](../../../backend/app/routers/agent_investigations.py#L219).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_investigation_access.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
investigation_id: uuid.UUID, db: AsyncSession=Depends(get_db), current_user: User=Depends(require_investigation_access())
```

Direct handler error branches (dependency/service errors can add others):

```python
HTTPException(status_code=404, detail='Investigation not found')
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "get_investigation_api_v1_investigations__investigation_id__get",
  "parameters": [
    {
      "in": "path",
      "name": "investigation_id",
      "required": true,
      "schema": {
        "format": "uuid",
        "title": "Investigation Id",
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
            "additionalProperties": true,
            "title": "Response Get Investigation Api V1 Investigations  Investigation Id  Get",
            "type": "object"
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

## POST `/api/v1/investigations/{investigation_id}/cancel`

Cancel Investigation

Request cooperative cancellation (QA_ENGINEER+). The workflow checks
the flag between nodes and finalizes the row as ``cancelled``.

Source: [backend/app/routers/agent_investigations.py:236](../../../backend/app/routers/agent_investigations.py#L236).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_investigation_access.<locals>._check`, `require_role.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
investigation_id: uuid.UUID, db: AsyncSession=Depends(get_db), current_user: User=Depends(require_investigation_access()), _writer: User=Depends(require_role(UserRole.QA_ENGINEER))
```

Direct handler error branches (dependency/service errors can add others):

```python
HTTPException(status_code=404, detail='Investigation not found')
HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f'Investigation is already {investigation.status} — nothing to cancel.')
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "cancel_investigation_api_v1_investigations__investigation_id__cancel_post",
  "parameters": [
    {
      "in": "path",
      "name": "investigation_id",
      "required": true,
      "schema": {
        "format": "uuid",
        "title": "Investigation Id",
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
            "additionalProperties": true,
            "title": "Response Cancel Investigation Api V1 Investigations  Investigation Id  Cancel Post",
            "type": "object"
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

## GET `/api/v1/projects/{project_id}/agent-policies`

List Agent Policies

Every known agent's effective policy (defaults when no row exists).

Source: [backend/app/routers/agent_investigations.py:305](../../../backend/app/routers/agent_investigations.py#L305).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_project_access.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
project_id: uuid.UUID, db: AsyncSession=Depends(get_db), current_user: User=Depends(require_project_access())
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "deprecated": true,
  "operationId": "list_agent_policies_api_v1_projects__project_id__agent_policies_get",
  "parameters": [
    {
      "in": "path",
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
          "schema": {
            "additionalProperties": true,
            "title": "Response List Agent Policies Api V1 Projects  Project Id  Agent Policies Get",
            "type": "object"
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

## PUT `/api/v1/projects/{project_id}/agent-policies/{agent_id}`

Update Agent Policy

Retired write alias; agent-configs is the single writable resource.

Source: [backend/app/routers/agent_investigations.py:320](../../../backend/app/routers/agent_investigations.py#L320).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_project_access.<locals>._check`, `require_project_role.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
project_id: uuid.UUID, agent_id: str, db: AsyncSession=Depends(get_db), current_user: User=Depends(require_project_access()), _lead: User=Depends(require_project_role(UserRole.QA_LEAD))
```

Direct handler error branches (dependency/service errors can add others):

```python
HTTPException(status_code=status.HTTP_405_METHOD_NOT_ALLOWED, detail='agent-policies is read-only; write /agent-configs/investigator', headers={'Location': f'/api/v1/projects/{project_id}/agent-configs/investigator'})
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "deprecated": true,
  "operationId": "update_agent_policy_api_v1_projects__project_id__agent_policies__agent_id__put",
  "parameters": [
    {
      "in": "path",
      "name": "project_id",
      "required": true,
      "schema": {
        "format": "uuid",
        "title": "Project Id",
        "type": "string"
      }
    },
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
            "additionalProperties": true,
            "title": "Response Update Agent Policy Api V1 Projects  Project Id  Agent Policies  Agent Id  Put",
            "type": "object"
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

## GET `/api/v1/projects/{project_id}/agent-runs`

List Agent Runs

Paged AgentRunEntry ledger, newest first (AI-3).

Source: [backend/app/routers/agent_investigations.py:339](../../../backend/app/routers/agent_investigations.py#L339).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_project_access.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
project_id: uuid.UUID, agent_id: Optional[str]=None, limit: int=50, offset: int=0, db: AsyncSession=Depends(get_db), current_user: User=Depends(require_project_access())
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "list_agent_runs_api_v1_projects__project_id__agent_runs_get",
  "parameters": [
    {
      "in": "path",
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
      "name": "agent_id",
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
        "title": "Agent Id"
      }
    },
    {
      "in": "query",
      "name": "limit",
      "required": false,
      "schema": {
        "default": 50,
        "title": "Limit",
        "type": "integer"
      }
    },
    {
      "in": "query",
      "name": "offset",
      "required": false,
      "schema": {
        "default": 0,
        "title": "Offset",
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
            "additionalProperties": true,
            "title": "Response List Agent Runs Api V1 Projects  Project Id  Agent Runs Get",
            "type": "object"
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

## GET `/api/v1/projects/{project_id}/investigations`

List Investigations

Paged InvestigationSummary list, newest first.

Source: [backend/app/routers/agent_investigations.py:266](../../../backend/app/routers/agent_investigations.py#L266).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_project_access.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
project_id: uuid.UUID, limit: int=20, offset: int=0, db: AsyncSession=Depends(get_db), current_user: User=Depends(require_project_access())
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "list_investigations_api_v1_projects__project_id__investigations_get",
  "parameters": [
    {
      "in": "path",
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
      "name": "limit",
      "required": false,
      "schema": {
        "default": 20,
        "title": "Limit",
        "type": "integer"
      }
    },
    {
      "in": "query",
      "name": "offset",
      "required": false,
      "schema": {
        "default": 0,
        "title": "Offset",
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
            "additionalProperties": true,
            "title": "Response List Investigations Api V1 Projects  Project Id  Investigations Get",
            "type": "object"
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

## POST `/api/v1/runs/{run_id}/investigations`

Start Investigation

Trigger a manual investigation for a run (QA_ENGINEER+).

Source: [backend/app/routers/agent_investigations.py:163](../../../backend/app/routers/agent_investigations.py#L163).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_role.<locals>._check`, `require_run_access.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
run_id: uuid.UUID, db: AsyncSession=Depends(get_db), current_user: User=Depends(require_run_access()), _writer: User=Depends(require_role(UserRole.QA_ENGINEER))
```

Direct handler error branches (dependency/service errors can add others):

```python
HTTPException(status_code=404, detail='Test run not found')
HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=f'The Investigator agent is disabled for this project by its configuration. A QA Lead can enable it via PUT /api/v1/projects/{run.project_id}/agent-configs/investigator.')
HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f'An investigation is already running for this run (investigation_id={exc.investigation_id}).')
HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=f"The project's max_runs_per_day budget ({exc.max_runs_per_day}) is exhausted for today. A QA Lead can raise it via the agent policy.")
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "start_investigation_api_v1_runs__run_id__investigations_post",
  "parameters": [
    {
      "in": "path",
      "name": "run_id",
      "required": true,
      "schema": {
        "format": "uuid",
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
    "202": {
      "content": {
        "application/json": {
          "schema": {
            "additionalProperties": true,
            "title": "Response Start Investigation Api V1 Runs  Run Id  Investigations Post",
            "type": "object"
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
