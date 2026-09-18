# Run Compare API

[Documentation home](../../README.md) · [Regeneration](../../handoff/maintenance.md)

Generated from tracked source by `scripts/generate_handoff_reference.py`. Do not edit by hand. The baseline and validation limits are recorded in [verification](../../handoff/verification.md).

## GET `/api/v1/runs/compare`

Compare Two Runs

Compare two test runs and return per-test + aggregate deltas.

Classifies every test whose status or duration differed between
the two runs into buckets like ``new_failure``, ``fixed``,
``regressed``, ``duration_spike``. The response is sorted with
the most urgent deltas first so the UI's default view surfaces
regressions before wins.

Args:
    left: UUID of the baseline ("before") run.
    right: UUID of the target ("after") run.

Source: [backend/app/routers/run_compare.py:34](../../../backend/app/routers/run_compare.py#L34).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`.

Declared Python handler arguments (includes exact role/guard options):

```python
left: uuid.UUID, right: uuid.UUID, suite_name: Optional[str]=Query(None, description='Optional suite scope; matched case-insensitively'), include_ai_report: bool=Query(True, description='Attach cached or generated AI comparison report'), db: AsyncSession=Depends(get_db), current_user: User=Depends(get_current_active_user)
```

Direct handler error branches (dependency/service errors can add others):

```python
HTTPException(status_code=404, detail='Left run not found')
HTTPException(status_code=404, detail='Right run not found')
HTTPException(status_code=404, detail=str(exc))
HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='left and right must be different run ids')
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "compare_two_runs_api_v1_runs_compare_get",
  "parameters": [
    {
      "in": "query",
      "name": "left",
      "required": true,
      "schema": {
        "format": "uuid",
        "title": "Left",
        "type": "string"
      }
    },
    {
      "in": "query",
      "name": "right",
      "required": true,
      "schema": {
        "format": "uuid",
        "title": "Right",
        "type": "string"
      }
    },
    {
      "description": "Optional suite scope; matched case-insensitively",
      "in": "query",
      "name": "suite_name",
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
        "description": "Optional suite scope; matched case-insensitively",
        "title": "Suite Name"
      }
    },
    {
      "description": "Attach cached or generated AI comparison report",
      "in": "query",
      "name": "include_ai_report",
      "required": false,
      "schema": {
        "default": true,
        "description": "Attach cached or generated AI comparison report",
        "title": "Include Ai Report",
        "type": "boolean"
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
            "$ref": "#/components/schemas/RunCompareResponse"
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

## GET `/api/v1/runs/compare/latest`

Compare Latest Suite Runs

Compare the latest completed run for a suite against the previous latest
completed run for the same suite and branch.

Source: [backend/app/routers/run_compare.py:121](../../../backend/app/routers/run_compare.py#L121).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`.

Declared Python handler arguments (includes exact role/guard options):

```python
suite_name: str=Query(..., min_length=1, description='Suite name; matched case-insensitively'), project_id: Optional[uuid.UUID]=Query(None), include_ai_report: bool=Query(True, description='Attach cached or generated AI comparison report'), db: AsyncSession=Depends(get_db), current_user: User=Depends(get_current_active_user)
```

Direct handler error branches (dependency/service errors can add others):

```python
HTTPException(status_code=404, detail=str(exc))
HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='project_id is required when multiple projects are accessible')
HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "compare_latest_suite_runs_api_v1_runs_compare_latest_get",
  "parameters": [
    {
      "description": "Suite name; matched case-insensitively",
      "in": "query",
      "name": "suite_name",
      "required": true,
      "schema": {
        "description": "Suite name; matched case-insensitively",
        "minLength": 1,
        "title": "Suite Name",
        "type": "string"
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
      "description": "Attach cached or generated AI comparison report",
      "in": "query",
      "name": "include_ai_report",
      "required": false,
      "schema": {
        "default": true,
        "description": "Attach cached or generated AI comparison report",
        "title": "Include Ai Report",
        "type": "boolean"
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
            "$ref": "#/components/schemas/RunCompareResponse"
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
