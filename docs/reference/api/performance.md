# Performance API

[Documentation home](../../README.md) · [Regeneration](../../handoff/maintenance.md)

Generated from tracked source by `scripts/generate_handoff_reference.py`. Do not edit by hand. The baseline and validation limits are recorded in [verification](../../handoff/verification.md).

## GET `/api/v1/performance/budgets`

Get Performance Budgets

Return all codified performance budgets and scale scenarios.

Source: [backend/app/routers/performance.py:14](../../../backend/app/routers/performance.py#L14).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_role.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
current_user: User=Depends(require_role(UserRole.QA_LEAD))
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "get_performance_budgets_api_v1_performance_budgets_get",
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
get_all_budgets()
```

Contract limit: at least one response has an unstructured schema. Read the linked handler/serializer for emitted fields; the empty schema is not a promise of an empty JSON object.

## GET `/api/v1/performance/search-config`

Get Search Config

Return current search and indexing configuration.

Source: [backend/app/routers/performance.py:24](../../../backend/app/routers/performance.py#L24).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_role.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
current_user: User=Depends(require_role(UserRole.QA_LEAD))
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "get_search_config_api_v1_performance_search_config_get",
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
{'index_batch_size': settings.SEARCH_INDEX_BATCH_SIZE, 'incremental_limit': settings.SEARCH_INDEX_INCREMENTAL_LIMIT, 'query_timeout_ms': settings.SEARCH_QUERY_TIMEOUT_MS, 'max_results': settings.SEARCH_MAX_RESULTS, 'pg_pool_size': pool['pool_size'], 'pg_max_overflow': pool['max_overflow'], 'pg_pool_recycle': pool['pool_recycle'], 'pg_process_role': settings.PG_PROCESS_ROLE, 'pg_processes_per_pod': settings.PG_PROCESSES_PER_POD, 'pg_fleet_max_connections': settings.PG_FLEET_MAX_CONNECTIONS, 'pg_fleet_operational_reserve': settings.PG_FLEET_OPERATIONAL_RESERVE, 'pg_fleet_superuser_reserved_connections': settings.PG_FLEET_SUPERUSER_RESERVED_CONNECTIONS, 'pg_fleet_reserved_connections': settings.PG_FLEET_RESERVED_CONNECTIONS, 'pg_fleet_migration_connections': settings.PG_FLEET_MIGRATION_CONNECTIONS, 'pg_fleet_required_connections': settings.PG_FLEET_REQUIRED_CONNECTIONS, 'celery_worker_concurrency': settings.CELERY_WORKER_CONCURRENCY}
```

Contract limit: at least one response has an unstructured schema. Read the linked handler/serializer for emitted fields; the empty schema is not a promise of an empty JSON object.
