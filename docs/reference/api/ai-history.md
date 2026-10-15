# Ai History API

[Documentation home](../../README.md) · [Regeneration](../../maintainers/maintenance.md)

Generated from tracked source by `scripts/generate_reference.py`. Do not edit by hand. What the checks cover is recorded in [verification](../../maintainers/verification.md).

## GET `/api/v1/runs/{run_id}/ai-compare`

Compare Ai With Baseline

This run's AI analysis against a baseline's (default: the D42 previous run). Live sides
by default; a pinned version compares what a pipeline produced. Never triggers an LLM.

Source: [backend/app/routers/ai_history.py:253](../../../backend/app/routers/ai_history.py#L253).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_run_access.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
run_id: uuid.UUID, response: Response, baseline_run_id: Optional[uuid.UUID]=Query(default=None), suite_name: Optional[str]=Query(default=None, min_length=1, max_length=500), version_id: Optional[uuid.UUID]=Query(default=None, description='A digest version id of THIS run'), baseline_version_id: Optional[uuid.UUID]=Query(default=None, description='A digest version id of the BASELINE run'), db: Any=Depends(get_db), _: User=Depends(require_run_access())
```

Direct handler error branches (dependency/service errors can add others):

```python
HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail='baseline_run_id must be a different run')
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "compare_ai_with_baseline_api_v1_runs__run_id__ai_compare_get",
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
      "in": "query",
      "name": "baseline_run_id",
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
        "title": "Baseline Run Id"
      }
    },
    {
      "in": "query",
      "name": "suite_name",
      "required": false,
      "schema": {
        "anyOf": [
          {
            "maxLength": 500,
            "minLength": 1,
            "type": "string"
          },
          {
            "type": "null"
          }
        ],
        "title": "Suite Name"
      }
    },
    {
      "description": "A digest version id of THIS run",
      "in": "query",
      "name": "version_id",
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
        "description": "A digest version id of THIS run",
        "title": "Version Id"
      }
    },
    {
      "description": "A digest version id of the BASELINE run",
      "in": "query",
      "name": "baseline_version_id",
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
        "description": "A digest version id of the BASELINE run",
        "title": "Baseline Version Id"
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
            "$ref": "#/components/schemas/AICompareResponse"
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

## GET `/api/v1/runs/{run_id}/ai-history`

List Ai History

Stored AI versions of the run (one kind), newest first. Reads each version's
``meta``/``provenance`` only, never its payload. An empty store is ``versions: []``.

Source: [backend/app/routers/ai_history.py:106](../../../backend/app/routers/ai_history.py#L106).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_run_access.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
run_id: uuid.UUID, response: Response, kind: str=Query(default='analysis_digest', pattern=_KIND_PATTERN, description='analysis_digest | run_summary'), limit: int=Query(default=50, ge=1, le=100), db: Any=Depends(get_db), _: User=Depends(require_run_access())
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "list_ai_history_api_v1_runs__run_id__ai_history_get",
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
      "description": "analysis_digest | run_summary",
      "in": "query",
      "name": "kind",
      "required": false,
      "schema": {
        "default": "analysis_digest",
        "description": "analysis_digest | run_summary",
        "pattern": "^(analysis_digest|run_summary)$",
        "title": "Kind",
        "type": "string"
      }
    },
    {
      "in": "query",
      "name": "limit",
      "required": false,
      "schema": {
        "default": 50,
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
            "$ref": "#/components/schemas/AIHistoryListResponse"
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

## GET `/api/v1/runs/{run_id}/ai-history/diff`

Diff Ai History

Structured diff of two analysis digests of the same run (mode ``versions``).

Source: [backend/app/routers/ai_history.py:127](../../../backend/app/routers/ai_history.py#L127).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_run_access.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
run_id: uuid.UUID, response: Response, from_version: uuid.UUID=Query(..., alias='from', description='A digest version id of THIS run'), to: str=Query(default='latest', max_length=64, description='A version id | latest | live'), db: Any=Depends(get_db), _: User=Depends(require_run_access())
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "diff_ai_history_api_v1_runs__run_id__ai_history_diff_get",
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
      "description": "A digest version id of THIS run",
      "in": "query",
      "name": "from",
      "required": true,
      "schema": {
        "description": "A digest version id of THIS run",
        "format": "uuid",
        "title": "From",
        "type": "string"
      }
    },
    {
      "description": "A version id | latest | live",
      "in": "query",
      "name": "to",
      "required": false,
      "schema": {
        "default": "latest",
        "description": "A version id | latest | live",
        "maxLength": 64,
        "title": "To",
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
            "$ref": "#/components/schemas/AIDigestDiffResponse"
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

## GET `/api/v1/runs/{run_id}/ai-history/export`

Export Ai History

The run's whole AI history as one JSON file (``AIHistoryExportBundle``).

AI report content leaving the system: the E8.4 distribution policy decides
(channel ``ai_history_export``) and its audit row is committed whether the
export is allowed or refused (409) -- the one documented write of this
family of GETs, exactly like the intelligence export.

Source: [backend/app/routers/ai_history.py:152](../../../backend/app/routers/ai_history.py#L152).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_run_access.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
run_id: uuid.UUID, db: Any=Depends(get_db), _: User=Depends(require_run_access()), current_user: User=Depends(get_current_active_user)
```

Direct handler error branches (dependency/service errors can add others):

```python
HTTPException(status_code=status.HTTP_409_CONFLICT, detail=refusal_detail(distribution))
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "export_ai_history_api_v1_runs__run_id__ai_history_export_get",
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
Response(content=json.dumps(bundle, indent=2, default=str), media_type='application/json', headers={'Content-Disposition': f'attachment; filename="{export_filename(run.build_number, run_id)}"'})
```

Contract limit: at least one response has an unstructured schema. Read the linked handler/serializer for emitted fields; the empty schema is not a promise of an empty JSON object.

## GET `/api/v1/runs/{run_id}/ai-history/{version_id}`

Get Ai History Version

One stored version (any kind) with its payload. A version of another run is 404.

Source: [backend/app/routers/ai_history.py:195](../../../backend/app/routers/ai_history.py#L195).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_run_access.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
run_id: uuid.UUID, version_id: uuid.UUID, response: Response, db: Any=Depends(get_db), _: User=Depends(require_run_access())
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "get_ai_history_version_api_v1_runs__run_id__ai_history__version_id__get",
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
      "in": "path",
      "name": "version_id",
      "required": true,
      "schema": {
        "format": "uuid",
        "title": "Version Id",
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
            "$ref": "#/components/schemas/AIVersionResponse"
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

## GET `/api/v1/runs/{run_id}/previous`

Get Previous Run

The D42 previous run: same project, suite and branch, completed, newest finished
before this run. "No baseline" is an answer (``reason`` + ``message``), never an error.

Source: [backend/app/routers/ai_history.py:240](../../../backend/app/routers/ai_history.py#L240).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_run_access.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
run_id: uuid.UUID, suite_name: Optional[str]=Query(default=None, min_length=1, max_length=500), db: Any=Depends(get_db), _: User=Depends(require_run_access())
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "get_previous_run_api_v1_runs__run_id__previous_get",
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
      "in": "query",
      "name": "suite_name",
      "required": false,
      "schema": {
        "anyOf": [
          {
            "maxLength": 500,
            "minLength": 1,
            "type": "string"
          },
          {
            "type": "null"
          }
        ],
        "title": "Suite Name"
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
            "$ref": "#/components/schemas/AIPreviousRunResponse"
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

## GET `/api/v1/runs/{run_id}/tests/{test_id}/ai-history`

Get Test Ai History

The stored analysis versions of one test result, newest first, plus the current row.
Postgres only: never a 503.

Source: [backend/app/routers/ai_history.py:223](../../../backend/app/routers/ai_history.py#L223).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_run_access.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
run_id: uuid.UUID, test_id: uuid.UUID, limit: int=Query(default=20, ge=1, le=50), db: Any=Depends(get_db), _: User=Depends(require_run_access())
```

Direct handler error branches (dependency/service errors can add others):

```python
HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Test case not found')
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "get_test_ai_history_api_v1_runs__run_id__tests__test_id__ai_history_get",
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
      "in": "path",
      "name": "test_id",
      "required": true,
      "schema": {
        "format": "uuid",
        "title": "Test Id",
        "type": "string"
      }
    },
    {
      "in": "query",
      "name": "limit",
      "required": false,
      "schema": {
        "default": 20,
        "maximum": 50,
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
            "$ref": "#/components/schemas/AITestHistoryResponse"
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
