# My Failures API

[Documentation home](../../README.md) · [Regeneration](../../handoff/maintenance.md)

Generated from tracked source by `scripts/generate_handoff_reference.py`. Do not edit by hand. The baseline and validation limits are recorded in [verification](../../handoff/verification.md).

## GET `/api/v1/me/assigned-failures`

List My Assigned Failures

Return the caller's FAILED/BROKEN TestCase rows, newest first.

Pagination notes:
  * ``unresolved_total`` is the same-filter total without pagination so
    the sidebar badge stays accurate while the user is on page 2.
  * ``total`` is the paginated total (matches ``items`` count summed
    across pages) — currently equal to ``unresolved_total`` because we
    don't yet have a "resolved" concept; future-proofed by keeping both
    fields distinct now.

Source: [backend/app/routers/my_failures.py:104](../../../backend/app/routers/my_failures.py#L104).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`.

Declared Python handler arguments (includes exact role/guard options):

```python
project_id: Optional[str]=Query(None, description='Project UUID or "all"'), days: int=Query(30, ge=1, le=365, description='Time window (created_at)'), release_id: Optional[str]=Query(None, description='Only failures from runs in this release.'), page: int=Query(1, ge=1), size: int=Query(25, ge=1, le=100), scope: str=Query('mine', pattern='^(mine|team)$', description="'mine' = caller's assigned failures only; 'team' = all failures across project (QA_LEAD/ADMIN only)"), db: AsyncSession=Depends(get_db), current_user: User=Depends(get_current_active_user)
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "list_my_assigned_failures_api_v1_me_assigned_failures_get",
  "parameters": [
    {
      "description": "Project UUID or \"all\"",
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
        "description": "Project UUID or \"all\"",
        "title": "Project Id"
      }
    },
    {
      "description": "Time window (created_at)",
      "in": "query",
      "name": "days",
      "required": false,
      "schema": {
        "default": 30,
        "description": "Time window (created_at)",
        "maximum": 365,
        "minimum": 1,
        "title": "Days",
        "type": "integer"
      }
    },
    {
      "description": "Only failures from runs in this release.",
      "in": "query",
      "name": "release_id",
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
        "description": "Only failures from runs in this release.",
        "title": "Release Id"
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
      "description": "'mine' = caller's assigned failures only; 'team' = all failures across project (QA_LEAD/ADMIN only)",
      "in": "query",
      "name": "scope",
      "required": false,
      "schema": {
        "default": "mine",
        "description": "'mine' = caller's assigned failures only; 'team' = all failures across project (QA_LEAD/ADMIN only)",
        "pattern": "^(mine|team)$",
        "title": "Scope",
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
            "$ref": "#/components/schemas/MyFailureListResponse"
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

## GET `/api/v1/me/assigned-failures/count`

My Assigned Failures Count

Cheap COUNT for the sidebar badge — no row hydration.

The list endpoint already returns ``unresolved_total`` for pages it
serves, but the sidebar polls independently of the page state, so a
dedicated endpoint keeps the badge fresh without fetching 25 rows on
every poll.

Source: [backend/app/routers/my_failures.py:349](../../../backend/app/routers/my_failures.py#L349).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`.

Declared Python handler arguments (includes exact role/guard options):

```python
project_id: Optional[str]=Query(None), days: int=Query(30, ge=1, le=365), release_id: Optional[str]=Query(None, description='Only failures from runs in this release.'), scope: str=Query('mine', pattern='^(mine|team)$'), db: AsyncSession=Depends(get_db), current_user: User=Depends(get_current_active_user)
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "my_assigned_failures_count_api_v1_me_assigned_failures_count_get",
  "parameters": [
    {
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
      "description": "Only failures from runs in this release.",
      "in": "query",
      "name": "release_id",
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
        "description": "Only failures from runs in this release.",
        "title": "Release Id"
      }
    },
    {
      "in": "query",
      "name": "scope",
      "required": false,
      "schema": {
        "default": "mine",
        "pattern": "^(mine|team)$",
        "title": "Scope",
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
{'count': count}
```

Contract limit: at least one response has an unstructured schema. Read the linked handler/serializer for emitted fields; the empty schema is not a promise of an empty JSON object.

## PUT `/api/v1/me/assigned-failures/{test_case_id}/reassign`

Reassign Assigned Failure

Move a FAILED/BROKEN TestCase to a new owner.

Authorisation:
  * Caller must be QA_LEAD or ADMIN on the failure's project.
  * ``new_assignee_user_id`` must be EITHER the resolved suite owner
    OR a QA_ENGINEER project member. Anyone else 422s — including
    another QA_LEAD or ADMIN. The auto-assigner already covers
    intra-Lead reassignment via its pool distribution.

Returns the updated ``MyFailureItem`` so the frontend can drop the
new row into the (now-correct) owner's view without re-fetching the
whole list. The caller's own list shrinks by one on the next poll.

Source: [backend/app/routers/my_failures.py:469](../../../backend/app/routers/my_failures.py#L469).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`.

Declared Python handler arguments (includes exact role/guard options):

```python
test_case_id: uuid.UUID, new_assignee_user_id: uuid.UUID=Body(..., embed=True), db: AsyncSession=Depends(get_db), current_user: User=Depends(get_current_active_user)
```

Direct handler error branches (dependency/service errors can add others):

```python
HTTPException(status_code=exc.status_code, detail=exc.detail)
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "reassign_assigned_failure_api_v1_me_assigned_failures__test_case_id__reassign_put",
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
          "$ref": "#/components/schemas/Body_reassign_assigned_failure_api_v1_me_assigned_failures__test_case_id__reassign_put"
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
            "$ref": "#/components/schemas/MyFailureItem"
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

## GET `/api/v1/me/assigned-failures/{test_case_id}/reassign-options`

Get Reassign Options

Picker payload for the reassign modal.

Returns the resolved suite owner (when one exists) plus every
QA_ENGINEER project member. The frontend renders these as the
only valid reassignment targets — anyone outside this set fails
the ``PUT .../reassign`` endpoint's 422 validation.

Same authorisation contract as the PUT: caller must be QA_LEAD or
ADMIN on the project. Returning 403 here (instead of an empty
payload) keeps the UI honest about WHY the picker is unavailable.

Source: [backend/app/routers/my_failures.py:446](../../../backend/app/routers/my_failures.py#L446).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`.

Declared Python handler arguments (includes exact role/guard options):

```python
test_case_id: uuid.UUID, db: AsyncSession=Depends(get_db), current_user: User=Depends(get_current_active_user)
```

Direct handler error branches (dependency/service errors can add others):

```python
HTTPException(status_code=exc.status_code, detail=exc.detail)
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "get_reassign_options_api_v1_me_assigned_failures__test_case_id__reassign_options_get",
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
await get_reassignment_options(db, test_case_id, current_user)
```

Contract limit: at least one response has an unstructured schema. Read the linked handler/serializer for emitted fields; the empty schema is not a promise of an empty JSON object.

## PUT `/api/v1/me/assigned-failures/{test_case_id}/triage`

Update Failure Triage Status

Move a failure to a new ``triage_status``.

Statuses (see ``TriageStatus`` enum):

* ``PENDING_REVIEW``     — default; the row appears on /my-failures.
* ``REVIEWED_APPROVED``  — looked at, no action. Known flake or
                           environmental issue.
* ``DEFECT_CREATED``     — defect/bug logged. ``notes`` typically
                           holds the bug link.
* ``WONT_FIX``           — deprecated test or accepted failure.
                           ``notes`` typically holds the rationale.

Authorisation: the assignee themselves, OR a QA_LEAD / ADMIN on the
project. Anyone else 403s.

The row drops off ``GET /assigned-failures`` (and the count badge)
on the next poll once status moves off PENDING_REVIEW — that's the
primary mechanism for "resolving" an inbox item.

Source: [backend/app/routers/my_failures.py:553](../../../backend/app/routers/my_failures.py#L553).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`.

Declared Python handler arguments (includes exact role/guard options):

```python
test_case_id: uuid.UUID, payload: TriageStatusUpdate, db: AsyncSession=Depends(get_db), current_user: User=Depends(get_current_active_user)
```

Direct handler error branches (dependency/service errors can add others):

```python
HTTPException(status_code=exc.status_code, detail=exc.detail)
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "update_failure_triage_status_api_v1_me_assigned_failures__test_case_id__triage_put",
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
          "$ref": "#/components/schemas/TriageStatusUpdate"
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
            "$ref": "#/components/schemas/MyFailureItem"
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
