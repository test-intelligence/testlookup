# Integration Health API

[Documentation home](../../README.md) · [Regeneration](../../handoff/maintenance.md)

Generated from tracked source by `scripts/generate_handoff_reference.py`. Do not edit by hand. The baseline and validation limits are recorded in [verification](../../handoff/verification.md).

## GET `/api/v1/integration-health/history/{provider}`

Get Provider History

Get probe history for a specific provider.

Source: [backend/app/routers/integration_health.py:93](../../../backend/app/routers/integration_health.py#L93).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_role.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
provider: str, days: int=Query(default=7, ge=1, le=90), current_user: User=Depends(require_role(UserRole.QA_LEAD)), db: AsyncSession=Depends(get_db)
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "get_provider_history_api_v1_integration_health_history__provider__get",
  "parameters": [
    {
      "in": "path",
      "name": "provider",
      "required": true,
      "schema": {
        "title": "Provider",
        "type": "string"
      }
    },
    {
      "in": "query",
      "name": "days",
      "required": false,
      "schema": {
        "default": 7,
        "maximum": 90,
        "minimum": 1,
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
[{'id': str(p.id), 'status': p.status, 'response_ms': p.response_ms, 'message': p.message, 'auth_valid': p.auth_valid, 'payload_valid': p.payload_valid, 'checked_at': p.checked_at.isoformat() if p.checked_at else None} for p in probes]
```

Contract limit: at least one response has an unstructured schema. Read the linked handler/serializer for emitted fields; the empty schema is not a promise of an empty JSON object.

## POST `/api/v1/integration-health/probe`

Trigger Probe

Trigger an on-demand health probe for all or a specific provider (QA_LEAD+).

Source: [backend/app/routers/integration_health.py:43](../../../backend/app/routers/integration_health.py#L43).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_role.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
provider: Optional[str]=None, current_user: User=Depends(require_role(UserRole.QA_LEAD)), db: AsyncSession=Depends(get_db)
```

Direct handler error branches (dependency/service errors can add others):

```python
HTTPException(status_code=422, detail=f"Unknown integration provider: {provider}. Supported: {', '.join(sorted(ALL_PROBES))}")
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "trigger_probe_api_v1_integration_health_probe_post",
  "parameters": [
    {
      "in": "query",
      "name": "provider",
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
        "title": "Provider"
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
[{'provider': r.provider, 'status': r.status, 'response_ms': r.response_ms, 'message': r.message, 'auth_valid': r.auth_valid, 'payload_valid': r.payload_valid} for r in results]
```

Contract limit: at least one response has an unstructured schema. Read the linked handler/serializer for emitted fields; the empty schema is not a promise of an empty JSON object.

## GET `/api/v1/integration-health/status`

Get All Status

Get latest health status for all integration providers.

Source: [backend/app/routers/integration_health.py:19](../../../backend/app/routers/integration_health.py#L19).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_role.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
current_user: User=Depends(require_role(UserRole.QA_LEAD)), db: AsyncSession=Depends(get_db)
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "get_all_status_api_v1_integration_health_status_get",
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
[{'provider': hc.provider, 'status': hc.status, 'last_checked_at': hc.last_checked_at.isoformat() if hc.last_checked_at else None, 'message': hc.message, 'response_ms': hc.response_ms, 'consecutive_failures': hc.consecutive_failures or 0, 'last_success_at': hc.last_success_at.isoformat() if hc.last_success_at else None} for hc in checks]
```

Contract limit: at least one response has an unstructured schema. Read the linked handler/serializer for emitted fields; the empty schema is not a promise of an empty JSON object.

## GET `/api/v1/integration-health/trends`

Get Health Trends

Get aggregated health trends for all providers over a period.

Every status the probe service persists gets its own counter. The probers
emit five (``skipped`` is dropped before insert, see
``integration_probe_service._persist``), and reporting only three left
``auth_error`` and ``timeout`` probes counted in ``total_probes`` — so they
dragged ``uptime_pct`` down — while appearing in no column at all. A
provider whose token had expired rendered as 0% uptime with 0 healthy,
0 degraded and 0 down, which reads as "no data" rather than "your
credentials are wrong".

Source: [backend/app/routers/integration_health.py:126](../../../backend/app/routers/integration_health.py#L126).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_role.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
days: int=Query(default=7, ge=1, le=30), current_user: User=Depends(require_role(UserRole.QA_LEAD)), db: AsyncSession=Depends(get_db)
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "get_health_trends_api_v1_integration_health_trends_get",
  "parameters": [
    {
      "in": "query",
      "name": "days",
      "required": false,
      "schema": {
        "default": 7,
        "maximum": 30,
        "minimum": 1,
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
list(trends.values())
```

Contract limit: at least one response has an unstructured schema. Read the linked handler/serializer for emitted fields; the empty schema is not a promise of an empty JSON object.
