# Summary Report API

[Documentation home](../../README.md) · [Regeneration](../../handoff/maintenance.md)

Generated from tracked source by `scripts/generate_handoff_reference.py`. Do not edit by hand. The baseline and validation limits are recorded in [verification](../../handoff/verification.md).

## GET `/api/v1/reports/summary`

Get Summary Report

Return the summary report payload for the active project.

Source: [backend/app/routers/summary_report.py:59](../../../backend/app/routers/summary_report.py#L59).

Dependency chain: `OAuth2PasswordBearer`, `analytics_scope.<locals>.dependency`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`.

Declared Python handler arguments (includes exact role/guard options):

```python
mode: SummaryMode=Query('window', description='``window`` aggregates every run in the window; ``latest`` takes the most recent run per suite.'), scope: AnalyticsScope=Depends(analytics_scope(_REPORT_SCOPE)), db: AsyncSession=Depends(get_db)
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "get_summary_report_api_v1_reports_summary_get",
  "parameters": [
    {
      "description": "``window`` aggregates every run in the window; ``latest`` takes the most recent run per suite.",
      "in": "query",
      "name": "mode",
      "required": false,
      "schema": {
        "default": "window",
        "description": "``window`` aggregates every run in the window; ``latest`` takes the most recent run per suite.",
        "enum": [
          "window",
          "latest"
        ],
        "title": "Mode",
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
      "description": "Window in days, 1-365.",
      "in": "query",
      "name": "days",
      "required": false,
      "schema": {
        "default": 7,
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
          "schema": {
            "$ref": "#/components/schemas/SummaryReportResponse"
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

## GET `/api/v1/reports/summary/exports`

List Summary Report Exports

The reader's own background exports for the project that have not expired, newest first.

Source: [backend/app/routers/summary_report.py:213](../../../backend/app/routers/summary_report.py#L213).

Dependency chain: `OAuth2PasswordBearer`, `analytics_scope.<locals>.dependency`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`.

Declared Python handler arguments (includes exact role/guard options):

```python
scope: AnalyticsScope=Depends(analytics_scope(_PDF_SCOPE)), db: AsyncSession=Depends(get_db), current_user: User=Depends(get_current_active_user)
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "list_summary_report_exports_api_v1_reports_summary_exports_get",
  "parameters": [
    {
      "description": "Project to read. Single-valued. Required: without it the request is refused with 422 'missing_parameter' (All Projects is not supported here).",
      "in": "query",
      "name": "project_id",
      "required": true,
      "schema": {
        "description": "Project to read. Single-valued. Required: without it the request is refused with 422 'missing_parameter' (All Projects is not supported here).",
        "title": "Project Id",
        "type": "string"
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
      "description": "Window in days, 1-365.",
      "in": "query",
      "name": "days",
      "required": false,
      "schema": {
        "default": 7,
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
          "schema": {
            "items": {
              "$ref": "#/components/schemas/ReportExportOut"
            },
            "title": "Response List Summary Report Exports Api V1 Reports Summary Exports Get",
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

## POST `/api/v1/reports/summary/exports`

Request Summary Report Export

VIZ-607: download now, or queue a background export.

``delivery: "download"`` -- the report is small: fetch ``GET /pdf`` or
``/xlsx`` with the same query. ``delivery: "background"`` (202) -- a job
was queued; poll ``GET /exports/{id}`` and download from ``download_url``
when it completes. ``dispatched: false`` means the worker could not be
reached: the job stays queued (it is not failed) and can be retried.

Source: [backend/app/routers/summary_report.py:178](../../../backend/app/routers/summary_report.py#L178).

Dependency chain: `OAuth2PasswordBearer`, `analytics_scope.<locals>.dependency`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`.

Declared Python handler arguments (includes exact role/guard options):

```python
format: Literal['pdf', 'xlsx']=Query(..., description='``pdf`` or ``xlsx``.'), mode: SummaryMode=Query('window'), background: bool=Query(False, description='Queue a background export even when the report is small.'), scope: AnalyticsScope=Depends(analytics_scope(_PDF_SCOPE)), db: AsyncSession=Depends(get_db), current_user: User=Depends(get_current_active_user)
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "request_summary_report_export_api_v1_reports_summary_exports_post",
  "parameters": [
    {
      "description": "``pdf`` or ``xlsx``.",
      "in": "query",
      "name": "format",
      "required": true,
      "schema": {
        "description": "``pdf`` or ``xlsx``.",
        "enum": [
          "pdf",
          "xlsx"
        ],
        "title": "Format",
        "type": "string"
      }
    },
    {
      "in": "query",
      "name": "mode",
      "required": false,
      "schema": {
        "default": "window",
        "enum": [
          "window",
          "latest"
        ],
        "title": "Mode",
        "type": "string"
      }
    },
    {
      "description": "Queue a background export even when the report is small.",
      "in": "query",
      "name": "background",
      "required": false,
      "schema": {
        "default": false,
        "description": "Queue a background export even when the report is small.",
        "title": "Background",
        "type": "boolean"
      }
    },
    {
      "description": "Project to read. Single-valued. Required: without it the request is refused with 422 'missing_parameter' (All Projects is not supported here).",
      "in": "query",
      "name": "project_id",
      "required": true,
      "schema": {
        "description": "Project to read. Single-valued. Required: without it the request is refused with 422 'missing_parameter' (All Projects is not supported here).",
        "title": "Project Id",
        "type": "string"
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
      "description": "Window in days, 1-365.",
      "in": "query",
      "name": "days",
      "required": false,
      "schema": {
        "default": 7,
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
          "schema": {
            "$ref": "#/components/schemas/ReportExportRequestOut"
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

## GET `/api/v1/reports/summary/exports/{export_id}`

Get Summary Report Export

One background export's status (any member of its project).

Source: [backend/app/routers/summary_report.py:232](../../../backend/app/routers/summary_report.py#L232).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_report_export_access.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
export_id: uuid.UUID, db: AsyncSession=Depends(get_db), current_user: User=Depends(require_report_export_access())
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "get_summary_report_export_api_v1_reports_summary_exports__export_id__get",
  "parameters": [
    {
      "in": "path",
      "name": "export_id",
      "required": true,
      "schema": {
        "format": "uuid",
        "title": "Export Id",
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
            "$ref": "#/components/schemas/ReportExportOut"
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

## GET `/api/v1/reports/summary/exports/{export_id}/download`

Download Summary Report Export

The finished file, through the API (no storage URL leaves the server).

409 until the export has completed; 410 once it has expired.

Source: [backend/app/routers/summary_report.py:242](../../../backend/app/routers/summary_report.py#L242).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_report_export_access.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
export_id: uuid.UUID, db: AsyncSession=Depends(get_db), current_user: User=Depends(require_report_export_access())
```

Direct handler error branches (dependency/service errors can add others):

```python
HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f'This export is {export.status}, not ready to download.')
HTTPException(status_code=status.HTTP_410_GONE, detail='This export has expired. Export the report again.')
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "download_summary_report_export_api_v1_reports_summary_exports__export_id__download_get",
  "parameters": [
    {
      "in": "path",
      "name": "export_id",
      "required": true,
      "schema": {
        "format": "uuid",
        "title": "Export Id",
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
StreamingResponse(get_storage_provider().stream_object(export.storage_key), media_type=media_type, headers={'Content-Disposition': f'attachment; filename="{export.filename}"'})
```

Contract limit: at least one response has an unstructured schema. Read the linked handler/serializer for emitted fields; the empty schema is not a promise of an empty JSON object.

## POST `/api/v1/reports/summary/exports/{export_id}/retry`

Retry Summary Report Export

Re-queue a failed export, or one whose message or worker was lost. 409 otherwise.

Source: [backend/app/routers/summary_report.py:280](../../../backend/app/routers/summary_report.py#L280).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_report_export_access.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
export_id: uuid.UUID, db: AsyncSession=Depends(get_db), current_user: User=Depends(require_report_export_access())
```

Direct handler error branches (dependency/service errors can add others):

```python
HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f'This export is {export.status} and cannot be retried.')
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "retry_summary_report_export_api_v1_reports_summary_exports__export_id__retry_post",
  "parameters": [
    {
      "in": "path",
      "name": "export_id",
      "required": true,
      "schema": {
        "format": "uuid",
        "title": "Export Id",
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
            "$ref": "#/components/schemas/ReportExportOut"
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

## GET `/api/v1/reports/summary/pdf`

Export Summary Report Pdf

Return the summary report as a downloadable PDF, charts included (VIZ-607).

Source: [backend/app/routers/summary_report.py:116](../../../backend/app/routers/summary_report.py#L116).

Dependency chain: `OAuth2PasswordBearer`, `analytics_scope.<locals>.dependency`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`.

Declared Python handler arguments (includes exact role/guard options):

```python
mode: SummaryMode=Query('window'), scope: AnalyticsScope=Depends(analytics_scope(_PDF_SCOPE)), db: AsyncSession=Depends(get_db), current_user: User=Depends(get_current_active_user)
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "export_summary_report_pdf_api_v1_reports_summary_pdf_get",
  "parameters": [
    {
      "in": "query",
      "name": "mode",
      "required": false,
      "schema": {
        "default": "window",
        "enum": [
          "window",
          "latest"
        ],
        "title": "Mode",
        "type": "string"
      }
    },
    {
      "description": "Project to read. Single-valued. Required: without it the request is refused with 422 'missing_parameter' (All Projects is not supported here).",
      "in": "query",
      "name": "project_id",
      "required": true,
      "schema": {
        "description": "Project to read. Single-valued. Required: without it the request is refused with 422 'missing_parameter' (All Projects is not supported here).",
        "title": "Project Id",
        "type": "string"
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
      "description": "Window in days, 1-365.",
      "in": "query",
      "name": "days",
      "required": false,
      "schema": {
        "default": 7,
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
await _export('pdf', mode, scope, db, current_user)
```

Contract limit: at least one response has an unstructured schema. Read the linked handler/serializer for emitted fields; the empty schema is not a promise of an empty JSON object.

## GET `/api/v1/reports/summary/xlsx`

Export Summary Report Xlsx

VIZ-607: the summary report as an Excel workbook: a context sheet, then
one sheet per part of the report, each with a native chart over its data.

Source: [backend/app/routers/summary_report.py:132](../../../backend/app/routers/summary_report.py#L132).

Dependency chain: `OAuth2PasswordBearer`, `analytics_scope.<locals>.dependency`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`.

Declared Python handler arguments (includes exact role/guard options):

```python
mode: SummaryMode=Query('window'), scope: AnalyticsScope=Depends(analytics_scope(_PDF_SCOPE)), db: AsyncSession=Depends(get_db), current_user: User=Depends(get_current_active_user)
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "export_summary_report_xlsx_api_v1_reports_summary_xlsx_get",
  "parameters": [
    {
      "in": "query",
      "name": "mode",
      "required": false,
      "schema": {
        "default": "window",
        "enum": [
          "window",
          "latest"
        ],
        "title": "Mode",
        "type": "string"
      }
    },
    {
      "description": "Project to read. Single-valued. Required: without it the request is refused with 422 'missing_parameter' (All Projects is not supported here).",
      "in": "query",
      "name": "project_id",
      "required": true,
      "schema": {
        "description": "Project to read. Single-valued. Required: without it the request is refused with 422 'missing_parameter' (All Projects is not supported here).",
        "title": "Project Id",
        "type": "string"
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
      "description": "Window in days, 1-365.",
      "in": "query",
      "name": "days",
      "required": false,
      "schema": {
        "default": 7,
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
await _export('xlsx', mode, scope, db, current_user)
```

Contract limit: at least one response has an unstructured schema. Read the linked handler/serializer for emitted fields; the empty schema is not a promise of an empty JSON object.
