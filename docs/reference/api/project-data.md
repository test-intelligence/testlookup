# Project Data API

[Documentation home](../../README.md) · [Regeneration](../../maintainers/maintenance.md)

Generated from tracked source by `scripts/generate_reference.py`. Do not edit by hand. What the checks cover is recorded in [verification](../../maintainers/verification.md).

## GET `/api/v1/project-data-jobs/{data_job_id}`

Get Project Data Job

One export or import job (the page and the CLI poll it). Writes nothing.

Source: [backend/app/routers/project_data.py:297](../../../backend/app/routers/project_data.py#L297).

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

Source: [backend/app/routers/project_data.py:313](../../../backend/app/routers/project_data.py#L313).

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

Source: [backend/app/routers/project_data.py:405](../../../backend/app/routers/project_data.py#L405).

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

## POST `/api/v1/project-imports/uploads`

Start Import Upload

Open a parted upload (ADMIN): a NEW import job in ``uploading`` (201), or — the token checked FIRST — the
caller's existing non-failed job of that ``client_token`` (200, with its received parts or its later
status). Then the name check (W24's order): 422 ``name_invalid`` / 409 ``name_taken``. Nothing is read.

Source: [backend/app/routers/project_data.py:438](../../../backend/app/routers/project_data.py#L438).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_role.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
start: ProjectImportUploadStart, response: Response, db: AsyncSession=Depends(get_db), current_user: User=Depends(require_role(UserRole.ADMIN))
```

Direct handler error branches (dependency/service errors can add others):

```python
HTTPException(status_code=http_status, detail=jobs.error_detail(refused.code, **refused.fmt))
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "start_import_upload_api_v1_project_imports_uploads_post",
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
          "$ref": "#/components/schemas/ProjectImportUploadStart"
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

## POST `/api/v1/project-imports/uploads/{data_job_id}/complete`

Complete Import Upload

Every part present at its planned size → ``queued``, COMMIT, then the worker is enqueued with the history
time limits (202). A repeat on a job already queued / running / finished → 200 with it (no second enqueue —
a lost one is the janitor's). 409 ``upload_incomplete`` names the missing parts; 409 ``upload_not_open``
for a failed / expired upload.

Source: [backend/app/routers/project_data.py:574](../../../backend/app/routers/project_data.py#L574).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_project_data_job_access.<locals>._check`, `require_role.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
data_job_id: uuid.UUID, response: Response, db: AsyncSession=Depends(get_db), job: ProjectDataJob=Depends(require_project_data_job_access())
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "complete_import_upload_api_v1_project_imports_uploads__data_job_id__complete_post",
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

## PUT `/api/v1/project-imports/uploads/{data_job_id}/parts/{part_number}`

Upload Import Part

One part (raw ``application/octet-stream``, ≤ 32 MiB) of the caller's own open upload. Checked before the
body is read: the job is this admin's import (else the ONE 404), still ``uploading`` (409
``upload_not_open``), the part number in its plan (422 ``part_number_invalid``), ``X-Part-Sha256`` 64 hex
(422 ``part_digest_mismatch``); the same part again with the same digest → 200, nothing rewritten; another
digest → 409 ``part_conflict``. Then the body is counted + hashed (413 ``part_too_large`` past 32 MiB, 422
``part_digest_mismatch`` / ``upload_parts_invalid`` — in every case NOTHING is stored), the job row locked
(``FOR UPDATE``), the part written as ONE object, its receipt recorded, ONE commit.

Source: [backend/app/routers/project_data.py:505](../../../backend/app/routers/project_data.py#L505).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_project_data_job_access.<locals>._check`, `require_role.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
data_job_id: uuid.UUID, part_number: int, request: Request, x_part_sha256: Optional[str]=Header(None, alias='X-Part-Sha256'), db: AsyncSession=Depends(get_db), job: ProjectDataJob=Depends(require_project_data_job_access())
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "upload_import_part_api_v1_project_imports_uploads__data_job_id__parts__part_number__put",
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
      "in": "path",
      "name": "part_number",
      "required": true,
      "schema": {
        "title": "Part Number",
        "type": "integer"
      }
    },
    {
      "in": "header",
      "name": "X-Part-Sha256",
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
        "title": "X-Part-Sha256"
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
            "$ref": "#/components/schemas/ProjectImportPartReceipt"
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

Source: [backend/app/routers/project_data.py:366](../../../backend/app/routers/project_data.py#L366).

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

Source: [backend/app/routers/project_data.py:278](../../../backend/app/routers/project_data.py#L278).

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
``require_project_access``'s, unchanged). 409 ``export_running`` names the live job. W30: no body /
``include_history: false`` == phase 1 exactly; ``include_history: true`` stores the options and dispatches
with the history time limits (422 ``history_window_invalid`` / ``max_runs_invalid`` for a bad body).

Source: [backend/app/routers/project_data.py:195](../../../backend/app/routers/project_data.py#L195).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_project_access.<locals>._check`, `require_role.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
project_id: uuid.UUID, body: Optional[ProjectExportRequest]=Body(default=None), db: AsyncSession=Depends(get_db), current_user: User=Depends(get_current_active_user)
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
  "requestBody": {
    "content": {
      "application/json": {
        "schema": {
          "anyOf": [
            {
              "$ref": "#/components/schemas/ProjectExportRequest"
            },
            {
              "type": "null"
            }
          ],
          "title": "Body"
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

## GET `/api/v1/projects/{project_id}/export-estimate`

Estimate Project Export

What an export with these options would hold — counts only (the export's rule: QA lead or ADMIN with
access). Writes NOTHING (no job, no object, no cache). An unreachable Mongo / object store answers
``measured[store] = false`` with NULL counts (200, never 0). ``history_days`` absent = all history.

Source: [backend/app/routers/project_data.py:233](../../../backend/app/routers/project_data.py#L233).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_project_access.<locals>._check`, `require_role.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
project_id: uuid.UUID, include_history: bool=Query(True), history_days: Optional[int]=Query(None), max_runs: int=Query(vocab.HISTORY_MAX_RUNS), db: AsyncSession=Depends(get_db)
```

Direct handler error branches (dependency/service errors can add others):

```python
HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Project not found')
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "estimate_project_export_api_v1_projects__project_id__export_estimate_get",
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
      "name": "include_history",
      "required": false,
      "schema": {
        "default": true,
        "title": "Include History",
        "type": "boolean"
      }
    },
    {
      "in": "query",
      "name": "history_days",
      "required": false,
      "schema": {
        "anyOf": [
          {
            "type": "integer"
          },
          {
            "type": "null"
          }
        ],
        "title": "History Days"
      }
    },
    {
      "in": "query",
      "name": "max_runs",
      "required": false,
      "schema": {
        "default": 2000,
        "title": "Max Runs",
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
            "$ref": "#/components/schemas/ProjectExportEstimate"
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
