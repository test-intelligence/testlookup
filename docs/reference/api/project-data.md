# Project Data API

[Documentation home](../../README.md) · [Regeneration](../../maintainers/maintenance.md)

Generated from tracked source by `scripts/generate_reference.py`. Do not edit by hand. What the checks cover is recorded in [verification](../../maintainers/verification.md).

## GET `/api/v1/project-data-jobs/{data_job_id}`

Get Project Data Job

One export or import job (the page and the CLI poll it). Writes nothing.

Source: [backend/app/routers/project_data.py:153](../../../backend/app/routers/project_data.py#L153).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_project_data_job_access.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
data_job_id: uuid.UUID, db: AsyncSession=Depends(get_db), job: ProjectDataJob=Depends(require_project_data_job_access())
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "get_project_data_job_api_v1_project_data_jobs__data_job_id__get",
  "parameters": [
    {
      "in": "path",
      "name": "data_job_id",
      "required": true,
      "schema": {
        "format": "uuid",
        "title": "Data Job Id",
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
            "$ref": "#/components/schemas/ProjectDataJobRead"
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

## GET `/api/v1/project-data-jobs/{data_job_id}/download`

Download Project Export

Stream a succeeded, unexpired export (``Content-Length`` = the stored size). 409 not an export / not
ready / failed, 410 expired, 502 when storage cannot give the first byte. Writes NOTHING.

Source: [backend/app/routers/project_data.py:169](../../../backend/app/routers/project_data.py#L169).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_project_data_job_access.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
data_job_id: uuid.UUID, db: AsyncSession=Depends(get_db), job: ProjectDataJob=Depends(require_project_data_job_access())
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "download_project_export_api_v1_project_data_jobs__data_job_id__download_get",
  "parameters": [
    {
      "in": "path",
      "name": "data_job_id",
      "required": true,
      "schema": {
        "format": "uuid",
        "title": "Data Job Id",
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
StreamingResponse(_body(), media_type='application/zip', headers=headers)
```

Contract limit: at least one response has an unstructured schema. Read the linked handler/serializer for emitted fields; the empty schema is not a promise of an empty JSON object.

## GET `/api/v1/project-imports`

List My Project Imports

The caller's OWN import jobs, newest first. Writes nothing.

Source: [backend/app/routers/project_data.py:260](../../../backend/app/routers/project_data.py#L260).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_role.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
limit: int=Query(JOBS_LIST_LIMIT, ge=1, le=JOBS_LIST_LIMIT), db: AsyncSession=Depends(get_db), current_user: User=Depends(require_role(UserRole.ADMIN))
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "list_my_project_imports_api_v1_project_imports_get",
  "parameters": [
    {
      "in": "query",
      "name": "limit",
      "required": false,
      "schema": {
        "default": 10,
        "maximum": 10,
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
            "$ref": "#/components/schemas/ProjectDataJobList"
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

## POST `/api/v1/projects/import`

Start Project Import

Accept an export ZIP to import as a NEW project. The body is counted into a spool before any job
row exists (413 past 50 MB, nothing stored); a repeated ``client_token`` answers 200 with its job and
reads nothing. Commit, then enqueue; 202.

Source: [backend/app/routers/project_data.py:221](../../../backend/app/routers/project_data.py#L221).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_role.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
response: Response, file: Optional[UploadFile]=File(None), client_token: Optional[str]=Form(None), name: Optional[str]=Form(None), db: AsyncSession=Depends(get_db), current_user: User=Depends(require_role(UserRole.ADMIN))
```

Direct handler error branches (dependency/service errors can add others):

```python
HTTPException(status_code=ROUTE_STATUS.get(refused.code, 422), detail=refused.detail)
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "start_project_import_api_v1_projects_import_post",
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
      "multipart/form-data": {
        "schema": {
          "$ref": "#/components/schemas/Body_start_project_import_api_v1_projects_import_post"
        }
      }
    }
  },
  "responses": {
    "202": {
      "content": {
        "application/json": {
          "schema": {
            "$ref": "#/components/schemas/ProjectDataJobRead"
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

## GET `/api/v1/projects/{project_id}/data-jobs`

List Project Exports

The project's exports, newest first; ``total`` counts all of them. Writes nothing.

Source: [backend/app/routers/project_data.py:134](../../../backend/app/routers/project_data.py#L134).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_project_access.<locals>._check`, `require_role.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
project_id: uuid.UUID, limit: int=Query(JOBS_LIST_LIMIT, ge=1, le=JOBS_LIST_LIMIT), db: AsyncSession=Depends(get_db)
```

Direct handler error branches (dependency/service errors can add others):

```python
HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Project not found')
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "list_project_exports_api_v1_projects__project_id__data_jobs_get",
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
        "default": 10,
        "maximum": 10,
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
            "$ref": "#/components/schemas/ProjectDataJobList"
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

## POST `/api/v1/projects/{project_id}/export`

Start Project Export

Queue an export of the project's settings + catalog (an archived project too: the access rule is
``require_project_access``'s, unchanged). 409 ``export_running`` names the live job.

Source: [backend/app/routers/project_data.py:103](../../../backend/app/routers/project_data.py#L103).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_project_access.<locals>._check`, `require_role.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
project_id: uuid.UUID, db: AsyncSession=Depends(get_db), current_user: User=Depends(get_current_active_user)
```

Direct handler error branches (dependency/service errors can add others):

```python
HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Project not found')
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "start_project_export_api_v1_projects__project_id__export_post",
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
            "$ref": "#/components/schemas/ProjectDataJobRead"
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
