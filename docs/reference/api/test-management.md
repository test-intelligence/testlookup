# Test Management API

[Documentation home](../../README.md) · [Regeneration](../../maintainers/maintenance.md)

Generated from tracked source by `scripts/generate_reference.py`. Do not edit by hand. What the checks cover is recorded in [verification](../../maintainers/verification.md).

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



Source: [backend/app/routers/test_management_cases.py:219](../../../backend/app/routers/test_management_cases.py#L219).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`.

Declared Python handler arguments (includes exact role/guard options):

```python
project_id: Optional[uuid.UUID]=None, status: Optional[List[TestCaseLifecycleState]]=Query(None), test_type: Optional[List[str]]=Query(None), priority: Optional[List[str]]=Query(None), feature_area: str | None=None, ai_generated: bool | None=None, search: str | None=None, suite_name: str | None=None, owner: Optional[str]=None, automation: Optional[str]=None, requirement: Optional[str]=Query(None, max_length=200), include_automation: bool=Query(False, description='When true, merge synthesised rows derived from per-run test_cases into the response so the Test Management page can surface automation-ingested tests alongside authored ones. Deduped by test_fingerprint — any fingerprint already linked to a managed_test_cases row is skipped.'), include_archived: bool=Query(False, description='Include archived authored cases when no exact status filter is set.'), page: int=Query(1, ge=1), size: int=Query(25, ge=1, le=200), db: AsyncSession=Depends(get_db), current_user: User=Depends(get_current_active_user)
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
            "items": {
              "$ref": "#/components/schemas/TestCaseLifecycleState"
            },
            "type": "array"
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
            "items": {
              "type": "string"
            },
            "type": "array"
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
            "items": {
              "type": "string"
            },
            "type": "array"
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
      "in": "query",
      "name": "owner",
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
        "title": "Owner"
      }
    },
    {
      "in": "query",
      "name": "automation",
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
        "title": "Automation"
      }
    },
    {
      "in": "query",
      "name": "requirement",
      "required": false,
      "schema": {
        "anyOf": [
          {
            "maxLength": 200,
            "type": "string"
          },
          {
            "type": "null"
          }
        ],
        "title": "Requirement"
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



Source: [backend/app/routers/test_management_cases.py:386](../../../backend/app/routers/test_management_cases.py#L386).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_role.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
payload: ManagedTestCaseCreate, db: AsyncSession=Depends(get_db), current_user: User=Depends(require_role(UserRole.TESTER))
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



Source: [backend/app/routers/test_management_ai.py:80](../../../backend/app/routers/test_management_ai.py#L80).

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



Source: [backend/app/routers/test_management_ai.py:36](../../../backend/app/routers/test_management_ai.py#L36).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_role.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
payload: AIGenerateTestCasesRequest, db: AsyncSession=Depends(get_db), current_user: User=Depends(require_role(UserRole.TESTER))
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



Source: [backend/app/routers/test_management_ai.py:50](../../../backend/app/routers/test_management_ai.py#L50).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_role.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
payload: AIGenerateTestCasesRequest, db: AsyncSession=Depends(get_db), current_user: User=Depends(require_role(UserRole.TESTER))
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



Source: [backend/app/routers/test_management_ai.py:61](../../../backend/app/routers/test_management_ai.py#L61).

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

## POST `/api/v1/test-management/cases/bulk`

Bulk Edit Test Cases

Change the status, owner, priority, tags or suite of selected cases of ONE project.

The body's ``project_id`` is authorised FIRST (403 before anything is
loaded); ``case_ids`` are then loaded only within that project, so another
project's id is ``not_found`` in the per-case results, like an unknown one.
One 200 answers per case; a refused case never fails the request.

Source: [backend/app/routers/test_management_editing.py:41](../../../backend/app/routers/test_management_editing.py#L41).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_role.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
payload: TestCaseBulkRequest, db: AsyncSession=Depends(get_db), current_user: User=Depends(require_role(UserRole.TESTER))
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "bulk_edit_test_cases_api_v1_test_management_cases_bulk_post",
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
          "$ref": "#/components/schemas/TestCaseBulkRequest"
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
            "$ref": "#/components/schemas/TestCaseBulkResponse"
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



Source: [backend/app/routers/test_management_cases.py:399](../../../backend/app/routers/test_management_cases.py#L399).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`.

Declared Python handler arguments (includes exact role/guard options):

```python
kind: str=Query(..., pattern=EVIDENCE_GAP_KIND_PATTERN), project_id: Optional[uuid.UUID]=Query(None), page: int=Query(1, ge=1), size: int=Query(25, ge=1, le=200), db: AsyncSession=Depends(get_db), current_user: User=Depends(get_current_active_user)
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
        "pattern": "^(never_executed|automation_vanished|flaky_active|no_owner)$",
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

Honours the case list's filters (``status`` / ``test_type`` / ``priority``
repeatable, ``owner``, ``automation``, ``requirement``, the list's search)
and keeps its own default of every status. Columns 1-18 are unchanged;
``ID``, ``Suite`` and ``Description`` are appended as 19-21 (an import
reads them back; prepending would renumber the pinned columns).

Source: [backend/app/routers/test_management_exports.py:83](../../../backend/app/routers/test_management_exports.py#L83).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`.

Declared Python handler arguments (includes exact role/guard options):

```python
project_id: Optional[uuid.UUID]=None, status: Optional[List[str]]=Query(None), test_type: Optional[List[str]]=Query(None), priority: Optional[List[str]]=Query(None), search: Optional[str]=None, owner: Optional[str]=None, automation: Optional[str]=None, requirement: Optional[str]=None, db: AsyncSession=Depends(get_db), current_user: User=Depends(get_current_active_user)
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
            "items": {
              "type": "string"
            },
            "type": "array"
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
            "items": {
              "type": "string"
            },
            "type": "array"
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
            "items": {
              "type": "string"
            },
            "type": "array"
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
      "in": "query",
      "name": "owner",
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
        "title": "Owner"
      }
    },
    {
      "in": "query",
      "name": "automation",
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
        "title": "Automation"
      }
    },
    {
      "in": "query",
      "name": "requirement",
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
        "title": "Requirement"
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

## POST `/api/v1/test-management/cases/import`

Import Test Cases

A dry run by default (nothing written); ``dry_run=false`` creates every valid row as a Draft.

Source: [backend/app/routers/test_management_library.py:103](../../../backend/app/routers/test_management_library.py#L103).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_role.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
project_id: uuid.UUID=Query(...), dry_run: bool=Query(True), file: UploadFile=File(...), db: AsyncSession=Depends(get_db), current_user: User=Depends(require_role(UserRole.TESTER))
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "import_test_cases_api_v1_test_management_cases_import_post",
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
      "name": "dry_run",
      "required": false,
      "schema": {
        "default": true,
        "title": "Dry Run",
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
  "requestBody": {
    "content": {
      "multipart/form-data": {
        "schema": {
          "$ref": "#/components/schemas/Body_import_test_cases_api_v1_test_management_cases_import_post"
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
            "$ref": "#/components/schemas/TestCaseImportResponse"
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

## DELETE `/api/v1/test-management/cases/{case_id}`

Deprecate Test Case



Source: [backend/app/routers/test_management_cases.py:459](../../../backend/app/routers/test_management_cases.py#L459).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_case_access`, `require_role.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
case_id: uuid.UUID, payload: Optional[TestCaseDeprecateRequest]=Body(None), db: AsyncSession=Depends(get_db), current_user: User=Depends(require_role(UserRole.QA_ENGINEER)), _case=Depends(require_case_access)
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



Source: [backend/app/routers/test_management_cases.py:431](../../../backend/app/routers/test_management_cases.py#L431).

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



Source: [backend/app/routers/test_management_cases.py:444](../../../backend/app/routers/test_management_cases.py#L444).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_case_access`, `require_role.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
case_id: uuid.UUID, payload: ManagedTestCaseUpdate, db: AsyncSession=Depends(get_db), current_user: User=Depends(require_role(UserRole.TESTER)), _case=Depends(require_case_access)
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



Source: [backend/app/routers/test_management_ai.py:69](../../../backend/app/routers/test_management_ai.py#L69).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_role.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
case_id: uuid.UUID, db: AsyncSession=Depends(get_db), current_user: User=Depends(require_role(UserRole.TESTER))
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



Source: [backend/app/routers/test_management_cases.py:531](../../../backend/app/routers/test_management_cases.py#L531).

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



Source: [backend/app/routers/test_management_cases.py:575](../../../backend/app/routers/test_management_cases.py#L575).

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



Source: [backend/app/routers/test_management_cases.py:586](../../../backend/app/routers/test_management_cases.py#L586).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_case_access`, `require_role.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
case_id: uuid.UUID, payload: TestCaseCommentCreate, db: AsyncSession=Depends(get_db), current_user: User=Depends(require_role(UserRole.TESTER)), _case=Depends(require_case_access)
```

Direct handler error branches (dependency/service errors can add others):

```python
HTTPException(status_code=404, detail='Parent comment not found on this test case.')
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



Source: [backend/app/routers/test_management_cases.py:474](../../../backend/app/routers/test_management_cases.py#L474).

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



Source: [backend/app/routers/test_management_cases.py:489](../../../backend/app/routers/test_management_cases.py#L489).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_case_access_for_review_target`, `require_role.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
case_id: uuid.UUID, db: AsyncSession=Depends(get_db), current_user: User=Depends(require_role(UserRole.TESTER)), _case=Depends(require_case_access_for_review_target)
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

## GET `/api/v1/test-management/cases/{case_id}/requirements`

List Case Requirements

The case's manual requirement links, oldest first.

Source: [backend/app/routers/test_management_library.py:130](../../../backend/app/routers/test_management_library.py#L130).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_case_access`.

Declared Python handler arguments (includes exact role/guard options):

```python
case_id: uuid.UUID, db: AsyncSession=Depends(get_db), current_user: User=Depends(get_current_active_user), case: ManagedTestCase=Depends(require_case_access)
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "list_case_requirements_api_v1_test_management_cases__case_id__requirements_get",
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
            "$ref": "#/components/schemas/RequirementLinkListResponse"
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

## POST `/api/v1/test-management/cases/{case_id}/requirements`

Add Case Requirement

Link a requirement key to the case (TESTER+); records ``test_case.requirement_linked``.

Source: [backend/app/routers/test_management_library.py:143](../../../backend/app/routers/test_management_library.py#L143).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_case_access`, `require_role.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
case_id: uuid.UUID, payload: RequirementLinkCreate, db: AsyncSession=Depends(get_db), current_user: User=Depends(require_role(UserRole.TESTER)), case: ManagedTestCase=Depends(require_case_access)
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "add_case_requirement_api_v1_test_management_cases__case_id__requirements_post",
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
          "$ref": "#/components/schemas/RequirementLinkCreate"
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
            "$ref": "#/components/schemas/RequirementLinkRead"
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

## DELETE `/api/v1/test-management/cases/{case_id}/requirements/{requirement_link_id}`

Remove Case Requirement

Unlink one of THIS case's requirement links (TESTER+); records ``test_case.requirement_unlinked``.

Source: [backend/app/routers/test_management_library.py:162](../../../backend/app/routers/test_management_library.py#L162).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_case_access`, `require_role.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
case_id: uuid.UUID, requirement_link_id: uuid.UUID, db: AsyncSession=Depends(get_db), current_user: User=Depends(require_role(UserRole.TESTER)), case: ManagedTestCase=Depends(require_case_access)
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "remove_case_requirement_api_v1_test_management_cases__case_id__requirements__requirement_link_id__delete",
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
      "in": "path",
      "name": "requirement_link_id",
      "required": true,
      "schema": {
        "format": "uuid",
        "title": "Requirement Link Id",
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

## POST `/api/v1/test-management/cases/{case_id}/restore`

Restore Test Case Version

Save an older version's content as a new version; status and owner are kept.

Answers the case unchanged (same version) when that version's content is
already the current one.

Source: [backend/app/routers/test_management_editing.py:72](../../../backend/app/routers/test_management_editing.py#L72).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_case_access`, `require_role.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
case_id: uuid.UUID, payload: TestCaseRestoreRequest, db: AsyncSession=Depends(get_db), current_user: User=Depends(require_role(UserRole.TESTER)), _case: ManagedTestCase=Depends(require_case_access)
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "restore_test_case_version_api_v1_test_management_cases__case_id__restore_post",
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
          "$ref": "#/components/schemas/TestCaseRestoreRequest"
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

## POST `/api/v1/test-management/cases/{case_id}/review-action`

Review Action



Source: [backend/app/routers/test_management_cases.py:549](../../../backend/app/routers/test_management_cases.py#L549).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_case_access`, `require_role.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
case_id: uuid.UUID, payload: ReviewActionRequest, db: AsyncSession=Depends(get_db), current_user: User=Depends(require_role(UserRole.TESTER)), _case=Depends(require_case_access)
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



Source: [backend/app/routers/test_management_cases.py:564](../../../backend/app/routers/test_management_cases.py#L564).

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

## GET `/api/v1/test-management/cases/{case_id}/timeline`

Get Test Case Timeline

The case's timeline (TCL-05): newest first, keyset-paged by ``cursor``.

Source: [backend/app/routers/test_management_library.py:83](../../../backend/app/routers/test_management_library.py#L83).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_case_access`.

Declared Python handler arguments (includes exact role/guard options):

```python
case_id: uuid.UUID, kinds: Optional[List[str]]=Query(None), cursor: Optional[str]=Query(None, max_length=512), limit: int=Query(TIMELINE_LIMIT_DEFAULT, ge=1, le=TIMELINE_LIMIT_MAX), db: AsyncSession=Depends(get_db), current_user: User=Depends(get_current_active_user), case: ManagedTestCase=Depends(require_case_access)
```

Direct handler error branches (dependency/service errors can add others):

```python
HTTPException(status_code=422, detail=TIMELINE_KIND_INVALID)
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "get_test_case_timeline_api_v1_test_management_cases__case_id__timeline_get",
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
      "in": "query",
      "name": "kinds",
      "required": false,
      "schema": {
        "anyOf": [
          {
            "items": {
              "type": "string"
            },
            "type": "array"
          },
          {
            "type": "null"
          }
        ],
        "title": "Kinds"
      }
    },
    {
      "in": "query",
      "name": "cursor",
      "required": false,
      "schema": {
        "anyOf": [
          {
            "maxLength": 512,
            "type": "string"
          },
          {
            "type": "null"
          }
        ],
        "title": "Cursor"
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
            "$ref": "#/components/schemas/TestCaseTimelineResponse"
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



Source: [backend/app/routers/test_management_cases.py:503](../../../backend/app/routers/test_management_cases.py#L503).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_case_access`, `require_role.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
case_id: uuid.UUID, payload: TestCaseTransitionRequest, db: AsyncSession=Depends(get_db), current_user: User=Depends(require_role(UserRole.TESTER)), _case: ManagedTestCase=Depends(require_case_access)
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

## GET `/api/v1/test-management/health`

Get Library Health

Library health for ONE project (All Projects has none: 422 with the sentence).

Source: [backend/app/routers/test_management_library.py:64](../../../backend/app/routers/test_management_library.py#L64).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`.

Declared Python handler arguments (includes exact role/guard options):

```python
project_id: Optional[uuid.UUID]=Query(None), db: AsyncSession=Depends(get_db), current_user: User=Depends(get_current_active_user)
```

Direct handler error branches (dependency/service errors can add others):

```python
HTTPException(status_code=422, detail=HEALTH_NEEDS_PROJECT_SENTENCE)
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "get_library_health_api_v1_test_management_health_get",
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
            "$ref": "#/components/schemas/LibraryHealthResponse"
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



Source: [backend/app/routers/test_management_plans.py:175](../../../backend/app/routers/test_management_plans.py#L175).

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



Source: [backend/app/routers/test_management_plans.py:225](../../../backend/app/routers/test_management_plans.py#L225).

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

Source: [backend/app/routers/test_management_exports.py:422](../../../backend/app/routers/test_management_exports.py#L422).

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

Source: [backend/app/routers/test_management_exports.py:327](../../../backend/app/routers/test_management_exports.py#L327).

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



Source: [backend/app/routers/test_management_plans.py:126](../../../backend/app/routers/test_management_plans.py#L126).

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



Source: [backend/app/routers/test_management_plans.py:140](../../../backend/app/routers/test_management_plans.py#L140).

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



Source: [backend/app/routers/test_management_plans.py:152](../../../backend/app/routers/test_management_plans.py#L152).

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

Source: [backend/app/routers/test_management_exports.py:653](../../../backend/app/routers/test_management_exports.py#L653).

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

Source: [backend/app/routers/test_management_exports.py:541](../../../backend/app/routers/test_management_exports.py#L541).

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

List Test Suites In Scope

Return test suites grouped by suite_name, combining automation test_cases
(from ingested runs) and manually authored managed_test_cases.

``release_id`` / ``suite_name`` repeat (OR within each). A release keeps
the suites and counts of runs in those releases (manual cases, which never
ran in a release, are then left out); a suite name keeps those suites
(trimmed, case-insensitive). The body stays a JSON list, so a bounded
summary of the analytics envelope (ids and counts, no names, <= 2 KB;
``analytics_meta.header_summary``) travels in the ``X-Analytics-Meta``
header (VIZ-204).

Source: [backend/app/routers/test_management_exports.py:779](../../../backend/app/routers/test_management_exports.py#L779).

Dependency chain: `OAuth2PasswordBearer`, `analytics_scope.<locals>.dependency`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`.

Declared Python handler arguments (includes exact role/guard options):

```python
response: Response, scope: AnalyticsScope=Depends(analytics_scope(_SUITES_SCOPE)), db: AsyncSession=Depends(get_db), current_user: User=Depends(get_current_active_user)
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "list_test_suites_in_scope_api_v1_test_management_suites_get",
  "parameters": [
    {
      "description": "One project (single-valued). Omit for every project you can read.",
      "in": "query",
      "name": "project_id",
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
        "description": "One project (single-valued). Omit for every project you can read.",
        "title": "Project Id"
      }
    },
    {
      "description": "Repeatable (OR, at most 20): a release UUID or 'unattributed' for runs no release claims.",
      "in": "query",
      "name": "release_id",
      "required": false,
      "schema": {
        "anyOf": [
          {
            "items": {
              "type": "string"
            },
            "type": "array"
          },
          {
            "type": "null"
          }
        ],
        "description": "Repeatable (OR, at most 20): a release UUID or 'unattributed' for runs no release claims.",
        "title": "Release Id"
      }
    },
    {
      "description": "Repeatable (OR, at most 50), 1-500 characters; matched case-insensitively on the effective suite.",
      "in": "query",
      "name": "suite_name",
      "required": false,
      "schema": {
        "anyOf": [
          {
            "items": {
              "type": "string"
            },
            "type": "array"
          },
          {
            "type": "null"
          }
        ],
        "description": "Repeatable (OR, at most 50), 1-500 characters; matched case-insensitively on the effective suite.",
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
rows
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

Source: [backend/app/routers/test_management_exports.py:1301](../../../backend/app/routers/test_management_exports.py#L1301).

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

Source: [backend/app/routers/test_management_exports.py:1560](../../../backend/app/routers/test_management_exports.py#L1560).

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
await suite_cutover.canonical_changes(db, suite_name=suite_name, project_id=project_id, run_id=run_id, limit=limit)
```

Contract limit: at least one response has an unstructured schema. Read the linked handler/serializer for emitted fields; the empty schema is not a promise of an empty JSON object.

## GET `/api/v1/test-management/suites/{suite_name}/deleted`

Get Suite Deleted

Return deleted/needs_review members from the <suite>-deleted bucket.

Source: [backend/app/routers/test_management_exports.py:1611](../../../backend/app/routers/test_management_exports.py#L1611).

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
await suite_cutover.canonical_deleted(db, suite_name=suite_name, project_id=project_id)
```

Contract limit: at least one response has an unstructured schema. Read the linked handler/serializer for emitted fields; the empty schema is not a promise of an empty JSON object.

## GET `/api/v1/test-management/suites/{suite_name}/membership`

Get Suite Membership

Return current suite membership records from the traceability model.

Source: [backend/app/routers/test_management_exports.py:1506](../../../backend/app/routers/test_management_exports.py#L1506).

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
await suite_cutover.canonical_membership(db, suite_name=suite_name, project_id=project_id, status=status)
```

Contract limit: at least one response has an unstructured schema. Read the linked handler/serializer for emitted fields; the empty schema is not a promise of an empty JSON object.

## GET `/api/v1/test-management/suites/{suite_name}/trend`

Get Suite Trend In Scope

Per-day trend points for one suite over the time window.

``release_id`` repeats (OR) and keeps the runs of those releases. The
suite is the path segment; a ``suite_name`` query parameter cannot widen
or narrow it and is reported in ``meta.ignored_filters``.

Source: [backend/app/routers/test_management_exports.py:1208](../../../backend/app/routers/test_management_exports.py#L1208).

Dependency chain: `OAuth2PasswordBearer`, `analytics_scope.<locals>.dependency`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`.

Declared Python handler arguments (includes exact role/guard options):

```python
suite_name: str, request: Request, scope: AnalyticsScope=Depends(analytics_scope(_SUITE_TREND_SCOPE)), db: AsyncSession=Depends(get_db), current_user: User=Depends(get_current_active_user)
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "get_suite_trend_in_scope_api_v1_test_management_suites__suite_name__trend_get",
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
      "description": "One project (single-valued). Omit for every project you can read.",
      "in": "query",
      "name": "project_id",
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
        "description": "One project (single-valued). Omit for every project you can read.",
        "title": "Project Id"
      }
    },
    {
      "description": "Repeatable (OR, at most 20): a release UUID or 'unattributed' for runs no release claims.",
      "in": "query",
      "name": "release_id",
      "required": false,
      "schema": {
        "anyOf": [
          {
            "items": {
              "type": "string"
            },
            "type": "array"
          },
          {
            "type": "null"
          }
        ],
        "description": "Repeatable (OR, at most 20): a release UUID or 'unattributed' for runs no release claims.",
        "title": "Release Id"
      }
    },
    {
      "description": "Window in days, 1-365.",
      "in": "query",
      "name": "days",
      "required": false,
      "schema": {
        "default": 30,
        "description": "Window in days, 1-365.",
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
{**body, 'meta': meta}
```

Contract limit: at least one response has an unstructured schema. Read the linked handler/serializer for emitted fields; the empty schema is not a promise of an empty JSON object.

## GET `/api/v1/test-management/traceability/gaps`

Get Traceability Gaps

The project's uncovered requirements, paged, and the cases linking none.

Source: [backend/app/routers/test_management_library.py:179](../../../backend/app/routers/test_management_library.py#L179).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`.

Declared Python handler arguments (includes exact role/guard options):

```python
project_id: Optional[uuid.UUID]=Query(None), page: int=Query(1, ge=1), size: int=Query(25, ge=1, le=100), db: AsyncSession=Depends(get_db), current_user: User=Depends(get_current_active_user)
```

Direct handler error branches (dependency/service errors can add others):

```python
HTTPException(status_code=422, detail=TRACEABILITY_NEEDS_PROJECT)
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "get_traceability_gaps_api_v1_test_management_traceability_gaps_get",
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
            "$ref": "#/components/schemas/TraceabilityGapsResponse"
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
