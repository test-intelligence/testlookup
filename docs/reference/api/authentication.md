# Authentication API

[Documentation home](../../README.md) · [Regeneration](../../handoff/maintenance.md)

Generated from tracked source by `scripts/generate_handoff_reference.py`. Do not edit by hand. The baseline and validation limits are recorded in [verification](../../handoff/verification.md).

## POST `/api/v1/auth/change-password`

Change Password

Change the current user's password.

Source: [backend/app/routers/auth.py:641](../../../backend/app/routers/auth.py#L641).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`.

Declared Python handler arguments (includes exact role/guard options):

```python
payload: ChangePasswordRequest, current_user: User=Depends(get_current_active_user), db: AsyncSession=Depends(get_db)
```

Direct handler error branches (dependency/service errors can add others):

```python
HTTPException(status_code=503, detail='Token revocation store unavailable; password change was not applied')
HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='Current password is incorrect')
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "change_password_api_v1_auth_change_password_post",
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
          "$ref": "#/components/schemas/ChangePasswordRequest"
        }
      }
    },
    "required": true
  },
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

## POST `/api/v1/auth/dev-login`

Dev Login

Development-only: issue a JWT for a seeded user without credentials.

Enabled only when APP_ENV=development AND DEV_AUTO_LOGIN_ENABLED=true.
Returns 404 in all other environments so it is invisible in staging/prod.

Query param:
  role — one of: admin, qa_lead, qa_engineer, tester, viewer (default: admin)
  username — optional active username to authenticate as directly

If the requested user does not exist yet (seed not run), a temporary account
is created on the fly so developers can always access the UI after `make dev`.

Source: [backend/app/routers/auth.py:351](../../../backend/app/routers/auth.py#L351).

Dependency chain: `get_db`.

Declared Python handler arguments (includes exact role/guard options):

```python
role: str='admin', username: str | None=None, db: AsyncSession=Depends(get_db)
```

Direct handler error branches (dependency/service errors can add others):

```python
HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Unknown role '{role}'. Valid values: {', '.join(_DEV_ROLE_MAP)}")
HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail='dev-login cannot be used for an account with MFA enabled. Sign in with the password + authenticator flow.')
HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Not found')
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "dev_login_api_v1_auth_dev_login_post",
  "parameters": [
    {
      "in": "query",
      "name": "role",
      "required": false,
      "schema": {
        "default": "admin",
        "title": "Role",
        "type": "string"
      }
    },
    {
      "in": "query",
      "name": "username",
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
        "title": "Username"
      }
    }
  ],
  "responses": {
    "200": {
      "content": {
        "application/json": {
          "schema": {
            "$ref": "#/components/schemas/TokenResponse"
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

## POST `/api/v1/auth/first-time-reset`

First Time Reset

Forced password reset for self-registered users on first login.

- Requires a valid JWT (user must be authenticated).
- Only permitted when must_change_password=True on the account.
- Does NOT require the current/registration password.
- Clears the must_change_password flag after a successful change.

Source: [backend/app/routers/auth.py:415](../../../backend/app/routers/auth.py#L415).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`.

Declared Python handler arguments (includes exact role/guard options):

```python
payload: FirstTimeResetRequest, current_user: User=Depends(get_current_active_user), db: AsyncSession=Depends(get_db)
```

Direct handler error branches (dependency/service errors can add others):

```python
HTTPException(status_code=503, detail='Token revocation store unavailable; password reset was not applied')
HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='Passwords do not match')
HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail='Password reset not required for this account. Use change-password instead.')
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "first_time_reset_api_v1_auth_first_time_reset_post",
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
          "$ref": "#/components/schemas/FirstTimeResetRequest"
        }
      }
    },
    "required": true
  },
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

## POST `/api/v1/auth/login`

Login

Authenticate and return JWT access + refresh tokens.

Three success shapes, all HTTP 200 (see ``models/schemas.py``):

* :class:`TokenResponse` — fully authenticated.
* :class:`MfaChallengeResponse` — password accepted, second factor owed.
* :class:`MfaEnrollmentRequiredResponse` — password accepted, workspace
  policy requires MFA and this account has none yet.

The last two carry interstitial tokens with their own ``type`` claim. They
are **not** access tokens and are rejected by ``get_current_user``: see
``core/security.create_mfa_token`` for why a claim on a real access token
would have been a complete bypass.

Source: [backend/app/routers/auth.py:120](../../../backend/app/routers/auth.py#L120).

Dependency chain: `OAuth2PasswordRequestForm`, `get_db`.

Declared Python handler arguments (includes exact role/guard options):

```python
request: Request, form_data: OAuth2PasswordRequestForm=Depends(), db: AsyncSession=Depends(get_db)
```

Direct handler error branches (dependency/service errors can add others):

```python
HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='Incorrect username or password', headers={'WWW-Authenticate': 'Bearer'})
HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail='Account disabled')
HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail='SSO is required for this account. Please use the SSO login option.')
HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=mfa_service.SEED_UNREADABLE_DETAIL)
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "login_api_v1_auth_login_post",
  "requestBody": {
    "content": {
      "application/x-www-form-urlencoded": {
        "schema": {
          "$ref": "#/components/schemas/Body_login_api_v1_auth_login_post"
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
            "anyOf": [
              {
                "$ref": "#/components/schemas/TokenResponse"
              },
              {
                "$ref": "#/components/schemas/MfaChallengeResponse"
              },
              {
                "$ref": "#/components/schemas/MfaEnrollmentRequiredResponse"
              }
            ],
            "title": "Response Login Api V1 Auth Login Post"
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

## POST `/api/v1/auth/logout`

Logout

Server-side logout — revokes the caller's access-token jti until its
natural expiry and revokes all live refresh tokens for the user so the
session cannot be re-minted via /auth/refresh.

Source: [backend/app/routers/auth.py:591](../../../backend/app/routers/auth.py#L591).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`.

Declared Python handler arguments (includes exact role/guard options):

```python
current_user: User=Depends(get_current_active_user), token: str=Depends(oauth2_scheme), db: AsyncSession=Depends(get_db)
```

Direct handler error branches (dependency/service errors can add others):

```python
HTTPException(status_code=503, detail='Token revocation store unavailable; logout was not applied')
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "logout_api_v1_auth_logout_post",
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
    },
    {
      "JWT": []
    }
  ]
}
```

## GET `/api/v1/auth/me`

Get Me

Return the authenticated user's profile.

Source: [backend/app/routers/auth.py:536](../../../backend/app/routers/auth.py#L536).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`.

Declared Python handler arguments (includes exact role/guard options):

```python
current_user: User=Depends(get_current_active_user)
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "get_me_api_v1_auth_me_get",
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
            "$ref": "#/components/schemas/UserResponse"
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

## PATCH `/api/v1/auth/me`

Update Me

Self-service profile update — any authenticated user can update their own
full_name and avatar_color.  Email and username are read-only here.

Source: [backend/app/routers/auth.py:573](../../../backend/app/routers/auth.py#L573).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`.

Declared Python handler arguments (includes exact role/guard options):

```python
payload: SelfUpdateProfileRequest, current_user: User=Depends(get_current_active_user), db: AsyncSession=Depends(get_db)
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "update_me_api_v1_auth_me_patch",
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
          "$ref": "#/components/schemas/SelfUpdateProfileRequest"
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
            "$ref": "#/components/schemas/UserResponse"
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

## GET `/api/v1/auth/me/dismissals`

List My Dismissals

UI prompts this user has dismissed. Read-only; no project scope — a
dismissal is a property of the person, not of a project.

Source: [backend/app/routers/auth.py:542](../../../backend/app/routers/auth.py#L542).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`.

Declared Python handler arguments (includes exact role/guard options):

```python
current_user: User=Depends(get_current_active_user), db: AsyncSession=Depends(get_db)
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "list_my_dismissals_api_v1_auth_me_dismissals_get",
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
            "$ref": "#/components/schemas/UIDismissalListResponse"
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

## POST `/api/v1/auth/me/dismissals`

Dismiss Prompt

Dismiss a UI prompt for this user. Idempotent — dismissing twice is a
no-op and still returns 201 with the full list.

Source: [backend/app/routers/auth.py:553](../../../backend/app/routers/auth.py#L553).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`.

Declared Python handler arguments (includes exact role/guard options):

```python
payload: UIDismissalCreate, current_user: User=Depends(get_current_active_user), db: AsyncSession=Depends(get_db)
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "dismiss_prompt_api_v1_auth_me_dismissals_post",
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
          "$ref": "#/components/schemas/UIDismissalCreate"
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
            "$ref": "#/components/schemas/UIDismissalListResponse"
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

## POST `/api/v1/auth/mfa/disable`

Disable Mfa

Turn MFA off. Requires the password **and** a live second factor.

Source: [backend/app/routers/mfa.py:462](../../../backend/app/routers/mfa.py#L462).

Dependency chain: `OAuth2PasswordBearer`, `_require_interactive_user`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`.

Declared Python handler arguments (includes exact role/guard options):

```python
payload: MfaDisableRequest, request: Request, current_user: User=Depends(_require_interactive_user), db: AsyncSession=Depends(get_db)
```

Direct handler error branches (dependency/service errors can add others):

```python
HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='A valid authenticator code or recovery code is required to disable MFA.')
HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='Password is incorrect')
HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail='Workspace policy requires MFA for your role, so it cannot be disabled. Ask an administrator to change the policy or reset your device.')
HTTPException(status_code=status.HTTP_409_CONFLICT, detail='MFA is not enabled for this account.')
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "disable_mfa_api_v1_auth_mfa_disable_post",
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
          "$ref": "#/components/schemas/MfaDisableRequest"
        }
      }
    },
    "required": true
  },
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

## POST `/api/v1/auth/mfa/enroll/confirm`

Confirm Enrollment

Verify a code against the staged seed, then enable MFA.

Recovery codes are minted in the same unit of work as the flag flip — an
account must never reach ``mfa_enabled=True`` without a way back in. They
are returned once and only digests are kept.

Source: [backend/app/routers/mfa.py:306](../../../backend/app/routers/mfa.py#L306).

Dependency chain: `_optional_session_user`, `get_db`.

Declared Python handler arguments (includes exact role/guard options):

```python
payload: MfaEnrollConfirmRequest, request: Request, session_user: Optional[User]=Depends(_optional_session_user), db: AsyncSession=Depends(get_db)
```

Direct handler error branches (dependency/service errors can add others):

```python
HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='That code did not match. Check your authenticator and try again.')
HTTPException(status_code=status.HTTP_409_CONFLICT, detail='MFA is already enabled for this account.')
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "confirm_enrollment_api_v1_auth_mfa_enroll_confirm_post",
  "requestBody": {
    "content": {
      "application/json": {
        "schema": {
          "$ref": "#/components/schemas/MfaEnrollConfirmRequest"
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
            "$ref": "#/components/schemas/MfaEnrollConfirmResponse"
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

## POST `/api/v1/auth/mfa/enroll/start`

Start Enrollment

Generate a TOTP seed and provisioning URI. Does **not** enable MFA.

The seed is stored immediately so ``/enroll/confirm`` has something to
verify against, but ``users.mfa_enabled`` stays false until a code proves
the authenticator actually holds it. An abandoned enrollment therefore
changes nothing about how the account authenticates.

Source: [backend/app/routers/mfa.py:266](../../../backend/app/routers/mfa.py#L266).

Dependency chain: `_optional_session_user`, `get_db`.

Declared Python handler arguments (includes exact role/guard options):

```python
payload: MfaEnrollStartRequest, request: Request, session_user: Optional[User]=Depends(_optional_session_user), db: AsyncSession=Depends(get_db)
```

Direct handler error branches (dependency/service errors can add others):

```python
HTTPException(status_code=status.HTTP_409_CONFLICT, detail='MFA is already enabled. Disable it first to enroll a new device.')
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "start_enrollment_api_v1_auth_mfa_enroll_start_post",
  "requestBody": {
    "content": {
      "application/json": {
        "schema": {
          "$ref": "#/components/schemas/MfaEnrollStartRequest"
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
            "$ref": "#/components/schemas/MfaEnrollStartResponse"
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

## POST `/api/v1/auth/mfa/recovery-codes`

Reissue Recovery Codes

Replace the recovery-code set. The previous codes stop working.

Source: [backend/app/routers/mfa.py:517](../../../backend/app/routers/mfa.py#L517).

Dependency chain: `OAuth2PasswordBearer`, `_require_interactive_user`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`.

Declared Python handler arguments (includes exact role/guard options):

```python
payload: MfaRecoveryCodesRequest, request: Request, current_user: User=Depends(_require_interactive_user), db: AsyncSession=Depends(get_db)
```

Direct handler error branches (dependency/service errors can add others):

```python
HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='A valid authenticator code or recovery code is required.')
HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='Password is incorrect')
HTTPException(status_code=status.HTTP_409_CONFLICT, detail='MFA is not enabled for this account.')
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "reissue_recovery_codes_api_v1_auth_mfa_recovery_codes_post",
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
          "$ref": "#/components/schemas/MfaRecoveryCodesRequest"
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
            "$ref": "#/components/schemas/MfaRecoveryCodesResponse"
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

## GET `/api/v1/auth/mfa/status`

Mfa Status

Current MFA state for the caller.

Readable with an API key (unlike the mutating endpoints) so a CI job can
report on its owner's posture without being able to change it.

Source: [backend/app/routers/mfa.py:555](../../../backend/app/routers/mfa.py#L555).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`.

Declared Python handler arguments (includes exact role/guard options):

```python
current_user: User=Depends(get_current_active_user), db: AsyncSession=Depends(get_db)
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "mfa_status_api_v1_auth_mfa_status_get",
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
            "$ref": "#/components/schemas/MfaStatusResponse"
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

## POST `/api/v1/auth/mfa/verify`

Verify Mfa

Exchange a challenge token plus a second factor for a real session.

Source: [backend/app/routers/mfa.py:357](../../../backend/app/routers/mfa.py#L357).

Dependency chain: `get_db`.

Declared Python handler arguments (includes exact role/guard options):

```python
payload: MfaVerifyRequest, request: Request, db: AsyncSession=Depends(get_db)
```

Direct handler error branches (dependency/service errors can add others):

```python
HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='Provide either code or recovery_code.')
HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='Invalid verification code.', headers={'WWW-Authenticate': 'Bearer'})
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "verify_mfa_api_v1_auth_mfa_verify_post",
  "requestBody": {
    "content": {
      "application/json": {
        "schema": {
          "$ref": "#/components/schemas/MfaVerifyRequest"
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
            "$ref": "#/components/schemas/TokenResponse"
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

## POST `/api/v1/auth/refresh`

Refresh Tokens

Exchange a valid refresh token for a new access + refresh token pair (rotation).
The old refresh token is not reusable after this call.

Source: [backend/app/routers/auth.py:457](../../../backend/app/routers/auth.py#L457).

Dependency chain: `get_db`.

Declared Python handler arguments (includes exact role/guard options):

```python
payload: RefreshRequest, db: AsyncSession=Depends(get_db)
```

Direct handler error branches (dependency/service errors can add others):

```python
HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='Invalid or expired refresh token', headers={'WWW-Authenticate': 'Bearer'})
HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='Multi-factor authentication is now required for your role. Sign in again to enroll an authenticator.', headers={'WWW-Authenticate': 'Bearer'})
HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=mfa_service.SEED_UNREADABLE_DETAIL, headers={'X-Refresh-Retry-Safe': '1'})
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "refresh_tokens_api_v1_auth_refresh_post",
  "requestBody": {
    "content": {
      "application/json": {
        "schema": {
          "$ref": "#/components/schemas/RefreshRequest"
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
            "$ref": "#/components/schemas/TokenResponse"
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

## POST `/api/v1/auth/register`

Register

Self-service registration.

New accounts are created with the QA_ENGINEER role and
must_change_password=True so the user is prompted to set a permanent
password on their first login.

Source: [backend/app/routers/auth.py:62](../../../backend/app/routers/auth.py#L62).

Dependency chain: `get_db`.

Declared Python handler arguments (includes exact role/guard options):

```python
payload: UserCreate, db: AsyncSession=Depends(get_db)
```

Direct handler error branches (dependency/service errors can add others):

```python
HTTPException(status_code=status.HTTP_409_CONFLICT, detail='Email or username already registered')
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "register_api_v1_auth_register_post",
  "requestBody": {
    "content": {
      "application/json": {
        "schema": {
          "$ref": "#/components/schemas/UserCreate"
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
            "$ref": "#/components/schemas/UserResponse"
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

## GET `/api/v1/auth/users`

List Users

Return active users for assignee dropdowns (bounded).

Source: [backend/app/routers/auth.py:625](../../../backend/app/routers/auth.py#L625).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`.

Declared Python handler arguments (includes exact role/guard options):

```python
limit: int=Query(100, ge=1, le=500, description='Max users to return'), db: AsyncSession=Depends(get_db), current_user: User=Depends(get_current_active_user)
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "list_users_api_v1_auth_users_get",
  "parameters": [
    {
      "description": "Max users to return",
      "in": "query",
      "name": "limit",
      "required": false,
      "schema": {
        "default": 100,
        "description": "Max users to return",
        "maximum": 500,
        "minimum": 1,
        "title": "Limit",
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
            "items": {
              "$ref": "#/components/schemas/UserResponse"
            },
            "title": "Response List Users Api V1 Auth Users Get",
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
