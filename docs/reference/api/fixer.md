# Fixer API

[Documentation home](../../README.md) · [Regeneration](../../handoff/maintenance.md)

Generated from tracked source by `scripts/generate_handoff_reference.py`. Do not edit by hand. The baseline and validation limits are recorded in [verification](../../handoff/verification.md).

## GET `/api/v1/fixer/attempts/{attempt_id}`

Get Fixer Attempt

A single FixAttempt with patch + runner_log_digest + ledger_run_id.

Source: [backend/app/routers/fixer.py:273](../../../backend/app/routers/fixer.py#L273).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_attempt_access.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
attempt_id: uuid.UUID, db: AsyncSession=Depends(get_db), current_user: User=Depends(require_attempt_access())
```

Direct handler error branches (dependency/service errors can add others):

```python
HTTPException(status_code=404, detail='Fix attempt not found')
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "get_fixer_attempt_api_v1_fixer_attempts__attempt_id__get",
  "parameters": [
    {
      "in": "path",
      "name": "attempt_id",
      "required": true,
      "schema": {
        "format": "uuid",
        "title": "Attempt Id",
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
            "title": "Response Get Fixer Attempt Api V1 Fixer Attempts  Attempt Id  Get",
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

## GET `/api/v1/projects/{project_id}/fixer/attempts`

List Fixer Attempts

Paged FixAttempt list, newest first.

Source: [backend/app/routers/fixer.py:242](../../../backend/app/routers/fixer.py#L242).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_project_access.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
project_id: uuid.UUID, limit: int=20, offset: int=0, db: AsyncSession=Depends(get_db), current_user: User=Depends(require_project_access())
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "list_fixer_attempts_api_v1_projects__project_id__fixer_attempts_get",
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
            "title": "Response List Fixer Attempts Api V1 Projects  Project Id  Fixer Attempts Get",
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

## GET `/api/v1/projects/{project_id}/fixer/config`

Get Fixer Config

The project's effective FixerConfig (defaults when unconfigured).

Source: [backend/app/routers/fixer.py:161](../../../backend/app/routers/fixer.py#L161).

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
  "operationId": "get_fixer_config_api_v1_projects__project_id__fixer_config_get",
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
            "title": "Response Get Fixer Config Api V1 Projects  Project Id  Fixer Config Get",
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

## PUT `/api/v1/projects/{project_id}/fixer/config`

Put Fixer Config

Retired write alias; agent-configs is the single writable resource.

Source: [backend/app/routers/fixer.py:172](../../../backend/app/routers/fixer.py#L172).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_project_access.<locals>._check`, `require_project_role.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
project_id: uuid.UUID, db: AsyncSession=Depends(get_db), current_user: User=Depends(require_project_access()), _lead: User=Depends(require_project_role(UserRole.QA_LEAD))
```

Direct handler error branches (dependency/service errors can add others):

```python
HTTPException(status_code=status.HTTP_405_METHOD_NOT_ALLOWED, detail='fixer/config is read-only; write /agent-configs/fixer', headers={'Location': f'/api/v1/projects/{project_id}/agent-configs/fixer'})
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "deprecated": true,
  "operationId": "put_fixer_config_api_v1_projects__project_id__fixer_config_put",
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
            "title": "Response Put Fixer Config Api V1 Projects  Project Id  Fixer Config Put",
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

## POST `/api/v1/projects/{project_id}/fixer/run`

Start Fixer Run

Trigger a manual fixer run (project QA_ENGINEER+).

Source: [backend/app/routers/fixer.py:190](../../../backend/app/routers/fixer.py#L190).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_project_access.<locals>._check`, `require_project_role.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
project_id: uuid.UUID, db: AsyncSession=Depends(get_db), current_user: User=Depends(require_project_access()), _writer: User=Depends(require_project_role(UserRole.QA_ENGINEER))
```

Direct handler error branches (dependency/service errors can add others):

```python
HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=f'The Fixer is disabled for this project. A QA Lead can enable it via PUT /api/v1/projects/{project_id}/agent-configs/fixer.')
HTTPException(status_code=status.HTTP_409_CONFLICT, detail='A fixer run is already in flight for this project.')
HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail='Suggest mode requires a runner (docker or workflow_dispatch). Configure fixer.runner.type before running in suggest mode.')
HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail='The fixer run could not be queued (task broker unavailable). Try again shortly.')
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "start_fixer_run_api_v1_projects__project_id__fixer_run_post",
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
    "202": {
      "content": {
        "application/json": {
          "schema": {
            "additionalProperties": true,
            "title": "Response Start Fixer Run Api V1 Projects  Project Id  Fixer Run Post",
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
