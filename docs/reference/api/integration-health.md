# Integration Health API

[Documentation home](../../README.md) · [Regeneration](../../maintainers/maintenance.md)

Generated from tracked source by `scripts/generate_reference.py`. Do not edit by hand. What the checks cover is recorded in [verification](../../maintainers/verification.md).

## GET `/api/v1/integration-health/history/{provider}`

Get Provider History

Probe history for a provider: the deployment-wide history, or -- with
``project_id`` (checked by ``resolve_project_scope`` first) -- that
project's integration.

Source: [backend/app/routers/integration_health.py:323](../../../backend/app/routers/integration_health.py#L323).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_role.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
provider: str, days: int=Query(default=7, ge=1, le=90), project_id: Optional[uuid.UUID]=None, current_user: User=Depends(require_role(UserRole.QA_LEAD)), db: AsyncSession=Depends(get_db)
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
        "title": "Project Id"
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

``project_id`` probes that project's GitHub or GitLab integration
(``provider=github|gitlab``); the id passes ``resolve_project_scope`` before
anything is read. Without a provider and a project: every deployment-wide
probe plus every per-project integration the reader may see. The request
session's connection is handed back before any probe makes an HTTP call.

Source: [backend/app/routers/integration_health.py:236](../../../backend/app/routers/integration_health.py#L236).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_role.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
provider: Optional[str]=None, project_id: Optional[uuid.UUID]=None, current_user: User=Depends(require_role(UserRole.QA_LEAD)), db: AsyncSession=Depends(get_db)
```

Direct handler error branches (dependency/service errors can add others):

```python
HTTPException(status_code=404, detail=f'This project has no enabled {label} integration.')
HTTPException(status_code=422, detail='A per-project probe needs provider=github or provider=gitlab.')
HTTPException(status_code=422, detail='GitLab is probed per project: pass project_id.')
HTTPException(status_code=422, detail=f"Unknown integration provider: {provider}. Supported: {', '.join(sorted(supported))}")
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
        "title": "Project Id"
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
_probe_answer(results)
```

Contract limit: at least one response has an unstructured schema. Read the linked handler/serializer for emitted fields; the empty schema is not a promise of an empty JSON object.

## GET `/api/v1/integration-health/status`

Get All Status

Latest health per integration.

``scope=global`` (the default, every existing consumer): the
deployment-wide rows. ``scope=all``: those, then one row per enabled
per-project GitHub / GitLab integration of the reader's projects (an admin:
every project), then one row per paused outbound webhook in them. Five
reads for ``all``, none per row.

Source: [backend/app/routers/integration_health.py:188](../../../backend/app/routers/integration_health.py#L188).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_role.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
scope: str='global', current_user: User=Depends(require_role(UserRole.QA_LEAD)), db: AsyncSession=Depends(get_db)
```

Direct handler error branches (dependency/service errors can add others):

```python
HTTPException(status_code=422, detail='scope must be global or all')
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "get_all_status_api_v1_integration_health_status_get",
  "parameters": [
    {
      "in": "query",
      "name": "scope",
      "required": false,
      "schema": {
        "default": "global",
        "title": "Scope",
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
            "items": {
              "$ref": "#/components/schemas/IntegrationHealthRow"
            },
            "title": "Response Get All Status Api V1 Integration Health Status Get",
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

The trend is deployment-wide (W10 INT-04): per-project probes
(``scope_key <> ''``) stay out of both statements.

Source: [backend/app/routers/integration_health.py:363](../../../backend/app/routers/integration_health.py#L363).

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
