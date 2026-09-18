# Commit Attribution API

[Documentation home](../../README.md) · [Regeneration](../../handoff/maintenance.md)

Generated from tracked source by `scripts/generate_handoff_reference.py`. Do not edit by hand. The baseline and validation limits are recorded in [verification](../../handoff/verification.md).

## GET `/api/v1/runs/{run_id}/commit-range`

Get Run Commit Range

Return the commit range associated with a run (US-8.1).

If no range exists yet — or the last attempt is older than the service's
re-resolve cooldown — attempts a one-shot resolution (supplied wins →
connector → unavailable) in a dedicated write session so this GET stays
read-only, then returns the freshly-resolved range. The cooldown gate
means repeated GETs on a range-less run do NOT re-run the connector.

Source: [backend/app/routers/commit_attribution.py:59](../../../backend/app/routers/commit_attribution.py#L59).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_run_access.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
run_id: uuid.UUID, db: Any=Depends(get_db), _: Any=Depends(require_run_access())
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "get_run_commit_range_api_v1_runs__run_id__commit_range_get",
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
result
```

Contract limit: at least one response has an unstructured schema. Read the linked handler/serializer for emitted fields; the empty schema is not a promise of an empty JSON object.

## GET `/api/v1/runs/{run_id}/suspects`

Get Run Suspects

Return ranked suspect commits for a newly-failing cluster/test (US-8.2).

Honest ``available: False`` when there's no commit range for the run.
Ranking is deterministic + inspectable (per-commit overlapping files).

Source: [backend/app/routers/commit_attribution.py:81](../../../backend/app/routers/commit_attribution.py#L81).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_run_access.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
run_id: uuid.UUID, cluster_id: Optional[str]=Query(default=None, description='Failure cluster id (e.g. cl_001) to attribute'), fingerprint: Optional[str]=Query(default=None, description='Test fingerprint to attribute (single test)'), db: Any=Depends(get_db), _: Any=Depends(require_run_access())
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "get_run_suspects_api_v1_runs__run_id__suspects_get",
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
      "description": "Failure cluster id (e.g. cl_001) to attribute",
      "in": "query",
      "name": "cluster_id",
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
        "description": "Failure cluster id (e.g. cl_001) to attribute",
        "title": "Cluster Id"
      }
    },
    {
      "description": "Test fingerprint to attribute (single test)",
      "in": "query",
      "name": "fingerprint",
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
        "description": "Test fingerprint to attribute (single test)",
        "title": "Fingerprint"
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
await svc.rank_suspects(db, run, cluster_id=cluster_id, fingerprint=fingerprint)
```

Contract limit: at least one response has an unstructured schema. Read the linked handler/serializer for emitted fields; the empty schema is not a promise of an empty JSON object.
