# Defects API

[Documentation home](../../README.md) · [Regeneration](../../maintainers/maintenance.md)

Generated from tracked source by `scripts/generate_reference.py`. Do not edit by hand. What the checks cover is recorded in [verification](../../maintainers/verification.md).

## POST `/api/v1/projects/{project_id}/defects/jira`

Create Jira Defect One Click

One-click defect creation from a failure signature. QA_ENGINEER+.

The user reviews the prefilled payload in the dialog first — this
endpoint re-assembles it server-side (never trusts a client-edited
stack trace) and applies dedup before filing anything.

Source: [backend/app/routers/defect_jira.py:89](../../../backend/app/routers/defect_jira.py#L89).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_project_access.<locals>._check`, `require_role.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
project_id: uuid.UUID, payload: JiraDefectCreateRequest, db: AsyncSession=Depends(get_db), current_user: User=Depends(require_role(UserRole.QA_ENGINEER)), _: User=Depends(require_project_access())
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "create_jira_defect_one_click_api_v1_projects__project_id__defects_jira_post",
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
  "requestBody": {
    "content": {
      "application/json": {
        "schema": {
          "$ref": "#/components/schemas/JiraDefectCreateRequest"
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
            "$ref": "#/components/schemas/JiraDefectCreateResponse"
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

## GET `/api/v1/projects/{project_id}/defects/jira/metadata`

Jira Defect Metadata

Jira projects + issue types for the create dialog. Never 5xxs for a
broken Jira — returns ``available=false`` + ``reason`` instead.

Source: [backend/app/routers/defect_jira.py:54](../../../backend/app/routers/defect_jira.py#L54).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_project_access.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
project_id: uuid.UUID, db: AsyncSession=Depends(get_db), current_user: User=Depends(require_project_access())
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "jira_defect_metadata_api_v1_projects__project_id__defects_jira_metadata_get",
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
            "$ref": "#/components/schemas/JiraDefectMetadataResponse"
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

## GET `/api/v1/projects/{project_id}/defects/jira/preview`

Jira Defect Preview

Pre-filled Jira payload for the dialog's read-only preview.

422 when neither ``fingerprint`` nor ``cluster_id`` is given; 404 when
the signature matches nothing in the project.

Source: [backend/app/routers/defect_jira.py:68](../../../backend/app/routers/defect_jira.py#L68).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_project_access.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
project_id: uuid.UUID, fingerprint: str | None=Query(None, min_length=1, max_length=64), cluster_id: str | None=Query(None, min_length=1, max_length=255), db: AsyncSession=Depends(get_db), _: User=Depends(require_project_access())
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "jira_defect_preview_api_v1_projects__project_id__defects_jira_preview_get",
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
      "name": "fingerprint",
      "required": false,
      "schema": {
        "anyOf": [
          {
            "maxLength": 64,
            "minLength": 1,
            "type": "string"
          },
          {
            "type": "null"
          }
        ],
        "title": "Fingerprint"
      }
    },
    {
      "in": "query",
      "name": "cluster_id",
      "required": false,
      "schema": {
        "anyOf": [
          {
            "maxLength": 255,
            "minLength": 1,
            "type": "string"
          },
          {
            "type": "null"
          }
        ],
        "title": "Cluster Id"
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
            "$ref": "#/components/schemas/JiraDefectPreviewResponse"
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

## PATCH `/api/v1/projects/{project_id}/defects/{defect_id}`

Update Defect

Triage a defect: status, assignee, owner team, Jira link (DEF-01). QA_ENGINEER+.

Only the fields SENT change. Each step's failure leaves nothing written:
the Jira lookup (online only) runs with the request's connection handed
back and BEFORE any write; the writes and their ledger rows commit
together; the Jira status write-back runs after the commit and never
undoes it.

Source: [backend/app/routers/defect_jira.py:129](../../../backend/app/routers/defect_jira.py#L129).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_project_access.<locals>._check`, `require_role.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
project_id: uuid.UUID, defect_id: uuid.UUID, body: DefectPatchRequest, db: AsyncSession=Depends(get_db), current_user: User=Depends(require_role(UserRole.QA_ENGINEER)), _: User=Depends(require_project_access())
```

Direct handler error branches (dependency/service errors can add others):

```python
HTTPException(status_code=409, detail={'code': triage.REOPEN_CONFLICT, 'message': 'This test already has an open defect. Close that one first, or link this failure to it.', 'defect_id': None})
HTTPException(status_code=422, detail=triage.NOTHING_TO_CHANGE)
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "update_defect_api_v1_projects__project_id__defects__defect_id__patch",
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
      "name": "defect_id",
      "required": true,
      "schema": {
        "format": "uuid",
        "title": "Defect Id",
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
          "$ref": "#/components/schemas/DefectPatchRequest"
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
            "$ref": "#/components/schemas/DefectPatchResponse"
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
