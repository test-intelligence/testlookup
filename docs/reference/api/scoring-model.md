# Scoring Model API

[Documentation home](../../README.md) · [Regeneration](../../maintainers/maintenance.md)

Generated from tracked source by `scripts/generate_reference.py`. Do not edit by hand. What the checks cover is recorded in [verification](../../maintainers/verification.md).

## GET `/api/v1/scoring-model`

Get Scoring Model

Return the risk scoring model.

Response shape:
{
  "version": 1,
  "go_threshold": 20,
  "no_go_threshold": 55,
  "dimensions": [
    { "name": "user_impact", "weight": 0.25, "description": "..." },
    ...
  ]
}

Scoped (``project_id`` or ``run_id``), it adds ``pass_rate_minimum``,
``pass_rate_hard_floor_factor`` and ``policy`` =
``{policy_id, name, version, level}`` where ``level`` is ``project``,
``system`` or ``hardcoded`` (no active policy: config defaults).

Source: [backend/app/routers/scoring.py:59](../../../backend/app/routers/scoring.py#L59).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`.

Declared Python handler arguments (includes exact role/guard options):

```python
project_id: Optional[str]=Query(None, description="Return the model this project's verdict applies (its active release-gate policy's thresholds and weights). 403 without access."), run_id: Optional[str]=Query(None, description="As project_id, for the run's project. 404 for a run you cannot read."), db: AsyncSession=Depends(get_db), current_user: User=Depends(get_current_active_user)
```

Direct handler error branches (dependency/service errors can add others):

```python
HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='run_id does not belong to project_id')
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "get_scoring_model_api_v1_scoring_model_get",
  "parameters": [
    {
      "description": "Return the model this project's verdict applies (its active release-gate policy's thresholds and weights). 403 without access.",
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
        "description": "Return the model this project's verdict applies (its active release-gate policy's thresholds and weights). 403 without access.",
        "title": "Project Id"
      }
    },
    {
      "description": "As project_id, for the run's project. 404 for a run you cannot read.",
      "in": "query",
      "name": "run_id",
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
        "description": "As project_id, for the run's project. 404 for a run you cannot read.",
        "title": "Run Id"
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
get_scoring_model_info()
get_scoring_model_info(policy, level=level)
```

Contract limit: at least one response has an unstructured schema. Read the linked handler/serializer for emitted fields; the empty schema is not a promise of an empty JSON object.
