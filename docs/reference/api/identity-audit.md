# Identity Audit API

[Documentation home](../../README.md) · [Regeneration](../../maintainers/maintenance.md)

Generated from tracked source by `scripts/generate_reference.py`. Do not edit by hand. What the checks cover is recorded in [verification](../../maintainers/verification.md).

## GET `/api/v1/identity/events`

List Identity Events

List identity events with filtering (ADMIN only).

Source: [backend/app/routers/identity_events.py:165](../../../backend/app/routers/identity_events.py#L165).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_role.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
event_type: Optional[IdentityEventType]=None, user_id: Optional[uuid.UUID]=None, sso_config_id: Optional[uuid.UUID]=None, success: Optional[bool]=None, days: int=Query(default=30, ge=1, le=365), page: int=Query(default=1, ge=1), page_size: int=Query(default=50, ge=1, le=200), current_user: User=Depends(require_role(UserRole.ADMIN)), db: AsyncSession=Depends(get_db)
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "list_identity_events_api_v1_identity_events_get",
  "parameters": [
    {
      "in": "query",
      "name": "event_type",
      "required": false,
      "schema": {
        "anyOf": [
          {
            "$ref": "#/components/schemas/IdentityEventType"
          },
          {
            "type": "null"
          }
        ],
        "title": "Event Type"
      }
    },
    {
      "in": "query",
      "name": "user_id",
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
        "title": "User Id"
      }
    },
    {
      "in": "query",
      "name": "sso_config_id",
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
        "title": "Sso Config Id"
      }
    },
    {
      "in": "query",
      "name": "success",
      "required": false,
      "schema": {
        "anyOf": [
          {
            "type": "boolean"
          },
          {
            "type": "null"
          }
        ],
        "title": "Success"
      }
    },
    {
      "in": "query",
      "name": "days",
      "required": false,
      "schema": {
        "default": 30,
        "maximum": 365,
        "minimum": 1,
        "title": "Days",
        "type": "integer"
      }
    },
    {
      "in": "query",
      "name": "page",
      "required": false,
      "schema": {
        "default": 1,
        "minimum": 1,
        "title": "Page",
        "type": "integer"
      }
    },
    {
      "in": "query",
      "name": "page_size",
      "required": false,
      "schema": {
        "default": 50,
        "maximum": 200,
        "minimum": 1,
        "title": "Page Size",
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
            "$ref": "#/components/schemas/IdentityEventListResponse"
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

## POST `/api/v1/identity/retention/disable`

Disable Identity Retention Policy

Disable future identity expiry without releasing any hold.

Source: [backend/app/routers/identity_events.py:96](../../../backend/app/routers/identity_events.py#L96).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_role.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
payload: IdentityRetentionReason, current_user: User=Depends(require_role(UserRole.ADMIN)), db: AsyncSession=Depends(get_db)
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "disable_identity_retention_policy_api_v1_identity_retention_disable_post",
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
  "requestBody": {
    "content": {
      "application/json": {
        "schema": {
          "$ref": "#/components/schemas/IdentityRetentionReason"
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
            "$ref": "#/components/schemas/IdentityRetentionRead"
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

## POST `/api/v1/identity/retention/holds`

Place Identity Retention Hold

Place an independent instance identity hold; all live holds block expiry.

Source: [backend/app/routers/identity_events.py:111](../../../backend/app/routers/identity_events.py#L111).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_role.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
payload: IdentityRetentionHoldCreate, current_user: User=Depends(require_role(UserRole.ADMIN)), db: AsyncSession=Depends(get_db)
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "place_identity_retention_hold_api_v1_identity_retention_holds_post",
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
  "requestBody": {
    "content": {
      "application/json": {
        "schema": {
          "$ref": "#/components/schemas/IdentityRetentionHoldCreate"
        }
      }
    },
    "required": true
  },
  "responses": {
    "201": {
      "content": {
        "application/json": {
          "schema": {
            "$ref": "#/components/schemas/IdentityRetentionHold"
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

## POST `/api/v1/identity/retention/holds/{hold_id}/release`

Release Identity Retention Hold

Release only this hold with a reason; overlapping holds remain absolute.

Source: [backend/app/routers/identity_events.py:128](../../../backend/app/routers/identity_events.py#L128).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_role.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
hold_id: uuid.UUID, payload: IdentityRetentionHoldRelease, current_user: User=Depends(require_role(UserRole.ADMIN)), db: AsyncSession=Depends(get_db)
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "release_identity_retention_hold_api_v1_identity_retention_holds__hold_id__release_post",
  "parameters": [
    {
      "in": "path",
      "name": "hold_id",
      "required": true,
      "schema": {
        "format": "uuid",
        "title": "Hold Id",
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
          "$ref": "#/components/schemas/IdentityRetentionHoldRelease"
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
            "$ref": "#/components/schemas/IdentityRetentionRead"
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

## GET `/api/v1/identity/retention/policy`

Read Identity Retention Policy

Read the separate, default-off instance identity policy and its holds.

Source: [backend/app/routers/identity_events.py:48](../../../backend/app/routers/identity_events.py#L48).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_role.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
current_user: User=Depends(require_role(UserRole.ADMIN)), db: AsyncSession=Depends(get_db)
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "read_identity_retention_policy_api_v1_identity_retention_policy_get",
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
          "schema": {
            "$ref": "#/components/schemas/IdentityRetentionRead"
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

## POST `/api/v1/identity/retention/policy`

Approve Identity Retention Policy

Approve routine-event expiry with a fresh preview and typed confirmation.

Requires an interactive instance administrator. Existing project retention
enablement never authorizes this separate policy.

Source: [backend/app/routers/identity_events.py:75](../../../backend/app/routers/identity_events.py#L75).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_role.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
payload: IdentityRetentionApproval, current_user: User=Depends(require_role(UserRole.ADMIN)), db: AsyncSession=Depends(get_db)
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "approve_identity_retention_policy_api_v1_identity_retention_policy_post",
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
  "requestBody": {
    "content": {
      "application/json": {
        "schema": {
          "$ref": "#/components/schemas/IdentityRetentionApproval"
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
            "$ref": "#/components/schemas/IdentityRetentionRead"
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

## GET `/api/v1/identity/retention/preview`

Preview Identity Retention

Count aged record classes without reading event content or deleting rows.

Source: [backend/app/routers/identity_events.py:60](../../../backend/app/routers/identity_events.py#L60).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_role.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
retention_days: Optional[int]=Query(default=None, ge=365, le=3650), current_user: User=Depends(require_role(UserRole.ADMIN)), db: AsyncSession=Depends(get_db)
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "preview_identity_retention_api_v1_identity_retention_preview_get",
  "parameters": [
    {
      "in": "query",
      "name": "retention_days",
      "required": false,
      "schema": {
        "anyOf": [
          {
            "maximum": 3650,
            "minimum": 365,
            "type": "integer"
          },
          {
            "type": "null"
          }
        ],
        "title": "Retention Days"
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
            "$ref": "#/components/schemas/IdentityRetentionPreview"
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

## POST `/api/v1/identity/retention/purge`

Purge Identity Retention Batch

Execute one approved, unheld batch of at most 500 routine events.

Source: [backend/app/routers/identity_events.py:146](../../../backend/app/routers/identity_events.py#L146).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_role.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
payload: IdentityRetentionPurge, current_user: User=Depends(require_role(UserRole.ADMIN)), db: AsyncSession=Depends(get_db)
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "purge_identity_retention_batch_api_v1_identity_retention_purge_post",
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
  "requestBody": {
    "content": {
      "application/json": {
        "schema": {
          "$ref": "#/components/schemas/IdentityRetentionPurge"
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
            "$ref": "#/components/schemas/IdentityRetentionPurgeResult"
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

## GET `/api/v1/identity/sync-status`

Get Identity Sync Status

Get aggregated identity sync health (ADMIN only).

Source: [backend/app/routers/identity_events.py:206](../../../backend/app/routers/identity_events.py#L206).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_role.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
sso_config_id: Optional[uuid.UUID]=None, current_user: User=Depends(require_role(UserRole.ADMIN)), db: AsyncSession=Depends(get_db)
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "get_identity_sync_status_api_v1_identity_sync_status_get",
  "parameters": [
    {
      "in": "query",
      "name": "sso_config_id",
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
        "title": "Sso Config Id"
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
            "$ref": "#/components/schemas/IdentitySyncStatus"
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
