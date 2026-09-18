# Analysis Report API

[Documentation home](../../README.md) · [Regeneration](../../handoff/maintenance.md)

Generated from tracked source by `scripts/generate_handoff_reference.py`. Do not edit by hand. The baseline and validation limits are recorded in [verification](../../handoff/verification.md).

## GET `/api/v1/projects/{project_id}/reports/analysis`

Download Analysis Report

Build and return the self-contained HTML analysis report.

Source: [backend/app/routers/analysis_report.py:37](../../../backend/app/routers/analysis_report.py#L37).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_project_access.<locals>._check`, `require_role.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
project_id: uuid.UUID, window: Literal['1d', '7d']=Query('7d', description='Report window: 1d (daily) or 7d (weekly).'), db: AsyncSession=Depends(get_db), _role: User=Depends(require_role(UserRole.QA_ENGINEER)), _: User=Depends(require_project_access())
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "download_analysis_report_api_v1_projects__project_id__reports_analysis_get",
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
      "description": "Report window: 1d (daily) or 7d (weekly).",
      "in": "query",
      "name": "window",
      "required": false,
      "schema": {
        "default": "7d",
        "description": "Report window: 1d (daily) or 7d (weekly).",
        "enum": [
          "1d",
          "7d"
        ],
        "title": "Window",
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
Response(content=html, media_type='text/html; charset=utf-8', headers={'Content-Disposition': f'attachment; filename="{filename}"'})
```

Contract limit: at least one response has an unstructured schema. Read the linked handler/serializer for emitted fields; the empty schema is not a promise of an empty JSON object.
