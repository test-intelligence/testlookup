# Sso Saml API

[Documentation home](../../README.md) · [Regeneration](../../maintainers/maintenance.md)

Generated from tracked source by `scripts/generate_reference.py`. Do not edit by hand. What the checks cover is recorded in [verification](../../maintainers/verification.md).

## POST `/api/v1/sso/acs`

Saml Acs

SAML Assertion Consumer Service — processes the SAML Response from the IdP.

This endpoint receives the base64-encoded SAMLResponse via form POST,
validates the assertion, and either logs in an existing user or JIT-provisions
a new one.

The POST comes from the browser. It answers with a 303 into the app
(``/sso/callback``) carrying a one-time code in the URL fragment, which the
app exchanges for the tokens (``POST /sso/exchange``); a refusal goes back
to ``/login``. A client that asks for JSON gets the tokens as before.

Source: [backend/app/routers/sso.py:373](../../../backend/app/routers/sso.py#L373).

Dependency chain: `get_db`.

Declared Python handler arguments (includes exact role/guard options):

```python
request: Request, db: AsyncSession=Depends(get_db)
```

Direct handler error branches (dependency/service errors can add others):

```python
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

Source: [backend/app/routers/sso.py:556](../../../backend/app/routers/sso.py#L556).

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

Source: [backend/app/routers/sso.py:585](../../../backend/app/routers/sso.py#L585).

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

Source: [backend/app/routers/sso.py:766](../../../backend/app/routers/sso.py#L766).

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

Source: [backend/app/routers/sso.py:569](../../../backend/app/routers/sso.py#L569).

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

Source: [backend/app/routers/sso.py:659](../../../backend/app/routers/sso.py#L659).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_role.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
config_id: uuid.UUID, payload: SSOConfigUpdate, request: Request, current_user: User=Depends(require_role(UserRole.ADMIN)), db: AsyncSession=Depends(get_db)
```

Direct handler error branches (dependency/service errors can add others):

```python
HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Invalid role '{role_value}' in role_mapping for group '{group_name}'")
HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f'Invalid IdP certificate: {cert_msg}')
HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f'{name} cannot be empty')
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

## POST `/api/v1/sso/configs/{config_id}/preview-role`

Preview Sso Role

W20 ADM-08a: the role a user in these IdP groups would get (ADMIN only; pure, writes nothing).

The posted ``role_mapping`` / ``default_role`` preview an unsaved edit; otherwise the stored ones answer.

Source: [backend/app/routers/sso.py:800](../../../backend/app/routers/sso.py#L800).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_role.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
config_id: uuid.UUID, payload: SSORolePreviewRequest, current_user: User=Depends(require_role(UserRole.ADMIN)), db: AsyncSession=Depends(get_db)
```

Direct handler error branches (dependency/service errors can add others):

```python
HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Invalid default role '{default}'")
HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Invalid role '{role_value}' in role_mapping for group '{group_name}'")
HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='SSO configuration not found')
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "preview_sso_role_api_v1_sso_configs__config_id__preview_role_post",
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
          "$ref": "#/components/schemas/SSORolePreviewRequest"
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
            "$ref": "#/components/schemas/SSORolePreviewResponse"
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

Source: [backend/app/routers/sso.py:829](../../../backend/app/routers/sso.py#L829).

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

## POST `/api/v1/sso/exchange`

Exchange Sso Code

The tokens for an SSO sign-in the ACS completed, for its one-time code.

Public, like the ACS: the code is the credential. It works once, within a
minute of the IdP's response.

Source: [backend/app/routers/sso.py:517](../../../backend/app/routers/sso.py#L517).

Dependency chain: .

Declared Python handler arguments (includes exact role/guard options):

```python
payload: SSOExchangeRequest
```

Direct handler error branches (dependency/service errors can add others):

```python
HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='This sign-in link has expired or was already used. Sign in again.')
HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='SSO is not enabled')
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "exchange_sso_code_api_v1_sso_exchange_post",
  "requestBody": {
    "content": {
      "application/json": {
        "schema": {
          "$ref": "#/components/schemas/SSOExchangeRequest"
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
            "$ref": "#/components/schemas/SSOLoginResponse"
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
  }
}
```

## GET `/api/v1/sso/login-url`

Get Sso Login Url

The IdP URL that starts an SP-initiated login: the IdP SSO URL carrying a
SAML AuthnRequest (HTTP-Redirect binding) and, when ``next`` is an
app-relative path, a RelayState the browser returns to after sign-in.

Source: [backend/app/routers/sso.py:176](../../../backend/app/routers/sso.py#L176).

Dependency chain: `get_db`.

Declared Python handler arguments (includes exact role/guard options):

```python
next: Optional[str]=None, db: AsyncSession=Depends(get_db)
```

Direct handler error branches (dependency/service errors can add others):

```python
HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='No active SSO configuration found')
HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='SSO is not enabled')
HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail='The identity provider could not be reached. Contact your administrator.')
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "get_sso_login_url_api_v1_sso_login_url_get",
  "parameters": [
    {
      "in": "query",
      "name": "next",
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
        "title": "Next"
      }
    }
  ],
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
  }
}
```

## GET `/api/v1/sso/metadata`

Get Sp Metadata

Return SP metadata for configuring the IdP.

W20 ADM-08c: the single-logout URL is advertised only while the active SAML
configuration has an IdP SLO URL (``/sso/slo`` answers 404 otherwise, and
advertising it then sent IdPs to that 404). IdP-initiated SLO only, so no
SP signing key is published.

Source: [backend/app/routers/sso.py:81](../../../backend/app/routers/sso.py#L81).

Dependency chain: `get_db`.

Declared Python handler arguments (includes exact role/guard options):

```python
db: AsyncSession=Depends(get_db)
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

## GET `/api/v1/sso/oidc/callback`

Oidc Callback

W20 ADM-08b: the IdP sends the browser back here with ``code`` + ``state``.

The state is burned first (single use), the code exchanged with the client secret and the PKCE verifier,
the ID token validated (signature / iss / aud / exp / nonce), then the user is JIT-provisioned or linked
and handed into the app through the one-time-code fragment. Every refusal goes back to ``/login`` with a
generic notice; the reason stays in the server log and the identity-event audit.

Source: [backend/app/routers/sso.py:241](../../../backend/app/routers/sso.py#L241).

Dependency chain: `get_db`.

Declared Python handler arguments (includes exact role/guard options):

```python
request: Request, code: Optional[str]=None, state: Optional[str]=None, error: Optional[str]=None, db: AsyncSession=Depends(get_db)
```

Direct handler error branches (dependency/service errors can add others):

```python
HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='SSO is not enabled')
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "oidc_callback_api_v1_sso_oidc_callback_get",
  "parameters": [
    {
      "in": "query",
      "name": "code",
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
        "title": "Code"
      }
    },
    {
      "in": "query",
      "name": "state",
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
        "title": "State"
      }
    },
    {
      "in": "query",
      "name": "error",
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
        "title": "Error"
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
  }
}
```

Handler return expressions (source excerpts, not an inferred wire schema):

```python
RedirectResponse(url=f'{settings.public_base_url}/login?sso_error=1', status_code=status.HTTP_303_SEE_OTHER)
RedirectResponse(url=f'{settings.public_base_url}/sso/callback#{fragment}', status_code=status.HTTP_303_SEE_OTHER)
await refuse('account is deactivated', sub=claims['sub'])
await refuse('missing authorization code')
await refuse('no active OIDC configuration', audit=False)
await refuse('no client secret is stored for this configuration')
await refuse('state unknown, already used or expired', audit=False)
await refuse('the active SSO configuration changed during the sign-in')
await refuse(f'identity provider returned error {error[:100]!r}')
await refuse(str(exc))
await refuse(str(exc), sub=claims['sub'])
```

Contract limit: at least one response has an unstructured schema. Read the linked handler/serializer for emitted fields; the empty schema is not a promise of an empty JSON object.

## GET `/api/v1/sso/oidc/login`

Oidc Login

W20 ADM-08b: start an OIDC sign-in — 302 to the IdP's authorization endpoint (state, nonce, PKCE S256).

Source: [backend/app/routers/sso.py:225](../../../backend/app/routers/sso.py#L225).

Dependency chain: `get_db`.

Declared Python handler arguments (includes exact role/guard options):

```python
next: Optional[str]=None, db: AsyncSession=Depends(get_db)
```

Direct handler error branches (dependency/service errors can add others):

```python
HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='No active OIDC configuration found')
HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='SSO is not enabled')
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "oidc_login_api_v1_sso_oidc_login_get",
  "parameters": [
    {
      "in": "query",
      "name": "next",
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
        "title": "Next"
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
  }
}
```

Handler return expressions (source excerpts, not an inferred wire schema):

```python
RedirectResponse(url=f'{settings.public_base_url}/login?sso_error=1', status_code=status.HTTP_302_FOUND)
RedirectResponse(url=url, status_code=status.HTTP_302_FOUND)
```

Contract limit: at least one response has an unstructured schema. Read the linked handler/serializer for emitted fields; the empty schema is not a promise of an empty JSON object.

## GET `/api/v1/sso/scim-groups`

List Scim Groups



Source: [backend/app/routers/scim_groups_admin.py:49](../../../backend/app/routers/scim_groups_admin.py#L49).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_role.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
current_user: User=Depends(require_role(UserRole.ADMIN)), db: AsyncSession=Depends(get_db)
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "list_scim_groups_api_v1_sso_scim_groups_get",
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
            "additionalProperties": true,
            "title": "Response List Scim Groups Api V1 Sso Scim Groups Get",
            "type": "object"
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

## PUT `/api/v1/sso/scim-groups/{group_id}/mappings`

Put Scim Group Mappings



Source: [backend/app/routers/scim_groups_admin.py:57](../../../backend/app/routers/scim_groups_admin.py#L57).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_role.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
group_id: uuid.UUID, payload: GroupMappingsIn, request: Request, current_user: User=Depends(require_role(UserRole.ADMIN)), db: AsyncSession=Depends(get_db)
```

Direct handler error branches (dependency/service errors can add others):

```python
HTTPException(status_code=400, detail='Unknown project in the mappings')
HTTPException(status_code=400, detail=str(exc))
HTTPException(status_code=404, detail='SCIM group not found')
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "put_scim_group_mappings_api_v1_sso_scim_groups__group_id__mappings_put",
  "parameters": [
    {
      "in": "path",
      "name": "group_id",
      "required": true,
      "schema": {
        "format": "uuid",
        "title": "Group Id",
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
          "$ref": "#/components/schemas/GroupMappingsIn"
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
            "additionalProperties": true,
            "title": "Response Put Scim Group Mappings Api V1 Sso Scim Groups  Group Id  Mappings Put",
            "type": "object"
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

## GET `/api/v1/sso/slo`

Saml Single Logout

W20 ADM-08c: IdP-initiated single logout (HTTP-Redirect or HTTP-POST binding).

A LogoutRequest signed by the configured IdP ends the NameID's TestLookup sessions — every refresh token
revoked and an access-token cutoff written, so the next API call goes to the sign-in page — and the browser
goes back to the IdP with a Success LogoutResponse. Anything unsigned, signed by another key, from another
issuer, stale or replayed is refused with 400 and changes nothing (no identity event: anyone can send one).

Source: [backend/app/routers/sso.py:106](../../../backend/app/routers/sso.py#L106).

Dependency chain: `get_db`.

Declared Python handler arguments (includes exact role/guard options):

```python
request: Request, db: AsyncSession=Depends(get_db)
```

Direct handler error branches (dependency/service errors can add others):

```python
HTTPException(status_code=503, detail='Token revocation store unavailable; try again')
HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='The logout request was refused.')
HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='SSO is not enabled')
HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Single logout is not configured')
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "saml_single_logout_api_v1_sso_slo_get",
  "responses": {
    "200": {
      "content": {
        "application/json": {
          "schema": {}
        }
      },
      "description": "Successful Response"
    }
  }
}
```

Handler return expressions (source excerpts, not an inferred wire schema):

```python
RedirectResponse(url=saml_slo.logout_response_url(idp_slo_url=config.idp_slo_url, in_response_to=claims['id'], sp_entity_id=config.sp_entity_id, relay_state=str(relay_state) if relay_state else None), status_code=status.HTTP_302_FOUND)
```

Contract limit: at least one response has an unstructured schema. Read the linked handler/serializer for emitted fields; the empty schema is not a promise of an empty JSON object.

## POST `/api/v1/sso/slo`

Saml Single Logout

W20 ADM-08c: IdP-initiated single logout (HTTP-Redirect or HTTP-POST binding).

A LogoutRequest signed by the configured IdP ends the NameID's TestLookup sessions — every refresh token
revoked and an access-token cutoff written, so the next API call goes to the sign-in page — and the browser
goes back to the IdP with a Success LogoutResponse. Anything unsigned, signed by another key, from another
issuer, stale or replayed is refused with 400 and changes nothing (no identity event: anyone can send one).

Source: [backend/app/routers/sso.py:106](../../../backend/app/routers/sso.py#L106).

Dependency chain: `get_db`.

Declared Python handler arguments (includes exact role/guard options):

```python
request: Request, db: AsyncSession=Depends(get_db)
```

Direct handler error branches (dependency/service errors can add others):

```python
HTTPException(status_code=503, detail='Token revocation store unavailable; try again')
HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='The logout request was refused.')
HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='SSO is not enabled')
HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Single logout is not configured')
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "saml_single_logout_api_v1_sso_slo_get",
  "responses": {
    "200": {
      "content": {
        "application/json": {
          "schema": {}
        }
      },
      "description": "Successful Response"
    }
  }
}
```

Handler return expressions (source excerpts, not an inferred wire schema):

```python
RedirectResponse(url=saml_slo.logout_response_url(idp_slo_url=config.idp_slo_url, in_response_to=claims['id'], sp_entity_id=config.sp_entity_id, relay_state=str(relay_state) if relay_state else None), status_code=status.HTTP_302_FOUND)
```

Contract limit: at least one response has an unstructured schema. Read the linked handler/serializer for emitted fields; the empty schema is not a promise of an empty JSON object.

## GET `/api/v1/sso/status`

Get Sso Status

Public endpoint to check if SSO is enabled and available.

Source: [backend/app/routers/sso.py:535](../../../backend/app/routers/sso.py#L535).

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
