# Sso Saml API

[Documentation home](../../README.md) · [Regeneration](../../handoff/maintenance.md)

Generated from tracked source by `scripts/generate_handoff_reference.py`. Do not edit by hand. The baseline and validation limits are recorded in [verification](../../handoff/verification.md).

## POST `/api/v1/sso/acs`

Saml Acs

SAML Assertion Consumer Service — processes the SAML Response from the IdP.

This endpoint receives the base64-encoded SAMLResponse via form POST,
validates the assertion, and either logs in an existing user or JIT-provisions
a new one.

Source: [backend/app/routers/sso.py:95](../../../backend/app/routers/sso.py#L95).

Dependency chain: `get_db`.

Declared Python handler arguments (includes exact role/guard options):

```python
request: Request, db: AsyncSession=Depends(get_db)
```

Direct handler error branches (dependency/service errors can add others):

```python
HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='Missing SAMLResponse in form data')
HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='No active SSO configuration')
HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='SSO is misconfigured. Contact your administrator.')
HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='SAML assertion validation failed.')
HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail='Account is deactivated')
HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='SSO is not enabled')
HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail='SAMLResponse too large')
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "saml_acs_api_v1_sso_acs_post",
  "responses": {
    "200": {
      "content": {
        "application/json": {
          "schema": {
            "$ref": "#/components/schemas/SSOLoginResponse"
          }
        }
      },
      "description": "Successful Response"
    }
  }
}
```

## GET `/api/v1/sso/configs`

List Sso Configs

List all SSO configurations (ADMIN only).

Source: [backend/app/routers/sso.py:251](../../../backend/app/routers/sso.py#L251).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_role.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
current_user: User=Depends(require_role(UserRole.ADMIN)), db: AsyncSession=Depends(get_db)
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "list_sso_configs_api_v1_sso_configs_get",
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
            "items": {
              "$ref": "#/components/schemas/SSOConfigResponse"
            },
            "title": "Response List Sso Configs Api V1 Sso Configs Get",
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

## POST `/api/v1/sso/configs`

Create Sso Config

Create a new SSO configuration (ADMIN only).

Source: [backend/app/routers/sso.py:280](../../../backend/app/routers/sso.py#L280).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_role.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
payload: SSOConfigCreate, request: Request, current_user: User=Depends(require_role(UserRole.ADMIN)), db: AsyncSession=Depends(get_db)
```

Direct handler error branches (dependency/service errors can add others):

```python
HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Invalid role '{role_value}' in role_mapping for group '{group_name}'. Valid roles: {', '.join(valid_roles)}")
HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f'Invalid IdP certificate: {cert_msg}')
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "create_sso_config_api_v1_sso_configs_post",
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
          "$ref": "#/components/schemas/SSOConfigCreate"
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
            "$ref": "#/components/schemas/SSOConfigResponse"
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

## DELETE `/api/v1/sso/configs/{config_id}`

Delete Sso Config

Delete an SSO configuration (ADMIN only).

Source: [backend/app/routers/sso.py:431](../../../backend/app/routers/sso.py#L431).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_role.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
config_id: uuid.UUID, request: Request, current_user: User=Depends(require_role(UserRole.ADMIN)), db: AsyncSession=Depends(get_db)
```

Direct handler error branches (dependency/service errors can add others):

```python
HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='SSO configuration not found')
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "delete_sso_config_api_v1_sso_configs__config_id__delete",
  "parameters": [
    {
      "in": "path",
      "name": "config_id",
      "required": true,
      "schema": {
        "format": "uuid",
        "title": "Config Id",
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

## GET `/api/v1/sso/configs/{config_id}`

Get Sso Config

Get a specific SSO configuration (ADMIN only).

Source: [backend/app/routers/sso.py:264](../../../backend/app/routers/sso.py#L264).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_role.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
config_id: uuid.UUID, current_user: User=Depends(require_role(UserRole.ADMIN)), db: AsyncSession=Depends(get_db)
```

Direct handler error branches (dependency/service errors can add others):

```python
HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='SSO configuration not found')
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "get_sso_config_api_v1_sso_configs__config_id__get",
  "parameters": [
    {
      "in": "path",
      "name": "config_id",
      "required": true,
      "schema": {
        "format": "uuid",
        "title": "Config Id",
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
            "$ref": "#/components/schemas/SSOConfigResponse"
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

## PATCH `/api/v1/sso/configs/{config_id}`

Update Sso Config

Update an SSO configuration (ADMIN only).

Source: [backend/app/routers/sso.py:341](../../../backend/app/routers/sso.py#L341).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_role.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
config_id: uuid.UUID, payload: SSOConfigUpdate, request: Request, current_user: User=Depends(require_role(UserRole.ADMIN)), db: AsyncSession=Depends(get_db)
```

Direct handler error branches (dependency/service errors can add others):

```python
HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Invalid role '{role_value}' in role_mapping for group '{group_name}'")
HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f'Invalid IdP certificate: {cert_msg}')
HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='SSO configuration not found')
HTTPException(status_code=status.HTTP_409_CONFLICT, detail='Another SSO configuration was activated concurrently; retry')
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "update_sso_config_api_v1_sso_configs__config_id__patch",
  "parameters": [
    {
      "in": "path",
      "name": "config_id",
      "required": true,
      "schema": {
        "format": "uuid",
        "title": "Config Id",
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
          "$ref": "#/components/schemas/SSOConfigUpdate"
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
            "$ref": "#/components/schemas/SSOConfigResponse"
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

## POST `/api/v1/sso/configs/{config_id}/test`

Test Sso Connection

Test an SSO configuration by validating the certificate and IdP metadata (ADMIN only).

Source: [backend/app/routers/sso.py:463](../../../backend/app/routers/sso.py#L463).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_role.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
config_id: uuid.UUID, request: Request, current_user: User=Depends(require_role(UserRole.ADMIN)), db: AsyncSession=Depends(get_db)
```

Direct handler error branches (dependency/service errors can add others):

```python
HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='SSO configuration not found')
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "test_sso_connection_api_v1_sso_configs__config_id__test_post",
  "parameters": [
    {
      "in": "path",
      "name": "config_id",
      "required": true,
      "schema": {
        "format": "uuid",
        "title": "Config Id",
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
            "$ref": "#/components/schemas/SSOTestConnectionResponse"
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

## GET `/api/v1/sso/login-url`

Get Sso Login Url

Get the SSO login redirect URL for SP-initiated login.
Returns the IdP SSO URL the frontend should redirect to.

Source: [backend/app/routers/sso.py:65](../../../backend/app/routers/sso.py#L65).

Dependency chain: `get_db`.

Declared Python handler arguments (includes exact role/guard options):

```python
db: AsyncSession=Depends(get_db)
```

Direct handler error branches (dependency/service errors can add others):

```python
HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='No active SSO configuration found')
HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='SSO is not enabled')
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "get_sso_login_url_api_v1_sso_login_url_get",
  "responses": {
    "200": {
      "content": {
        "application/json": {
          "schema": {
            "additionalProperties": true,
            "title": "Response Get Sso Login Url Api V1 Sso Login Url Get",
            "type": "object"
          }
        }
      },
      "description": "Successful Response"
    }
  }
}
```

## GET `/api/v1/sso/metadata`

Get Sp Metadata

Return SP metadata for configuring the IdP.

Source: [backend/app/routers/sso.py:54](../../../backend/app/routers/sso.py#L54).

Dependency chain: .

Declared Python handler arguments (includes exact role/guard options):

```python

```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "get_sp_metadata_api_v1_sso_metadata_get",
  "responses": {
    "200": {
      "content": {
        "application/json": {
          "schema": {
            "additionalProperties": true,
            "title": "Response Get Sp Metadata Api V1 Sso Metadata Get",
            "type": "object"
          }
        }
      },
      "description": "Successful Response"
    }
  }
}
```

## GET `/api/v1/sso/status`

Get Sso Status

Public endpoint to check if SSO is enabled and available.

Source: [backend/app/routers/sso.py:234](../../../backend/app/routers/sso.py#L234).

Dependency chain: `get_db`.

Declared Python handler arguments (includes exact role/guard options):

```python
db: AsyncSession=Depends(get_db)
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "get_sso_status_api_v1_sso_status_get",
  "responses": {
    "200": {
      "content": {
        "application/json": {
          "schema": {
            "additionalProperties": true,
            "title": "Response Get Sso Status Api V1 Sso Status Get",
            "type": "object"
          }
        }
      },
      "description": "Successful Response"
    }
  }
}
```
