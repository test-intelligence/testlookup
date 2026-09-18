# Duplicate Detection API

[Documentation home](../../README.md) · [Regeneration](../../handoff/maintenance.md)

Generated from tracked source by `scripts/generate_handoff_reference.py`. Do not edit by hand. The baseline and validation limits are recorded in [verification](../../handoff/verification.md).

## GET `/api/v1/projects/{project_id}/duplicate-candidates`

List Duplicate Candidates

List a project's duplicate-candidate review queue (paginated, filterable).

``status`` defaults to ``open`` so the review queue shows actionable pairs;
pass ``status=`` (empty) is not supported — use an explicit value to widen.

Source: [backend/app/routers/duplicates.py:82](../../../backend/app/routers/duplicates.py#L82).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_project_access.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
project_id: uuid.UUID, band: Optional[str]=Query(None, description='Filter: exact|strong|possible'), status_filter: Optional[str]=Query('open', alias='status', description='Filter: open|merged|dismissed (default open)'), page: int=Query(1, ge=1), size: int=Query(50, ge=1, le=200), db: AsyncSession=Depends(get_db), current_user: User=Depends(get_current_active_user), _: User=Depends(require_project_access())
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "list_duplicate_candidates_api_v1_projects__project_id__duplicate_candidates_get",
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
      "description": "Filter: exact|strong|possible",
      "in": "query",
      "name": "band",
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
        "description": "Filter: exact|strong|possible",
        "title": "Band"
      }
    },
    {
      "description": "Filter: open|merged|dismissed (default open)",
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
        "default": "open",
        "description": "Filter: open|merged|dismissed (default open)",
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
            "$ref": "#/components/schemas/DuplicateCandidateListResponse"
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

## POST `/api/v1/projects/{project_id}/duplicate-candidates/detect`

Run Duplicate Detection

Run a tiered detection sweep over the project's authored cases.

The service stages candidate rows + lazily backfills ``dup_fingerprint``;
this router owns the commit. ``semantic_used`` returned by the service is an
extra key the response model ignores.

Source: [backend/app/routers/duplicates.py:160](../../../backend/app/routers/duplicates.py#L160).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_project_access.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
project_id: uuid.UUID, enable_semantic: bool=Query(True, description='Run the optional local-embedder semantic tier (best-effort)'), db: AsyncSession=Depends(get_db), current_user: User=Depends(get_current_active_user), _: User=Depends(require_project_access())
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "run_duplicate_detection_api_v1_projects__project_id__duplicate_candidates_detect_post",
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
      "description": "Run the optional local-embedder semantic tier (best-effort)",
      "in": "query",
      "name": "enable_semantic",
      "required": false,
      "schema": {
        "default": true,
        "description": "Run the optional local-embedder semantic tier (best-effort)",
        "title": "Enable Semantic",
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
            "$ref": "#/components/schemas/DuplicateDetectionRunResponse"
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

## POST `/api/v1/projects/{project_id}/duplicate-candidates/{candidate_id}/dismiss`

Dismiss Duplicate Candidate

Dismiss a candidate pair (status → ``dismissed`` + suppression record).

The suppression row keeps the pair from resurfacing on the next detection
run. STAGE in the service; commit here.

Source: [backend/app/routers/duplicates.py:193](../../../backend/app/routers/duplicates.py#L193).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_project_access.<locals>._check`, `require_role.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
project_id: uuid.UUID, candidate_id: uuid.UUID, db: AsyncSession=Depends(get_db), current_user: User=Depends(get_current_active_user), _: User=Depends(require_project_access()), _reviewer: User=Depends(require_role(UserRole.QA_ENGINEER))
```

Direct handler error branches (dependency/service errors can add others):

```python
HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='duplicate candidate not found in project')
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "dismiss_duplicate_candidate_api_v1_projects__project_id__duplicate_candidates__candidate_id__dismiss_post",
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
      "name": "candidate_id",
      "required": true,
      "schema": {
        "format": "uuid",
        "title": "Candidate Id",
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
            "$ref": "#/components/schemas/DuplicateActionResponse"
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

## POST `/api/v1/projects/{project_id}/duplicate-candidates/{candidate_id}/merge`

Merge Duplicate Candidate

NON-DESTRUCTIVE merge: status → ``merged`` + optional soft-deprecate of the
losing case. Never deletes a case or redirects a fingerprint.

``payload.candidate_id`` must match the path ``candidate_id`` (the path is
authoritative — a mismatch is a 400). ``keep_case_id`` must be one of the
pair's two cases (service raises → 400).

Source: [backend/app/routers/duplicates.py:228](../../../backend/app/routers/duplicates.py#L228).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_project_access.<locals>._check`, `require_role.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
project_id: uuid.UUID, candidate_id: uuid.UUID, payload: DuplicateMergeRequest, db: AsyncSession=Depends(get_db), current_user: User=Depends(get_current_active_user), _: User=Depends(require_project_access()), _reviewer: User=Depends(require_role(UserRole.QA_ENGINEER))
```

Direct handler error branches (dependency/service errors can add others):

```python
HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='candidate_id in body does not match the path')
HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=msg)
HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail='Deprecating a duplicate merge loser requires QA_LEAD or ADMIN')
HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=msg)
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "merge_duplicate_candidate_api_v1_projects__project_id__duplicate_candidates__candidate_id__merge_post",
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
      "name": "candidate_id",
      "required": true,
      "schema": {
        "format": "uuid",
        "title": "Candidate Id",
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
          "$ref": "#/components/schemas/DuplicateMergeRequest"
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
            "$ref": "#/components/schemas/DuplicateActionResponse"
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
