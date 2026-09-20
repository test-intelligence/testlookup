# Release Readiness API

[Documentation home](../../README.md) · [Regeneration](../../handoff/maintenance.md)

Generated from tracked source by `scripts/generate_handoff_reference.py`. Do not edit by hand. The baseline and validation limits are recorded in [verification](../../handoff/verification.md).

## GET `/api/v1/release-readiness/{run_id}`

Get Release Decision

Retrieve the release readiness decision with full council context:
dimension scores, linked cluster insights, baseline diff, open defects,
and override audit trail.

Source: [backend/app/routers/release_readiness.py:44](../../../backend/app/routers/release_readiness.py#L44).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_run_access.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
run_id: uuid.UUID, allow_advisory: bool=Query(default=False, description='While the review gate is enforced, return ADVISORY_<value> for an unreviewed AI decision instead of PENDING_REVIEW.'), current_user: User=Depends(require_run_access())
```

Direct handler error branches (dependency/service errors can add others):

```python
HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail='Advisory release values require the QA Lead role.')
HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='No release decision found. Trigger deep investigation first.')
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "get_release_decision_api_v1_release_readiness__run_id__get",
  "parameters": [
    {
      "in": "path",
      "name": "run_id",
      "required": true,
      "schema": {
        "format": "uuid",
        "title": "Run Id",
        "type": "string"
      }
    },
    {
      "description": "While the review gate is enforced, return ADVISORY_<value> for an unreviewed AI decision instead of PENDING_REVIEW.",
      "in": "query",
      "name": "allow_advisory",
      "required": false,
      "schema": {
        "default": false,
        "description": "While the review gate is enforced, return ADVISORY_<value> for an unreviewed AI decision instead of PENDING_REVIEW.",
        "title": "Allow Advisory",
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
            "$ref": "#/components/schemas/ReleaseCouncilResponse"
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

## POST `/api/v1/release-readiness/{run_id}/override`

Override Release Decision

Override the AI release decision (QA Lead only).
Records the override in an immutable audit trail with before/after values.

Source: [backend/app/routers/release_readiness.py:100](../../../backend/app/routers/release_readiness.py#L100).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_role.<locals>._check`, `require_run_access.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
run_id: uuid.UUID, body: ReleaseCouncilOverrideRequest, current_user: User=Depends(require_role(UserRole.QA_LEAD, allow_project_key=True)), _: User=Depends(require_run_access())
```

Direct handler error branches (dependency/service errors can add others):

```python
HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='No release decision found for this run.')
HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail='Override reason is required.')
HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail='override_recommendation must be GO, NO_GO, or CONDITIONAL_GO')
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "override_release_decision_api_v1_release_readiness__run_id__override_post",
  "parameters": [
    {
      "in": "path",
      "name": "run_id",
      "required": true,
      "schema": {
        "format": "uuid",
        "title": "Run Id",
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
          "$ref": "#/components/schemas/ReleaseCouncilOverrideRequest"
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
            "$ref": "#/components/schemas/ReleaseCouncilResponse"
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
