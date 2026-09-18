# Test Execution Reviews API

[Documentation home](../../README.md) · [Regeneration](../../handoff/maintenance.md)

Generated from tracked source by `scripts/generate_handoff_reference.py`. Do not edit by hand. The baseline and validation limits are recorded in [verification](../../handoff/verification.md).

## DELETE `/api/v1/test-cases/{test_case_id}/review`

Clear Test Case Review

Drop the review row so the case reverts to ``pending_review``.

Source: [backend/app/routers/test_execution_reviews.py:117](../../../backend/app/routers/test_execution_reviews.py#L117).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`.

Declared Python handler arguments (includes exact role/guard options):

```python
test_case_id: uuid.UUID, db: AsyncSession=Depends(get_db), current_user: User=Depends(get_current_active_user)
```

Direct handler error branches (dependency/service errors can add others):

```python
HTTPException(status_code=404, detail='Test case not found')
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "clear_test_case_review_api_v1_test_cases__test_case_id__review_delete",
  "parameters": [
    {
      "in": "path",
      "name": "test_case_id",
      "required": true,
      "schema": {
        "format": "uuid",
        "title": "Test Case Id",
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
    "204": {
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

## GET `/api/v1/test-cases/{test_case_id}/review`

Get Test Case Review

Current review state for a test case, or ``null`` if no transition
has been recorded yet (the implicit ``pending_review`` initial state).

Returns 200 with ``null`` rather than 404 for the "no review yet"
case so test-run detail pages don't pepper the browser network
tab with red error rows on every page load. The frontend service
treats ``null`` and 404 identically — but 200/null keeps the tab
clean and removes a UI-test false positive. (Bug 2026-05-19.)

Source: [backend/app/routers/test_execution_reviews.py:61](../../../backend/app/routers/test_execution_reviews.py#L61).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`.

Declared Python handler arguments (includes exact role/guard options):

```python
test_case_id: uuid.UUID, db: AsyncSession=Depends(get_db), current_user: User=Depends(get_current_active_user)
```

Direct handler error branches (dependency/service errors can add others):

```python
HTTPException(status_code=404, detail='Test case not found')
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "get_test_case_review_api_v1_test_cases__test_case_id__review_get",
  "parameters": [
    {
      "in": "path",
      "name": "test_case_id",
      "required": true,
      "schema": {
        "format": "uuid",
        "title": "Test Case Id",
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
            "anyOf": [
              {
                "$ref": "#/components/schemas/TestExecutionReviewRead"
              },
              {
                "type": "null"
              }
            ],
            "title": "Response Get Test Case Review Api V1 Test Cases  Test Case Id  Review Get"
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

## PUT `/api/v1/test-cases/{test_case_id}/review`

Upsert Test Case Review

Transition the review state. The router resolves the project via
TestRun, enforces tenant access, then delegates to the service for
state-machine validation + write.

Source: [backend/app/routers/test_execution_reviews.py:90](../../../backend/app/routers/test_execution_reviews.py#L90).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`.

Declared Python handler arguments (includes exact role/guard options):

```python
test_case_id: uuid.UUID, payload: TestExecutionReviewUpdate, db: AsyncSession=Depends(get_db), current_user: User=Depends(get_current_active_user)
```

Direct handler error branches (dependency/service errors can add others):

```python
HTTPException(status_code=404, detail='Test case not found')
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "upsert_test_case_review_api_v1_test_cases__test_case_id__review_put",
  "parameters": [
    {
      "in": "path",
      "name": "test_case_id",
      "required": true,
      "schema": {
        "format": "uuid",
        "title": "Test Case Id",
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
  "requestBody": {
    "content": {
      "application/json": {
        "schema": {
          "$ref": "#/components/schemas/TestExecutionReviewUpdate"
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
            "$ref": "#/components/schemas/TestExecutionReviewRead"
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
