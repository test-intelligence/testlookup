# Debug API

[Documentation home](../../README.md) · [Regeneration](../../handoff/maintenance.md)

Generated from tracked source by `scripts/generate_handoff_reference.py`. Do not edit by hand. The baseline and validation limits are recorded in [verification](../../handoff/verification.md).

## POST `/api/v1/debug/generate-test-run`

Generate Mock Test Run

Generates a synthetic test run and triggers the ingestion pipeline.

Source: [backend/app/routers/debug.py:21](../../../backend/app/routers/debug.py#L21).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_role.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
project_id: uuid.UUID, num_tests: int=50, failure_rate: float=0.2, report_type: Literal['allure', 'testng', 'both']='both', db: AsyncSession=Depends(get_db), current_user: User=Depends(require_role(UserRole.ADMIN))
```

Direct handler error branches (dependency/service errors can add others):

```python
HTTPException(status_code=404, detail='Project not found')
HTTPException(status_code=500, detail=str(e))
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "generate_mock_test_run_api_v1_debug_generate_test_run_post",
  "parameters": [
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
      "name": "num_tests",
      "required": false,
      "schema": {
        "default": 50,
        "title": "Num Tests",
        "type": "integer"
      }
    },
    {
      "in": "query",
      "name": "failure_rate",
      "required": false,
      "schema": {
        "default": 0.2,
        "title": "Failure Rate",
        "type": "number"
      }
    },
    {
      "in": "query",
      "name": "report_type",
      "required": false,
      "schema": {
        "default": "both",
        "enum": [
          "allure",
          "testng",
          "both"
        ],
        "title": "Report Type",
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
{'status': 'accepted', 'message': 'Synthetic test run generated and ingestion triggered.', 'project_id': project_id, 'build_number': build_number, 'files_uploaded': len(uploaded_files)}
```

Contract limit: at least one response has an unstructured schema. Read the linked handler/serializer for emitted fields; the empty schema is not a promise of an empty JSON object.
