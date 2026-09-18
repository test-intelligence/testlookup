# Summary Report API

[Documentation home](../../README.md) · [Regeneration](../../handoff/maintenance.md)

Generated from tracked source by `scripts/generate_handoff_reference.py`. Do not edit by hand. The baseline and validation limits are recorded in [verification](../../handoff/verification.md).

## GET `/api/v1/reports/summary`

Get Summary Report

Return the summary report payload for the active project.

Source: [backend/app/routers/summary_report.py:52](../../../backend/app/routers/summary_report.py#L52).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`.

Declared Python handler arguments (includes exact role/guard options):

```python
project_id: Optional[uuid.UUID]=Query(None, description='Project UUID. Required; omit returns an empty envelope.'), days: int=Query(7, ge=1, le=365, description='Time-window size in days.'), mode: SummaryMode=Query('window', description='``window`` aggregates every run in the window; ``latest`` takes the most recent run per suite.'), release_id: Optional[str]=Query(None, description='Scope every number in the report to one release. Omit for all releases — the SQL is then byte-identical to before this existed.'), db: AsyncSession=Depends(get_db), current_user: User=Depends(get_current_active_user)
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "get_summary_report_api_v1_reports_summary_get",
  "parameters": [
    {
      "description": "Project UUID. Required; omit returns an empty envelope.",
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
        "description": "Project UUID. Required; omit returns an empty envelope.",
        "title": "Project Id"
      }
    },
    {
      "description": "Time-window size in days.",
      "in": "query",
      "name": "days",
      "required": false,
      "schema": {
        "default": 7,
        "description": "Time-window size in days.",
        "maximum": 365,
        "minimum": 1,
        "title": "Days",
        "type": "integer"
      }
    },
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
      "description": "Scope every number in the report to one release. Omit for all releases — the SQL is then byte-identical to before this existed.",
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
        "description": "Scope every number in the report to one release. Omit for all releases — the SQL is then byte-identical to before this existed.",
        "title": "Release Id"
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

## GET `/api/v1/reports/summary/pdf`

Export Summary Report Pdf

Return the summary report as a downloadable PDF.

Source: [backend/app/routers/summary_report.py:92](../../../backend/app/routers/summary_report.py#L92).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`.

Declared Python handler arguments (includes exact role/guard options):

```python
project_id: uuid.UUID=Query(..., description='Project UUID (required for PDF export).'), days: int=Query(7, ge=1, le=365), mode: SummaryMode=Query('window'), db: AsyncSession=Depends(get_db), current_user: User=Depends(get_current_active_user), release_id: Optional[str]=Query(None, description='Scope the exported report to one release.')
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "export_summary_report_pdf_api_v1_reports_summary_pdf_get",
  "parameters": [
    {
      "description": "Project UUID (required for PDF export).",
      "in": "query",
      "name": "project_id",
      "required": true,
      "schema": {
        "description": "Project UUID (required for PDF export).",
        "format": "uuid",
        "title": "Project Id",
        "type": "string"
      }
    },
    {
      "in": "query",
      "name": "days",
      "required": false,
      "schema": {
        "default": 7,
        "maximum": 365,
        "minimum": 1,
        "title": "Days",
        "type": "integer"
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
      "description": "Scope the exported report to one release.",
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
        "description": "Scope the exported report to one release.",
        "title": "Release Id"
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
StreamingResponse(io.BytesIO(pdf_bytes), media_type='application/pdf', headers={'Content-Disposition': f'attachment; filename="{filename}"'})
```

Contract limit: at least one response has an unstructured schema. Read the linked handler/serializer for emitted fields; the empty schema is not a promise of an empty JSON object.
