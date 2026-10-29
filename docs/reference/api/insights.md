# Insights API

[Documentation home](../../README.md) · [Regeneration](../../maintainers/maintenance.md)

Generated from tracked source by `scripts/generate_reference.py`. Do not edit by hand. What the checks cover is recorded in [verification](../../maintainers/verification.md).

## GET `/api/v1/projects/{project_id}/insights`

List Project Insights

The project's insights confirmed in the range (open first, then by
severity, newest first), the counts beside them, and the four detectors'
last evaluations -- a detector that could not measure says why.

Source: [backend/app/routers/insights.py:45](../../../backend/app/routers/insights.py#L45).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_project_access.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
project_id: uuid.UUID, range_key: str=Query(INSIGHT_RANGE_DEFAULT, alias='range', description='How far back an insight must have been confirmed: 24h or 7d.'), include_dismissed: bool=Query(False, description='Also list the insights someone dismissed.'), limit: int=Query(INSIGHT_LIST_DEFAULT, ge=1, le=INSIGHT_LIST_MAX, description='Rows to return; `total` counts all.'), db: AsyncSession=Depends(get_db), _: User=Depends(require_project_access())
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "list_project_insights_api_v1_projects__project_id__insights_get",
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
      "description": "How far back an insight must have been confirmed: 24h or 7d.",
      "in": "query",
      "name": "range",
      "required": false,
      "schema": {
        "default": "7d",
        "description": "How far back an insight must have been confirmed: 24h or 7d.",
        "title": "Range",
        "type": "string"
      }
    },
    {
      "description": "Also list the insights someone dismissed.",
      "in": "query",
      "name": "include_dismissed",
      "required": false,
      "schema": {
        "default": false,
        "description": "Also list the insights someone dismissed.",
        "title": "Include Dismissed",
        "type": "boolean"
      }
    },
    {
      "description": "Rows to return; `total` counts all.",
      "in": "query",
      "name": "limit",
      "required": false,
      "schema": {
        "default": 50,
        "description": "Rows to return; `total` counts all.",
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
            "$ref": "#/components/schemas/InsightListResponse"
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

## POST `/api/v1/projects/{project_id}/insights/{insight_id}/dismiss`

Dismiss Project Insight

Hide an insight for everyone in the project for the rest of its
episode (QA engineer or above). There is no un-dismiss; "Show dismissed"
keeps it readable.

Source: [backend/app/routers/insights.py:69](../../../backend/app/routers/insights.py#L69).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_project_access.<locals>._check`, `require_role.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
project_id: uuid.UUID, insight_id: uuid.UUID, db: AsyncSession=Depends(get_db), current_user: User=Depends(require_role(UserRole.QA_ENGINEER)), _: User=Depends(require_project_access())
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "dismiss_project_insight_api_v1_projects__project_id__insights__insight_id__dismiss_post",
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
      "name": "insight_id",
      "required": true,
      "schema": {
        "format": "uuid",
        "title": "Insight Id",
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
            "$ref": "#/components/schemas/InsightItem"
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
