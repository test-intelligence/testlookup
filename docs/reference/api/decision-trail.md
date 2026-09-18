# Decision Trail API

[Documentation home](../../README.md) · [Regeneration](../../handoff/maintenance.md)

Generated from tracked source by `scripts/generate_handoff_reference.py`. Do not edit by hand. The baseline and validation limits are recorded in [verification](../../handoff/verification.md).

## GET `/api/v1/runs/{run_id}/decision-trail`

AI decision trail for a test run

Return the full AI decision trail for a test run.

The trail contains:
  - Pipeline run metadata (status, started_at, completed_at, cost).
  - Per-stage summary with decision_log, analysis_mode, fallback reason.
  - Per-test routing showing which engine ran and any confidence
    adjustments or retries.
  - Workflow router events (fast-path skips, specialist stage picks).

Access control: the caller must have access to the run's project.

Source: [backend/app/routers/decision_trail.py:28](../../../backend/app/routers/decision_trail.py#L28).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_run_access.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
run_id: uuid.UUID, db: AsyncSession=Depends(get_db), current_user: User=Depends(get_current_active_user), _: User=Depends(require_run_access())
```

Direct handler error branches (dependency/service errors can add others):

```python
HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Test run not found')
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "get_decision_trail_api_v1_runs__run_id__decision_trail_get",
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
  "responses": {
    "200": {
      "content": {
        "application/json": {
          "schema": {
            "$ref": "#/components/schemas/DecisionTrailResponse"
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
