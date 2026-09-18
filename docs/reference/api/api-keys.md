# Api Keys API

[Documentation home](../../README.md) · [Regeneration](../../handoff/maintenance.md)

Generated from tracked source by `scripts/generate_handoff_reference.py`. Do not edit by hand. The baseline and validation limits are recorded in [verification](../../handoff/verification.md).

## GET `/api/v1/keys`

List Api Keys

List active API keys. Non-admin users see only their own keys.
ADMIN can filter by project_id to see all keys bound to a project.

A caller authenticated with a project-bound key sees only keys bound to
that project, and naming another project is a 403 (re-audit N20).

Source: [backend/app/routers/api_keys.py:277](../../../backend/app/routers/api_keys.py#L277).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_role.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
project_id: Optional[uuid.UUID]=Query(None, description='Filter by project (ADMIN only)'), db: AsyncSession=Depends(get_db), current_user: User=Depends(require_role(UserRole.QA_ENGINEER))
```

Direct handler error branches (dependency/service errors can add others):

```python
HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail='This API key is restricted to a different project')
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "list_api_keys_api_v1_keys_get",
  "parameters": [
    {
      "description": "Filter by project (ADMIN only)",
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
        "description": "Filter by project (ADMIN only)",
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
          "schema": {
            "items": {
              "$ref": "#/components/schemas/ApiKeyResponse"
            },
            "title": "Response List Api Keys Api V1 Keys Get",
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

## POST `/api/v1/keys`

Create Api Key

Generate a new scoped API key. The raw key is only shown once.

ADMIN can supply ``project_id`` to restrict the key to a single project
and ``target_user_id`` to create a key on behalf of another user.

A caller authenticated with a project-bound key may mint only a key bound
to that same project, for its own owner (re-audit N20). That is what CI
needs to rotate its key, and nothing wider.

A caller authenticated with an API key also grants no more than it holds
(QA-R3-11):

* scopes: if the caller's key is scoped, the new key's scopes must be a
  subset of them. Omitting ``scopes`` inherits the caller's; an explicit
  empty list (a full-access key) is refused. A legacy unscoped caller
  mints as before.
* expiry: the new key may not expire later than the caller's key.
  Omitting ``expires_days`` inherits the caller's expiry. A caller with
  no expiry mints as before.

So ``testlookup keys create``, which sends only a name, rotates a scoped,
expiring CI key into one with the same scopes and the same end date.

Scopes: an empty list is a full-access key. ``stream:write`` is required
by the streaming ingest endpoints. ``project:admin`` is required by the
project-administration routes a project-bound key may use (run deletion,
project reset, retention and deletion jobs, member removal, release/phase
deletion, compliance packs, release-gate policies).

Source: [backend/app/routers/api_keys.py:60](../../../backend/app/routers/api_keys.py#L60).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_role.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
payload: ApiKeyCreate, db: AsyncSession=Depends(get_db), current_user: User=Depends(require_role(UserRole.QA_ENGINEER))
```

Direct handler error branches (dependency/service errors can add others):

```python
HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail='Only ADMIN can create keys for other users')
HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail='Only ADMIN can create project-scoped API keys')
HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail='This API key can grant only the scopes it holds (' + ', '.join(grant.scopes) + '); it does not hold: ' + ', '.join(beyond))
HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail='This API key is bound to one project; it can only create keys bound to that same project. Set project_id to it.')
HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail='This API key is bound to one project; it can only create keys for its own owner')
HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail='This API key is scoped; it cannot create a full-access key (an empty scope list). Request a subset of: ' + ', '.join(grant.scopes))
HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=f'A key created with an API key may not outlive it: this key expires at {grant.expires_at.isoformat()}. Omit expires_days to inherit that date, or ask for fewer days.')
HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Project not found')
HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Target user not found')
HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail='Unknown API key scope(s): ' + ', '.join(unknown) + '. Valid scopes: ' + ', '.join(API_KEY_SCOPES))
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "create_api_key_api_v1_keys_post",
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
          "$ref": "#/components/schemas/ApiKeyCreate"
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
            "$ref": "#/components/schemas/ApiKeyCreatedResponse"
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

## DELETE `/api/v1/keys/{key_id}`

Revoke Api Key

Revoke (soft-delete) an API key. Only the owner can revoke their own keys.

A caller authenticated with a project-bound key may revoke only keys bound
to that project; ``require_api_key_owner`` refuses the rest (re-audit N20).

Source: [backend/app/routers/api_keys.py:317](../../../backend/app/routers/api_keys.py#L317).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_api_key_owner.<locals>._check`, `require_role.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
key_id: uuid.UUID, db: AsyncSession=Depends(get_db), current_user: User=Depends(require_role(UserRole.QA_ENGINEER)), _: User=Depends(require_api_key_owner())
```

Direct handler error branches (dependency/service errors can add others):

```python
HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=PROJECT_ADMIN_SCOPE_DETAIL)
HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='API key not found')
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "revoke_api_key_api_v1_keys__key_id__delete",
  "parameters": [
    {
      "in": "path",
      "name": "key_id",
      "required": true,
      "schema": {
        "format": "uuid",
        "title": "Key Id",
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
    "204": {
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
