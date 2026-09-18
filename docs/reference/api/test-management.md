# Test Management API

[Documentation home](../../README.md) · [Regeneration](../../handoff/maintenance.md)

Generated from tracked source by `scripts/generate_handoff_reference.py`. Do not edit by hand. The baseline and validation limits are recorded in [verification](../../handoff/verification.md).

## GET `/api/v1/test-management/audit`

Get Audit Log



Source: [backend/app/routers/test_management_audit.py:19](../../../backend/app/routers/test_management_audit.py#L19).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`.

Declared Python handler arguments (includes exact role/guard options):

```python
project_id: Optional[uuid.UUID]=None, entity_type: str | None=None, action: str | None=None, page: int=Query(1, ge=1), size: int=Query(50, ge=1, le=200), db: AsyncSession=Depends(get_db), current_user: User=Depends(get_current_active_user)
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "get_audit_log_api_v1_test_management_audit_get",
  "parameters": [
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
      "name": "entity_type",
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
        "title": "Entity Type"
      }
    },
    {
      "in": "query",
      "name": "action",
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
        "title": "Action"
      }
    },
    {
      "in": "query",
      "name": "page",
      "required": false,
      "schema": {
        "default": 1,
        "minimum": 1,
        "title": "Page",
        "type": "integer"
      }
    },
    {
      "in": "query",
      "name": "size",
      "required": false,
      "schema": {
        "default": 50,
        "maximum": 200,
        "minimum": 1,
        "title": "Size",
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
            "$ref": "#/components/schemas/AuditLogListResponse"
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

## GET `/api/v1/test-management/cases`

List Test Cases



Source: [backend/app/routers/test_management_cases.py:73](../../../backend/app/routers/test_management_cases.py#L73).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`.

Declared Python handler arguments (includes exact role/guard options):

```python
project_id: Optional[uuid.UUID]=None, status: TestCaseLifecycleState | None=None, test_type: str | None=None, priority: str | None=None, feature_area: str | None=None, ai_generated: bool | None=None, search: str | None=None, suite_name: str | None=None, include_automation: bool=Query(False, description='When true, merge synthesised rows derived from per-run test_cases into the response so the Test Management page can surface automation-ingested tests alongside authored ones. Deduped by test_fingerprint — any fingerprint already linked to a managed_test_cases row is skipped.'), include_archived: bool=Query(False, description='Include archived authored cases when no exact status filter is set.'), page: int=Query(1, ge=1), size: int=Query(25, ge=1, le=200), db: AsyncSession=Depends(get_db), current_user: User=Depends(get_current_active_user)
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "list_test_cases_api_v1_test_management_cases_get",
  "parameters": [
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
            "$ref": "#/components/schemas/TestCaseLifecycleState"
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
      "name": "test_type",
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
        "title": "Test Type"
      }
    },
    {
      "in": "query",
      "name": "priority",
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
        "title": "Priority"
      }
    },
    {
      "in": "query",
      "name": "feature_area",
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
        "title": "Feature Area"
      }
    },
    {
      "in": "query",
      "name": "ai_generated",
      "required": false,
      "schema": {
        "anyOf": [
          {
            "type": "boolean"
          },
          {
            "type": "null"
          }
        ],
        "title": "Ai Generated"
      }
    },
    {
      "in": "query",
      "name": "search",
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
        "title": "Search"
      }
    },
    {
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
        "title": "Suite Name"
      }
    },
    {
      "description": "When true, merge synthesised rows derived from per-run test_cases into the response so the Test Management page can surface automation-ingested tests alongside authored ones. Deduped by test_fingerprint — any fingerprint already linked to a managed_test_cases row is skipped.",
      "in": "query",
      "name": "include_automation",
      "required": false,
      "schema": {
        "default": false,
        "description": "When true, merge synthesised rows derived from per-run test_cases into the response so the Test Management page can surface automation-ingested tests alongside authored ones. Deduped by test_fingerprint — any fingerprint already linked to a managed_test_cases row is skipped.",
        "title": "Include Automation",
        "type": "boolean"
      }
    },
    {
      "description": "Include archived authored cases when no exact status filter is set.",
      "in": "query",
      "name": "include_archived",
      "required": false,
      "schema": {
        "default": false,
        "description": "Include archived authored cases when no exact status filter is set.",
        "title": "Include Archived",
        "type": "boolean"
      }
    },
    {
      "in": "query",
      "name": "page",
      "required": false,
      "schema": {
        "default": 1,
        "minimum": 1,
        "title": "Page",
        "type": "integer"
      }
    },
    {
      "in": "query",
      "name": "size",
      "required": false,
      "schema": {
        "default": 25,
        "maximum": 200,
        "minimum": 1,
        "title": "Size",
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
            "$ref": "#/components/schemas/ManagedTestCaseListResponse"
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

## POST `/api/v1/test-management/cases`

Create Test Case



Source: [backend/app/routers/test_management_cases.py:199](../../../backend/app/routers/test_management_cases.py#L199).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`.

Declared Python handler arguments (includes exact role/guard options):

```python
payload: ManagedTestCaseCreate, db: AsyncSession=Depends(get_db), current_user: User=Depends(get_current_active_user)
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "create_test_case_api_v1_test_management_cases_post",
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
          "$ref": "#/components/schemas/ManagedTestCaseCreate"
        }
      }
    },
    "required": true
  },
  "responses": {
    "201": {
      "content": {
        "application/json": {
          "schema": {
            "$ref": "#/components/schemas/ManagedTestCaseResponse"
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

## POST `/api/v1/test-management/cases/ai-coverage`

Ai Coverage Analysis



Source: [backend/app/routers/test_management_ai.py:75](../../../backend/app/routers/test_management_ai.py#L75).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`.

Declared Python handler arguments (includes exact role/guard options):

```python
payload: AICoverageAnalysisRequest, db: AsyncSession=Depends(get_db), current_user: User=Depends(get_current_active_user)
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "ai_coverage_analysis_api_v1_test_management_cases_ai_coverage_post",
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
          "$ref": "#/components/schemas/AICoverageAnalysisRequest"
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
            "$ref": "#/components/schemas/AICoverageAnalysisResponse"
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

## POST `/api/v1/test-management/cases/ai-generate`

Ai Generate Cases



Source: [backend/app/routers/test_management_ai.py:31](../../../backend/app/routers/test_management_ai.py#L31).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`.

Declared Python handler arguments (includes exact role/guard options):

```python
payload: AIGenerateTestCasesRequest, db: AsyncSession=Depends(get_db), current_user: User=Depends(get_current_active_user)
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "ai_generate_cases_api_v1_test_management_cases_ai_generate_post",
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
          "$ref": "#/components/schemas/AIGenerateTestCasesRequest"
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
            "$ref": "#/components/schemas/AIGenerateTestCasesResponse"
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

## POST `/api/v1/test-management/cases/ai-generate/async`

Ai Generate Cases Async



Source: [backend/app/routers/test_management_ai.py:45](../../../backend/app/routers/test_management_ai.py#L45).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`.

Declared Python handler arguments (includes exact role/guard options):

```python
payload: AIGenerateTestCasesRequest, db: AsyncSession=Depends(get_db), current_user: User=Depends(get_current_active_user)
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "ai_generate_cases_async_api_v1_test_management_cases_ai_generate_async_post",
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
          "$ref": "#/components/schemas/AIGenerateTestCasesRequest"
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
            "$ref": "#/components/schemas/AITaskEnqueueResponse"
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

## GET `/api/v1/test-management/cases/ai-task/{task_id}`

Get Ai Task Status



Source: [backend/app/routers/test_management_ai.py:56](../../../backend/app/routers/test_management_ai.py#L56).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`.

Declared Python handler arguments (includes exact role/guard options):

```python
task_id: str, current_user: User=Depends(get_current_active_user)
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "get_ai_task_status_api_v1_test_management_cases_ai_task__task_id__get",
  "parameters": [
    {
      "in": "path",
      "name": "task_id",
      "required": true,
      "schema": {
        "title": "Task Id",
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
            "$ref": "#/components/schemas/AITaskStatusResponse"
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

## GET `/api/v1/test-management/cases/evidence-gaps`

Get Test Case Evidence Gaps



Source: [backend/app/routers/test_management_cases.py:212](../../../backend/app/routers/test_management_cases.py#L212).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`.

Declared Python handler arguments (includes exact role/guard options):

```python
kind: str=Query(..., pattern='^(never_executed|automation_vanished)$'), project_id: Optional[uuid.UUID]=Query(None), page: int=Query(1, ge=1), size: int=Query(25, ge=1, le=200), db: AsyncSession=Depends(get_db), current_user: User=Depends(get_current_active_user)
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "get_test_case_evidence_gaps_api_v1_test_management_cases_evidence_gaps_get",
  "parameters": [
    {
      "in": "query",
      "name": "kind",
      "required": true,
      "schema": {
        "pattern": "^(never_executed|automation_vanished)$",
        "title": "Kind",
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
      "in": "query",
      "name": "page",
      "required": false,
      "schema": {
        "default": 1,
        "minimum": 1,
        "title": "Page",
        "type": "integer"
      }
    },
    {
      "in": "query",
      "name": "size",
      "required": false,
      "schema": {
        "default": 25,
        "maximum": 200,
        "minimum": 1,
        "title": "Size",
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
            "$ref": "#/components/schemas/EvidenceGapListResponse"
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

## GET `/api/v1/test-management/cases/export/excel`

Export Test Cases Excel

Export test cases to an Excel (.xlsx) file.

Source: [backend/app/routers/test_management_exports.py:61](../../../backend/app/routers/test_management_exports.py#L61).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`.

Declared Python handler arguments (includes exact role/guard options):

```python
project_id: Optional[uuid.UUID]=None, status: Optional[str]=None, test_type: Optional[str]=None, priority: Optional[str]=None, search: Optional[str]=None, db: AsyncSession=Depends(get_db), current_user: User=Depends(get_current_active_user)
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "export_test_cases_excel_api_v1_test_management_cases_export_excel_get",
  "parameters": [
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
      "name": "test_type",
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
        "title": "Test Type"
      }
    },
    {
      "in": "query",
      "name": "priority",
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
        "title": "Priority"
      }
    },
    {
      "in": "query",
      "name": "search",
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
        "title": "Search"
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
Response(content=b'', media_type='application/octet-stream')
Response(content=buf.getvalue(), media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', headers={'Content-Disposition': 'attachment; filename=test-cases.xlsx'})
```

Contract limit: at least one response has an unstructured schema. Read the linked handler/serializer for emitted fields; the empty schema is not a promise of an empty JSON object.

## DELETE `/api/v1/test-management/cases/{case_id}`

Deprecate Test Case



Source: [backend/app/routers/test_management_cases.py:270](../../../backend/app/routers/test_management_cases.py#L270).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_case_access`.

Declared Python handler arguments (includes exact role/guard options):

```python
case_id: uuid.UUID, payload: Optional[TestCaseDeprecateRequest]=Body(None), db: AsyncSession=Depends(get_db), current_user: User=Depends(get_current_active_user), _case=Depends(require_case_access)
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "deprecate_test_case_api_v1_test_management_cases__case_id__delete",
  "parameters": [
    {
      "in": "path",
      "name": "case_id",
      "required": true,
      "schema": {
        "format": "uuid",
        "title": "Case Id",
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
          "anyOf": [
            {
              "$ref": "#/components/schemas/TestCaseDeprecateRequest"
            },
            {
              "type": "null"
            }
          ],
          "title": "Payload"
        }
      }
    }
  },
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

## GET `/api/v1/test-management/cases/{case_id}`

Get Test Case



Source: [backend/app/routers/test_management_cases.py:243](../../../backend/app/routers/test_management_cases.py#L243).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_case_access`.

Declared Python handler arguments (includes exact role/guard options):

```python
case_id: uuid.UUID, db: AsyncSession=Depends(get_db), current_user: User=Depends(get_current_active_user), _case=Depends(require_case_access)
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "get_test_case_api_v1_test_management_cases__case_id__get",
  "parameters": [
    {
      "in": "path",
      "name": "case_id",
      "required": true,
      "schema": {
        "format": "uuid",
        "title": "Case Id",
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
            "$ref": "#/components/schemas/ManagedTestCaseResponse"
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

## PATCH `/api/v1/test-management/cases/{case_id}`

Update Test Case



Source: [backend/app/routers/test_management_cases.py:255](../../../backend/app/routers/test_management_cases.py#L255).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_case_access`.

Declared Python handler arguments (includes exact role/guard options):

```python
case_id: uuid.UUID, payload: ManagedTestCaseUpdate, db: AsyncSession=Depends(get_db), current_user: User=Depends(get_current_active_user), _case=Depends(require_case_access)
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "update_test_case_api_v1_test_management_cases__case_id__patch",
  "parameters": [
    {
      "in": "path",
      "name": "case_id",
      "required": true,
      "schema": {
        "format": "uuid",
        "title": "Case Id",
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
          "$ref": "#/components/schemas/ManagedTestCaseUpdate"
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
            "$ref": "#/components/schemas/ManagedTestCaseResponse"
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

## POST `/api/v1/test-management/cases/{case_id}/ai-review`

Ai Review Case



Source: [backend/app/routers/test_management_ai.py:64](../../../backend/app/routers/test_management_ai.py#L64).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`.

Declared Python handler arguments (includes exact role/guard options):

```python
case_id: uuid.UUID, db: AsyncSession=Depends(get_db), current_user: User=Depends(get_current_active_user)
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "ai_review_case_api_v1_test_management_cases__case_id__ai_review_post",
  "parameters": [
    {
      "in": "path",
      "name": "case_id",
      "required": true,
      "schema": {
        "format": "uuid",
        "title": "Case Id",
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
            "$ref": "#/components/schemas/AIReviewTestCaseResponse"
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

## GET `/api/v1/test-management/cases/{case_id}/allowed-transitions`

Get Allowed Transitions



Source: [backend/app/routers/test_management_cases.py:334](../../../backend/app/routers/test_management_cases.py#L334).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_case_access`.

Declared Python handler arguments (includes exact role/guard options):

```python
case_id: uuid.UUID, db: AsyncSession=Depends(get_db), current_user: User=Depends(get_current_active_user), _case: ManagedTestCase=Depends(require_case_access)
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "get_allowed_transitions_api_v1_test_management_cases__case_id__allowed_transitions_get",
  "parameters": [
    {
      "in": "path",
      "name": "case_id",
      "required": true,
      "schema": {
        "format": "uuid",
        "title": "Case Id",
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
              "$ref": "#/components/schemas/AllowedTransitionResponse"
            },
            "title": "Response Get Allowed Transitions Api V1 Test Management Cases  Case Id  Allowed Transitions Get",
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

## GET `/api/v1/test-management/cases/{case_id}/comments`

List Comments



Source: [backend/app/routers/test_management_cases.py:374](../../../backend/app/routers/test_management_cases.py#L374).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_case_access`.

Declared Python handler arguments (includes exact role/guard options):

```python
case_id: uuid.UUID, db: AsyncSession=Depends(get_db), current_user: User=Depends(get_current_active_user), _case=Depends(require_case_access)
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "list_comments_api_v1_test_management_cases__case_id__comments_get",
  "parameters": [
    {
      "in": "path",
      "name": "case_id",
      "required": true,
      "schema": {
        "format": "uuid",
        "title": "Case Id",
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
              "$ref": "#/components/schemas/TestCaseCommentResponse"
            },
            "title": "Response List Comments Api V1 Test Management Cases  Case Id  Comments Get",
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

## POST `/api/v1/test-management/cases/{case_id}/comments`

Add Comment



Source: [backend/app/routers/test_management_cases.py:385](../../../backend/app/routers/test_management_cases.py#L385).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_case_access`.

Declared Python handler arguments (includes exact role/guard options):

```python
case_id: uuid.UUID, payload: TestCaseCommentCreate, db: AsyncSession=Depends(get_db), current_user: User=Depends(get_current_active_user), _case=Depends(require_case_access)
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "add_comment_api_v1_test_management_cases__case_id__comments_post",
  "parameters": [
    {
      "in": "path",
      "name": "case_id",
      "required": true,
      "schema": {
        "format": "uuid",
        "title": "Case Id",
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
          "$ref": "#/components/schemas/TestCaseCommentCreate"
        }
      }
    },
    "required": true
  },
  "responses": {
    "201": {
      "content": {
        "application/json": {
          "schema": {
            "$ref": "#/components/schemas/TestCaseCommentResponse"
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

## GET `/api/v1/test-management/cases/{case_id}/history`

Get Test Case History



Source: [backend/app/routers/test_management_cases.py:285](../../../backend/app/routers/test_management_cases.py#L285).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_case_access`.

Declared Python handler arguments (includes exact role/guard options):

```python
case_id: uuid.UUID, db: AsyncSession=Depends(get_db), current_user: User=Depends(get_current_active_user), _case=Depends(require_case_access)
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "get_test_case_history_api_v1_test_management_cases__case_id__history_get",
  "parameters": [
    {
      "in": "path",
      "name": "case_id",
      "required": true,
      "schema": {
        "format": "uuid",
        "title": "Case Id",
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
              "$ref": "#/components/schemas/TestCaseVersionResponse"
            },
            "title": "Response Get Test Case History Api V1 Test Management Cases  Case Id  History Get",
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

## POST `/api/v1/test-management/cases/{case_id}/request-review`

Request Review



Source: [backend/app/routers/test_management_cases.py:296](../../../backend/app/routers/test_management_cases.py#L296).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_case_access_for_review_target`.

Declared Python handler arguments (includes exact role/guard options):

```python
case_id: uuid.UUID, db: AsyncSession=Depends(get_db), current_user: User=Depends(get_current_active_user), _case=Depends(require_case_access_for_review_target)
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "request_review_api_v1_test_management_cases__case_id__request_review_post",
  "parameters": [
    {
      "in": "path",
      "name": "case_id",
      "required": true,
      "schema": {
        "format": "uuid",
        "title": "Case Id",
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
            "$ref": "#/components/schemas/TestCaseReviewResponse"
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

## POST `/api/v1/test-management/cases/{case_id}/review-action`

Review Action



Source: [backend/app/routers/test_management_cases.py:348](../../../backend/app/routers/test_management_cases.py#L348).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_case_access`.

Declared Python handler arguments (includes exact role/guard options):

```python
case_id: uuid.UUID, payload: ReviewActionRequest, db: AsyncSession=Depends(get_db), current_user: User=Depends(get_current_active_user), _case=Depends(require_case_access)
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "review_action_api_v1_test_management_cases__case_id__review_action_post",
  "parameters": [
    {
      "in": "path",
      "name": "case_id",
      "required": true,
      "schema": {
        "format": "uuid",
        "title": "Case Id",
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
          "$ref": "#/components/schemas/ReviewActionRequest"
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
            "$ref": "#/components/schemas/ManagedTestCaseResponse"
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

## GET `/api/v1/test-management/cases/{case_id}/reviews`

Get Reviews



Source: [backend/app/routers/test_management_cases.py:363](../../../backend/app/routers/test_management_cases.py#L363).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_case_access`.

Declared Python handler arguments (includes exact role/guard options):

```python
case_id: uuid.UUID, db: AsyncSession=Depends(get_db), current_user: User=Depends(get_current_active_user), _case=Depends(require_case_access)
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "get_reviews_api_v1_test_management_cases__case_id__reviews_get",
  "parameters": [
    {
      "in": "path",
      "name": "case_id",
      "required": true,
      "schema": {
        "format": "uuid",
        "title": "Case Id",
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
              "$ref": "#/components/schemas/TestCaseReviewResponse"
            },
            "title": "Response Get Reviews Api V1 Test Management Cases  Case Id  Reviews Get",
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

## POST `/api/v1/test-management/cases/{case_id}/transition`

Transition Test Case



Source: [backend/app/routers/test_management_cases.py:310](../../../backend/app/routers/test_management_cases.py#L310).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_case_access`.

Declared Python handler arguments (includes exact role/guard options):

```python
case_id: uuid.UUID, payload: TestCaseTransitionRequest, db: AsyncSession=Depends(get_db), current_user: User=Depends(get_current_active_user), _case: ManagedTestCase=Depends(require_case_access)
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "transition_test_case_api_v1_test_management_cases__case_id__transition_post",
  "parameters": [
    {
      "in": "path",
      "name": "case_id",
      "required": true,
      "schema": {
        "format": "uuid",
        "title": "Case Id",
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
          "$ref": "#/components/schemas/TestCaseTransitionRequest"
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
            "$ref": "#/components/schemas/ManagedTestCaseResponse"
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

## GET `/api/v1/test-management/plans`

List Plans



Source: [backend/app/routers/test_management_plans.py:40](../../../backend/app/routers/test_management_plans.py#L40).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`.

Declared Python handler arguments (includes exact role/guard options):

```python
project_id: Optional[uuid.UUID]=None, status: str | None=None, page: int=Query(1, ge=1), size: int=Query(20, ge=1, le=100), db: AsyncSession=Depends(get_db), current_user: User=Depends(get_current_active_user)
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "list_plans_api_v1_test_management_plans_get",
  "parameters": [
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
      "name": "page",
      "required": false,
      "schema": {
        "default": 1,
        "minimum": 1,
        "title": "Page",
        "type": "integer"
      }
    },
    {
      "in": "query",
      "name": "size",
      "required": false,
      "schema": {
        "default": 20,
        "maximum": 100,
        "minimum": 1,
        "title": "Size",
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
            "$ref": "#/components/schemas/TestPlanListResponse"
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

## POST `/api/v1/test-management/plans`

Create Plan



Source: [backend/app/routers/test_management_plans.py:66](../../../backend/app/routers/test_management_plans.py#L66).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`.

Declared Python handler arguments (includes exact role/guard options):

```python
payload: TestPlanCreate, db: AsyncSession=Depends(get_db), current_user: User=Depends(get_current_active_user)
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "create_plan_api_v1_test_management_plans_post",
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
          "$ref": "#/components/schemas/TestPlanCreate"
        }
      }
    },
    "required": true
  },
  "responses": {
    "201": {
      "content": {
        "application/json": {
          "schema": {
            "$ref": "#/components/schemas/TestPlanResponse"
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

## POST `/api/v1/test-management/plans/ai-create`

Ai Create Plan



Source: [backend/app/routers/test_management_plans.py:163](../../../backend/app/routers/test_management_plans.py#L163).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`.

Declared Python handler arguments (includes exact role/guard options):

```python
payload: AIOptimizePlanRequest, db: AsyncSession=Depends(get_db), current_user: User=Depends(get_current_active_user)
```

Direct handler error branches (dependency/service errors can add others):

```python
HTTPException(status_code=400, detail='No approved test cases found for this project')
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "ai_create_plan_api_v1_test_management_plans_ai_create_post",
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
          "$ref": "#/components/schemas/AIOptimizePlanRequest"
        }
      }
    },
    "required": true
  },
  "responses": {
    "201": {
      "content": {
        "application/json": {
          "schema": {
            "$ref": "#/components/schemas/TestPlanResponse"
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

## POST `/api/v1/test-management/plans/ai-create/async`

Ai Create Plan Async



Source: [backend/app/routers/test_management_plans.py:213](../../../backend/app/routers/test_management_plans.py#L213).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`.

Declared Python handler arguments (includes exact role/guard options):

```python
payload: AIOptimizePlanRequest, db: AsyncSession=Depends(get_db), current_user: User=Depends(get_current_active_user)
```

Direct handler error branches (dependency/service errors can add others):

```python
HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail='AI task queue unavailable. Check that the Celery worker and Redis are running.')
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "ai_create_plan_async_api_v1_test_management_plans_ai_create_async_post",
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
          "$ref": "#/components/schemas/AIOptimizePlanRequest"
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
            "$ref": "#/components/schemas/AITaskEnqueueResponse"
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

## GET `/api/v1/test-management/plans/{plan_id}`

Get Plan



Source: [backend/app/routers/test_management_plans.py:78](../../../backend/app/routers/test_management_plans.py#L78).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_plan_access`.

Declared Python handler arguments (includes exact role/guard options):

```python
plan_id: uuid.UUID, db: AsyncSession=Depends(get_db), current_user: User=Depends(get_current_active_user), _plan=Depends(require_plan_access)
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "get_plan_api_v1_test_management_plans__plan_id__get",
  "parameters": [
    {
      "in": "path",
      "name": "plan_id",
      "required": true,
      "schema": {
        "format": "uuid",
        "title": "Plan Id",
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
            "$ref": "#/components/schemas/TestPlanResponse"
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

## PATCH `/api/v1/test-management/plans/{plan_id}`

Update Plan



Source: [backend/app/routers/test_management_plans.py:88](../../../backend/app/routers/test_management_plans.py#L88).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_plan_access`.

Declared Python handler arguments (includes exact role/guard options):

```python
plan_id: uuid.UUID, payload: TestPlanUpdate, db: AsyncSession=Depends(get_db), current_user: User=Depends(get_current_active_user), _plan=Depends(require_plan_access)
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "update_plan_api_v1_test_management_plans__plan_id__patch",
  "parameters": [
    {
      "in": "path",
      "name": "plan_id",
      "required": true,
      "schema": {
        "format": "uuid",
        "title": "Plan Id",
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
          "$ref": "#/components/schemas/TestPlanUpdate"
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
            "$ref": "#/components/schemas/TestPlanResponse"
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

## GET `/api/v1/test-management/plans/{plan_id}/export/pdf`

Export Test Plan Pdf

Export a test plan to a PDF document.

Source: [backend/app/routers/test_management_exports.py:367](../../../backend/app/routers/test_management_exports.py#L367).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`.

Declared Python handler arguments (includes exact role/guard options):

```python
plan_id: uuid.UUID, db: AsyncSession=Depends(get_db), current_user: User=Depends(get_current_active_user)
```

Direct handler error branches (dependency/service errors can add others):

```python
HTTPException(status_code=404, detail='Test plan not found')
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "export_test_plan_pdf_api_v1_test_management_plans__plan_id__export_pdf_get",
  "parameters": [
    {
      "in": "path",
      "name": "plan_id",
      "required": true,
      "schema": {
        "format": "uuid",
        "title": "Plan Id",
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
StreamingResponse(buf, media_type='application/pdf', headers={'Content-Disposition': f'attachment; filename=test-plan-{plan_id}.pdf'})
```

Contract limit: at least one response has an unstructured schema. Read the linked handler/serializer for emitted fields; the empty schema is not a promise of an empty JSON object.

## GET `/api/v1/test-management/plans/{plan_id}/export/word`

Export Test Plan Word

Export a test plan to a Word (.docx) document.

Source: [backend/app/routers/test_management_exports.py:272](../../../backend/app/routers/test_management_exports.py#L272).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`.

Declared Python handler arguments (includes exact role/guard options):

```python
plan_id: uuid.UUID, db: AsyncSession=Depends(get_db), current_user: User=Depends(get_current_active_user)
```

Direct handler error branches (dependency/service errors can add others):

```python
HTTPException(status_code=404, detail='Test plan not found')
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "export_test_plan_word_api_v1_test_management_plans__plan_id__export_word_get",
  "parameters": [
    {
      "in": "path",
      "name": "plan_id",
      "required": true,
      "schema": {
        "format": "uuid",
        "title": "Plan Id",
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
StreamingResponse(buf, media_type='application/vnd.openxmlformats-officedocument.wordprocessingml.document', headers={'Content-Disposition': f'attachment; filename=test-plan-{plan_id}.docx'})
```

Contract limit: at least one response has an unstructured schema. Read the linked handler/serializer for emitted fields; the empty schema is not a promise of an empty JSON object.

## GET `/api/v1/test-management/plans/{plan_id}/items`

List Plan Items



Source: [backend/app/routers/test_management_plans.py:102](../../../backend/app/routers/test_management_plans.py#L102).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_plan_access`.

Declared Python handler arguments (includes exact role/guard options):

```python
plan_id: uuid.UUID, db: AsyncSession=Depends(get_db), current_user: User=Depends(get_current_active_user), _plan=Depends(require_plan_access)
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "list_plan_items_api_v1_test_management_plans__plan_id__items_get",
  "parameters": [
    {
      "in": "path",
      "name": "plan_id",
      "required": true,
      "schema": {
        "format": "uuid",
        "title": "Plan Id",
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
              "$ref": "#/components/schemas/TestPlanItemResponse"
            },
            "title": "Response List Plan Items Api V1 Test Management Plans  Plan Id  Items Get",
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

## POST `/api/v1/test-management/plans/{plan_id}/items`

Add Plan Item



Source: [backend/app/routers/test_management_plans.py:114](../../../backend/app/routers/test_management_plans.py#L114).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_plan_access`.

Declared Python handler arguments (includes exact role/guard options):

```python
plan_id: uuid.UUID, payload: TestPlanItemCreate, db: AsyncSession=Depends(get_db), current_user: User=Depends(get_current_active_user), _plan=Depends(require_plan_access)
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "add_plan_item_api_v1_test_management_plans__plan_id__items_post",
  "parameters": [
    {
      "in": "path",
      "name": "plan_id",
      "required": true,
      "schema": {
        "format": "uuid",
        "title": "Plan Id",
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
          "$ref": "#/components/schemas/TestPlanItemCreate"
        }
      }
    },
    "required": true
  },
  "responses": {
    "201": {
      "content": {
        "application/json": {
          "schema": {
            "$ref": "#/components/schemas/TestPlanItemResponse"
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

## DELETE `/api/v1/test-management/plans/{plan_id}/items/{item_id}`

Remove Plan Item



Source: [backend/app/routers/test_management_plans.py:128](../../../backend/app/routers/test_management_plans.py#L128).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_plan_access`.

Declared Python handler arguments (includes exact role/guard options):

```python
plan_id: uuid.UUID, item_id: uuid.UUID, db: AsyncSession=Depends(get_db), current_user: User=Depends(get_current_active_user), _plan=Depends(require_plan_access)
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "remove_plan_item_api_v1_test_management_plans__plan_id__items__item_id__delete",
  "parameters": [
    {
      "in": "path",
      "name": "plan_id",
      "required": true,
      "schema": {
        "format": "uuid",
        "title": "Plan Id",
        "type": "string"
      }
    },
    {
      "in": "path",
      "name": "item_id",
      "required": true,
      "schema": {
        "format": "uuid",
        "title": "Item Id",
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

## PATCH `/api/v1/test-management/plans/{plan_id}/items/{item_id}/execute`

Record Execution



Source: [backend/app/routers/test_management_plans.py:140](../../../backend/app/routers/test_management_plans.py#L140).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_plan_access`.

Declared Python handler arguments (includes exact role/guard options):

```python
plan_id: uuid.UUID, item_id: uuid.UUID, payload: ExecuteTestPlanItemRequest, db: AsyncSession=Depends(get_db), current_user: User=Depends(get_current_active_user), _plan=Depends(require_plan_access)
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "record_execution_api_v1_test_management_plans__plan_id__items__item_id__execute_patch",
  "parameters": [
    {
      "in": "path",
      "name": "plan_id",
      "required": true,
      "schema": {
        "format": "uuid",
        "title": "Plan Id",
        "type": "string"
      }
    },
    {
      "in": "path",
      "name": "item_id",
      "required": true,
      "schema": {
        "format": "uuid",
        "title": "Item Id",
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
          "$ref": "#/components/schemas/ExecuteTestPlanItemRequest"
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
            "$ref": "#/components/schemas/TestPlanItemResponse"
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

## GET `/api/v1/test-management/strategies`

List Strategies



Source: [backend/app/routers/test_management_strategies.py:30](../../../backend/app/routers/test_management_strategies.py#L30).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`.

Declared Python handler arguments (includes exact role/guard options):

```python
project_id: Optional[uuid.UUID]=None, db: AsyncSession=Depends(get_db), current_user: User=Depends(get_current_active_user)
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "list_strategies_api_v1_test_management_strategies_get",
  "parameters": [
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
              "$ref": "#/components/schemas/TestStrategyResponse"
            },
            "title": "Response List Strategies Api V1 Test Management Strategies Get",
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

## POST `/api/v1/test-management/strategies/ai-generate`

Ai Generate Strategy



Source: [backend/app/routers/test_management_strategies.py:53](../../../backend/app/routers/test_management_strategies.py#L53).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`.

Declared Python handler arguments (includes exact role/guard options):

```python
payload: AIGenerateStrategyRequest, db: AsyncSession=Depends(get_db), current_user: User=Depends(get_current_active_user)
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "ai_generate_strategy_api_v1_test_management_strategies_ai_generate_post",
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
          "$ref": "#/components/schemas/AIGenerateStrategyRequest"
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
            "$ref": "#/components/schemas/TestStrategyResponse"
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

## POST `/api/v1/test-management/strategies/ai-generate/async`

Ai Generate Strategy Async



Source: [backend/app/routers/test_management_strategies.py:68](../../../backend/app/routers/test_management_strategies.py#L68).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`.

Declared Python handler arguments (includes exact role/guard options):

```python
payload: AIGenerateStrategyRequest, db: AsyncSession=Depends(get_db), current_user: User=Depends(get_current_active_user)
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "ai_generate_strategy_async_api_v1_test_management_strategies_ai_generate_async_post",
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
          "$ref": "#/components/schemas/AIGenerateStrategyRequest"
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
            "$ref": "#/components/schemas/AITaskEnqueueResponse"
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

## GET `/api/v1/test-management/strategies/{strategy_id}`

Get Strategy



Source: [backend/app/routers/test_management_strategies.py:79](../../../backend/app/routers/test_management_strategies.py#L79).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`.

Declared Python handler arguments (includes exact role/guard options):

```python
strategy_id: uuid.UUID, db: AsyncSession=Depends(get_db), current_user: User=Depends(get_current_active_user)
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "get_strategy_api_v1_test_management_strategies__strategy_id__get",
  "parameters": [
    {
      "in": "path",
      "name": "strategy_id",
      "required": true,
      "schema": {
        "format": "uuid",
        "title": "Strategy Id",
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
            "$ref": "#/components/schemas/TestStrategyResponse"
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

## PUT `/api/v1/test-management/strategies/{strategy_id}`

Update Strategy



Source: [backend/app/routers/test_management_strategies.py:95](../../../backend/app/routers/test_management_strategies.py#L95).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`.

Declared Python handler arguments (includes exact role/guard options):

```python
strategy_id: uuid.UUID, payload: TestStrategyUpdate, db: AsyncSession=Depends(get_db), current_user: User=Depends(get_current_active_user)
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "update_strategy_api_v1_test_management_strategies__strategy_id__put",
  "parameters": [
    {
      "in": "path",
      "name": "strategy_id",
      "required": true,
      "schema": {
        "format": "uuid",
        "title": "Strategy Id",
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
          "$ref": "#/components/schemas/TestStrategyUpdate"
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
            "$ref": "#/components/schemas/TestStrategyResponse"
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

## GET `/api/v1/test-management/strategies/{strategy_id}/export/pdf`

Export Test Strategy Pdf

Export a test strategy to a PDF document.

Source: [backend/app/routers/test_management_exports.py:598](../../../backend/app/routers/test_management_exports.py#L598).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`.

Declared Python handler arguments (includes exact role/guard options):

```python
strategy_id: uuid.UUID, db: AsyncSession=Depends(get_db), current_user: User=Depends(get_current_active_user)
```

Direct handler error branches (dependency/service errors can add others):

```python
HTTPException(status_code=404, detail='Test strategy not found')
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "export_test_strategy_pdf_api_v1_test_management_strategies__strategy_id__export_pdf_get",
  "parameters": [
    {
      "in": "path",
      "name": "strategy_id",
      "required": true,
      "schema": {
        "format": "uuid",
        "title": "Strategy Id",
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
StreamingResponse(buf, media_type='application/pdf', headers={'Content-Disposition': f'attachment; filename=test-strategy-{strategy_id}.pdf'})
```

Contract limit: at least one response has an unstructured schema. Read the linked handler/serializer for emitted fields; the empty schema is not a promise of an empty JSON object.

## GET `/api/v1/test-management/strategies/{strategy_id}/export/word`

Export Test Strategy Word

Export a test strategy to a Word (.docx) document.

Source: [backend/app/routers/test_management_exports.py:486](../../../backend/app/routers/test_management_exports.py#L486).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`.

Declared Python handler arguments (includes exact role/guard options):

```python
strategy_id: uuid.UUID, db: AsyncSession=Depends(get_db), current_user: User=Depends(get_current_active_user)
```

Direct handler error branches (dependency/service errors can add others):

```python
HTTPException(status_code=404, detail='Test strategy not found')
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "export_test_strategy_word_api_v1_test_management_strategies__strategy_id__export_word_get",
  "parameters": [
    {
      "in": "path",
      "name": "strategy_id",
      "required": true,
      "schema": {
        "format": "uuid",
        "title": "Strategy Id",
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
StreamingResponse(buf, media_type='application/vnd.openxmlformats-officedocument.wordprocessingml.document', headers={'Content-Disposition': f'attachment; filename=test-strategy-{strategy_id}.docx'})
```

Contract limit: at least one response has an unstructured schema. Read the linked handler/serializer for emitted fields; the empty schema is not a promise of an empty JSON object.

## GET `/api/v1/test-management/suite-owners`

List Suite Owners

Resolve owners for a project's suites.

With ``suite_name`` set, returns a single-item list (or an empty list
when neither an explicit owner nor a project manager is configured —
callers should not rely on a 404 for the empty case so the UI can show
a neutral "Unassigned" badge).

Source: [backend/app/routers/test_management_suite_reviews.py:60](../../../backend/app/routers/test_management_suite_reviews.py#L60).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`.

Declared Python handler arguments (includes exact role/guard options):

```python
project_id: uuid.UUID=Query(...), suite_name: Optional[str]=Query(None, description='If set, returns the single suite owner.'), db: AsyncSession=Depends(get_db), current_user: User=Depends(get_current_active_user)
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "list_suite_owners_api_v1_test_management_suite_owners_get",
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
      "description": "If set, returns the single suite owner.",
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
        "description": "If set, returns the single suite owner.",
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
            "items": {
              "$ref": "#/components/schemas/SuiteOwnerResponse"
            },
            "title": "Response List Suite Owners Api V1 Test Management Suite Owners Get",
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

## PUT `/api/v1/test-management/suite-owners/{suite_name}`

Set Suite Owner

Assign or clear (``owner_user_id=null``) the suite's explicit owner.

Source: [backend/app/routers/test_management_suite_reviews.py:128](../../../backend/app/routers/test_management_suite_reviews.py#L128).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_role.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
suite_name: str, payload: SuiteOwnerUpdate, project_id: uuid.UUID=Query(...), db: AsyncSession=Depends(get_db), current_user: User=Depends(get_current_active_user)
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "set_suite_owner_api_v1_test_management_suite_owners__suite_name__put",
  "parameters": [
    {
      "in": "path",
      "name": "suite_name",
      "required": true,
      "schema": {
        "title": "Suite Name",
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
  "requestBody": {
    "content": {
      "application/json": {
        "schema": {
          "$ref": "#/components/schemas/SuiteOwnerUpdate"
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
            "$ref": "#/components/schemas/SuiteOwnerResponse"
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

## GET `/api/v1/test-management/suite-reviews`

List Suite Reviews



Source: [backend/app/routers/test_management_suite_reviews.py:177](../../../backend/app/routers/test_management_suite_reviews.py#L177).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`.

Declared Python handler arguments (includes exact role/guard options):

```python
project_id: uuid.UUID=Query(...), suite_name: Optional[str]=Query(None), state: Optional[str]=Query(None, pattern='^(pending|confirmed|acknowledged|review_later)$'), test_run_id: Optional[uuid.UUID]=Query(None), reviewer_user_id: Optional[uuid.UUID]=Query(None), limit: int=Query(200, ge=1, le=500), db: AsyncSession=Depends(get_db), current_user: User=Depends(get_current_active_user)
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "list_suite_reviews_api_v1_test_management_suite_reviews_get",
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
        "title": "Suite Name"
      }
    },
    {
      "in": "query",
      "name": "state",
      "required": false,
      "schema": {
        "anyOf": [
          {
            "pattern": "^(pending|confirmed|acknowledged|review_later)$",
            "type": "string"
          },
          {
            "type": "null"
          }
        ],
        "title": "State"
      }
    },
    {
      "in": "query",
      "name": "test_run_id",
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
        "title": "Test Run Id"
      }
    },
    {
      "in": "query",
      "name": "reviewer_user_id",
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
        "title": "Reviewer User Id"
      }
    },
    {
      "in": "query",
      "name": "limit",
      "required": false,
      "schema": {
        "default": 200,
        "maximum": 500,
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
              "$ref": "#/components/schemas/SuiteReviewResponse"
            },
            "title": "Response List Suite Reviews Api V1 Test Management Suite Reviews Get",
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

## GET `/api/v1/test-management/suite-reviews/by-run/{test_run_id}`

List Reviews For Run

All suite reviews for a single run (used by the run-detail page).

Source: [backend/app/routers/test_management_suite_reviews.py:204](../../../backend/app/routers/test_management_suite_reviews.py#L204).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`.

Declared Python handler arguments (includes exact role/guard options):

```python
test_run_id: uuid.UUID, db: AsyncSession=Depends(get_db), current_user: User=Depends(get_current_active_user)
```

Direct handler error branches (dependency/service errors can add others):

```python
HTTPException(status_code=404, detail='Test run not found')
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "list_reviews_for_run_api_v1_test_management_suite_reviews_by_run__test_run_id__get",
  "parameters": [
    {
      "in": "path",
      "name": "test_run_id",
      "required": true,
      "schema": {
        "format": "uuid",
        "title": "Test Run Id",
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
              "$ref": "#/components/schemas/SuiteReviewResponse"
            },
            "title": "Response List Reviews For Run Api V1 Test Management Suite Reviews By Run  Test Run Id  Get",
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

## PUT `/api/v1/test-management/suite-reviews/by-run/{test_run_id}/{suite_name}`

Upsert Review For Run Suite

Set the review state for (run, suite). Creates the row on first call.

Non-gating: the AI pipeline already wrote ``AIAnalysis`` rows for this
run independently. This call records the human verdict on top.

Source: [backend/app/routers/test_management_suite_reviews.py:225](../../../backend/app/routers/test_management_suite_reviews.py#L225).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`.

Declared Python handler arguments (includes exact role/guard options):

```python
test_run_id: uuid.UUID, suite_name: str, payload: SuiteReviewUpdate, db: AsyncSession=Depends(get_db), current_user: User=Depends(get_current_active_user)
```

Direct handler error branches (dependency/service errors can add others):

```python
HTTPException(status_code=404, detail='Test run not found')
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "upsert_review_for_run_suite_api_v1_test_management_suite_reviews_by_run__test_run_id___suite_name__put",
  "parameters": [
    {
      "in": "path",
      "name": "test_run_id",
      "required": true,
      "schema": {
        "format": "uuid",
        "title": "Test Run Id",
        "type": "string"
      }
    },
    {
      "in": "path",
      "name": "suite_name",
      "required": true,
      "schema": {
        "title": "Suite Name",
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
          "$ref": "#/components/schemas/SuiteReviewUpdate"
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
            "$ref": "#/components/schemas/SuiteReviewResponse"
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

## GET `/api/v1/test-management/suites`

List Test Suites

Return test suites grouped by suite_name, combining automation test_cases
(from ingested runs) and manually authored managed_test_cases.

Source: [backend/app/routers/test_management_exports.py:703](../../../backend/app/routers/test_management_exports.py#L703).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`.

Declared Python handler arguments (includes exact role/guard options):

```python
project_id: Optional[uuid.UUID]=Query(None), db: AsyncSession=Depends(get_db), current_user: User=Depends(get_current_active_user)
```

Direct handler error branches (dependency/service errors can add others):

```python
HTTPException(status_code=403, detail='You do not have access to this project')
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "list_test_suites_api_v1_test_management_suites_get",
  "parameters": [
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
[]
[{'suite_name': s['suite_name'], 'test_count': s['test_count'], 'passed_count': s['passed_count'], 'failed_count': s['failed_count'], 'last_run_at': s['last_run_at'].isoformat() if s['last_run_at'] else None, 'last_run_id': str(s['last_run_id']) if s['last_run_id'] else None, 'pass_rate': round(s['passed_count'] / s['test_count'] * 100, 1) if s['test_count'] > 0 else None, 'run_count': history_map.get(s['suite_name'], {}).get('run_count', 0), 'total_executions': history_map.get(s['suite_name'], {}).get('total_tests', 0), 'total_passed': history_map.get(s['suite_name'], {}).get('passed_count', 0), 'total_failed': history_map.get(s['suite_name'], {}).get('failed_count', 0), 'total_skipped': history_map.get(s['suite_name'], {}).get('skipped_count', 0), 'total_broken': history_map.get(s['suite_name'], {}).get('broken_count', 0), 'owner_user_id': owner_map.get(s['suite_name'], {}).get('owner_user_id'), 'owner_email': owner_map.get(s['suite_name'], {}).get('owner_email'), 'owner_full_name': owner_map.get(s['suite_name'], {}).get('owner_full_name'), 'owner_is_fallback': owner_map.get(s['suite_name'], {}).get('is_fallback', False)} for s in result]
```

Contract limit: at least one response has an unstructured schema. Read the linked handler/serializer for emitted fields; the empty schema is not a promise of an empty JSON object.

## GET `/api/v1/test-management/suites/{suite_name}/cases`

Get Suite Test Cases

Paginated test cases for a suite, merging automation runs +
managed cases. Returns ``{items, total, page, pages, size}``.

Suite-match semantics: per-row ``tc.suite_name`` OR run-level
``tr.primary_suite_name`` — both contribute, so a SDK that stamps
the Java class as the per-row name but ``testlookup.suite`` at the
run level still surfaces the case under the run-level suite.

Source: [backend/app/routers/test_management_exports.py:1065](../../../backend/app/routers/test_management_exports.py#L1065).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`.

Declared Python handler arguments (includes exact role/guard options):

```python
suite_name: str, project_id: Optional[uuid.UUID]=Query(None), page: int=Query(1, ge=1), size: int=Query(25, ge=1, le=500), limit: Optional[int]=Query(None, ge=1, le=500), db: AsyncSession=Depends(get_db), current_user: User=Depends(get_current_active_user)
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "get_suite_test_cases_api_v1_test_management_suites__suite_name__cases_get",
  "parameters": [
    {
      "in": "path",
      "name": "suite_name",
      "required": true,
      "schema": {
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
      "in": "query",
      "name": "page",
      "required": false,
      "schema": {
        "default": 1,
        "minimum": 1,
        "title": "Page",
        "type": "integer"
      }
    },
    {
      "in": "query",
      "name": "size",
      "required": false,
      "schema": {
        "default": 25,
        "maximum": 500,
        "minimum": 1,
        "title": "Size",
        "type": "integer"
      }
    },
    {
      "in": "query",
      "name": "limit",
      "required": false,
      "schema": {
        "anyOf": [
          {
            "maximum": 500,
            "minimum": 1,
            "type": "integer"
          },
          {
            "type": "null"
          }
        ],
        "title": "Limit"
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
empty_page
{'items': result[:size], 'total': total, 'page': page, 'pages': pages, 'size': size}
```

Contract limit: at least one response has an unstructured schema. Read the linked handler/serializer for emitted fields; the empty schema is not a promise of an empty JSON object.

## GET `/api/v1/test-management/suites/{suite_name}/changes`

Get Suite Changes

Return suite membership change events, optionally filtered by run.

Source: [backend/app/routers/test_management_exports.py:1312](../../../backend/app/routers/test_management_exports.py#L1312).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`.

Declared Python handler arguments (includes exact role/guard options):

```python
suite_name: str, run_id: Optional[uuid.UUID]=Query(None), project_id: Optional[uuid.UUID]=Query(None), limit: int=Query(50, ge=1, le=200), db: AsyncSession=Depends(get_db), current_user: User=Depends(get_current_active_user)
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "get_suite_changes_api_v1_test_management_suites__suite_name__changes_get",
  "parameters": [
    {
      "in": "path",
      "name": "suite_name",
      "required": true,
      "schema": {
        "title": "Suite Name",
        "type": "string"
      }
    },
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
      "name": "limit",
      "required": false,
      "schema": {
        "default": 50,
        "maximum": 200,
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
[]
[{'id': str(e.id), 'suite_name': e.suite_name, 'test_fingerprint': e.test_fingerprint, 'test_name': e.test_name, 'event_type': e.event_type, 'run_id': str(e.run_id) if e.run_id else None, 'old_values': e.old_values, 'new_values': e.new_values, 'details': e.details, 'created_at': e.created_at.isoformat() if e.created_at else None} for e in rows]
```

Contract limit: at least one response has an unstructured schema. Read the linked handler/serializer for emitted fields; the empty schema is not a promise of an empty JSON object.

## GET `/api/v1/test-management/suites/{suite_name}/deleted`

Get Suite Deleted

Return deleted/needs_review members from the <suite>-deleted bucket.

Source: [backend/app/routers/test_management_exports.py:1359](../../../backend/app/routers/test_management_exports.py#L1359).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`.

Declared Python handler arguments (includes exact role/guard options):

```python
suite_name: str, project_id: Optional[uuid.UUID]=Query(None), db: AsyncSession=Depends(get_db), current_user: User=Depends(get_current_active_user)
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "get_suite_deleted_api_v1_test_management_suites__suite_name__deleted_get",
  "parameters": [
    {
      "in": "path",
      "name": "suite_name",
      "required": true,
      "schema": {
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
[]
[{'id': str(m.id), 'original_suite': suite_name, 'test_fingerprint': m.test_fingerprint, 'test_name': m.test_name, 'class_name': m.class_name, 'status': m.status, 'review_tag': m.review_tag, 'deleted_at_run_id': str(m.deleted_at_run_id) if m.deleted_at_run_id else None, 'last_seen_run_id': str(m.last_seen_run_id) if m.last_seen_run_id else None, 'created_at': m.created_at.isoformat() if m.created_at else None} for m in rows]
```

Contract limit: at least one response has an unstructured schema. Read the linked handler/serializer for emitted fields; the empty schema is not a promise of an empty JSON object.

## GET `/api/v1/test-management/suites/{suite_name}/membership`

Get Suite Membership

Return current suite membership records from the traceability model.

Source: [backend/app/routers/test_management_exports.py:1262](../../../backend/app/routers/test_management_exports.py#L1262).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`.

Declared Python handler arguments (includes exact role/guard options):

```python
suite_name: str, project_id: Optional[uuid.UUID]=Query(None), status: Optional[str]=Query(None, pattern='^(active|deleted|needs_review)$'), db: AsyncSession=Depends(get_db), current_user: User=Depends(get_current_active_user)
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "get_suite_membership_api_v1_test_management_suites__suite_name__membership_get",
  "parameters": [
    {
      "in": "path",
      "name": "suite_name",
      "required": true,
      "schema": {
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
      "in": "query",
      "name": "status",
      "required": false,
      "schema": {
        "anyOf": [
          {
            "pattern": "^(active|deleted|needs_review)$",
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
[]
[{'id': str(m.id), 'suite_name': m.suite_name, 'test_fingerprint': m.test_fingerprint, 'test_name': m.test_name, 'class_name': m.class_name, 'source': m.source, 'status': m.status, 'review_tag': m.review_tag, 'last_seen_run_id': str(m.last_seen_run_id) if m.last_seen_run_id else None, 'first_seen_run_id': str(m.first_seen_run_id) if m.first_seen_run_id else None, 'managed_test_case_id': str(m.managed_test_case_id) if m.managed_test_case_id else None, 'created_at': m.created_at.isoformat() if m.created_at else None} for m in rows]
```

Contract limit: at least one response has an unstructured schema. Read the linked handler/serializer for emitted fields; the empty schema is not a promise of an empty JSON object.

## GET `/api/v1/test-management/suites/{suite_name}/trend`

Get Suite Trend

Per-day trend points for one suite over the time window.

Returns ``{suite_name, days, points: [{date, run_count, total_tests,
passed_count, failed_count, skipped_count, broken_count}]}``. Empty
days are emitted with all-zero counts so the chart x-axis stays
continuous.

Powers the trend chart on /coverage/suite and the sparkline on
/test-management Test Suites. Single source of truth lives in
``services/suite_history_service.compute_suite_trend`` so /suites
/reports/summary can adopt the same shape later.

Source: [backend/app/routers/test_management_exports.py:1015](../../../backend/app/routers/test_management_exports.py#L1015).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`.

Declared Python handler arguments (includes exact role/guard options):

```python
suite_name: str, project_id: Optional[uuid.UUID]=Query(None), days: int=Query(30, ge=1, le=365), db: AsyncSession=Depends(get_db), current_user: User=Depends(get_current_active_user)
```

Direct handler error branches (dependency/service errors can add others):

```python
HTTPException(status_code=403, detail='You do not have access to this project')
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "get_suite_trend_api_v1_test_management_suites__suite_name__trend_get",
  "parameters": [
    {
      "in": "path",
      "name": "suite_name",
      "required": true,
      "schema": {
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
      "in": "query",
      "name": "days",
      "required": false,
      "schema": {
        "default": 30,
        "maximum": 365,
        "minimum": 1,
        "title": "Days",
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
{'suite_name': suite_name, 'days': days, 'points': []}
{'suite_name': suite_name, 'days': days, 'points': points}
```

Contract limit: at least one response has an unstructured schema. Read the linked handler/serializer for emitted fields; the empty schema is not a promise of an empty JSON object.
