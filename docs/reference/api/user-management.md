# User Management API

[Documentation home](../../README.md) · [Regeneration](../../maintainers/maintenance.md)

Generated from tracked source by `scripts/generate_reference.py`. Do not edit by hand. What the checks cover is recorded in [verification](../../maintainers/verification.md).

## GET `/api/v1/projects/{project_id}/members`

List Project Members

List all members of a project.

Source: [backend/app/routers/users.py:657](../../../backend/app/routers/users.py#L657).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_project_access.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
project_id: uuid.UUID, db: AsyncSession=Depends(get_db), current_user: User=Depends(get_current_active_user), _: User=Depends(require_project_access())
```

Direct handler error branches (dependency/service errors can add others):

```python
HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Project not found')
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "list_project_members_api_v1_projects__project_id__members_get",
  "parameters": [
    {
      "in": "path",
      "name": "project_id",
      "required": true,
      "schema": {
        "format": "uuid",
        "title": "Project Id",
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
              "$ref": "#/components/schemas/ProjectMemberResponse"
            },
            "title": "Response List Project Members Api V1 Projects  Project Id  Members Get",
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

## POST `/api/v1/projects/{project_id}/members`

Add Project Member

Add a user to a project with a role. Requires QA_LEAD or higher.

Source: [backend/app/routers/users.py:684](../../../backend/app/routers/users.py#L684).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_project_access.<locals>._check`, `require_role.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
project_id: uuid.UUID, payload: AddProjectMemberRequest, db: AsyncSession=Depends(get_db), current_user: User=Depends(require_role(UserRole.QA_LEAD, allow_project_key=True)), _: User=Depends(require_project_access())
```

Direct handler error branches (dependency/service errors can add others):

```python
HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Project not found')
HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='User not found')
HTTPException(status_code=status.HTTP_409_CONFLICT, detail='User is already a member of this project')
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "add_project_member_api_v1_projects__project_id__members_post",
  "parameters": [
    {
      "in": "path",
      "name": "project_id",
      "required": true,
      "schema": {
        "format": "uuid",
        "title": "Project Id",
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
          "$ref": "#/components/schemas/AddProjectMemberRequest"
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
            "$ref": "#/components/schemas/ProjectMemberResponse"
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

## DELETE `/api/v1/projects/{project_id}/members/{user_id}`

Remove Project Member

Remove a user from a project. Requires ADMIN.

Source: [backend/app/routers/users.py:785](../../../backend/app/routers/users.py#L785).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_project_access.<locals>._check`, `require_role.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
project_id: uuid.UUID, user_id: uuid.UUID, db: AsyncSession=Depends(get_db), current_user: User=Depends(require_role(UserRole.ADMIN, allow_project_key=True)), _: User=Depends(require_project_access())
```

Direct handler error branches (dependency/service errors can add others):

```python
HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Project membership not found')
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "remove_project_member_api_v1_projects__project_id__members__user_id__delete",
  "parameters": [
    {
      "in": "path",
      "name": "project_id",
      "required": true,
      "schema": {
        "format": "uuid",
        "title": "Project Id",
        "type": "string"
      }
    },
    {
      "in": "path",
      "name": "user_id",
      "required": true,
      "schema": {
        "format": "uuid",
        "title": "User Id",
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

## PATCH `/api/v1/projects/{project_id}/members/{user_id}`

Update Project Member Role

Update a project member's role. Requires QA_LEAD or higher.

Source: [backend/app/routers/users.py:737](../../../backend/app/routers/users.py#L737).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_project_access.<locals>._check`, `require_role.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
project_id: uuid.UUID, user_id: uuid.UUID, payload: UpdateProjectMemberRoleRequest, db: AsyncSession=Depends(get_db), current_user: User=Depends(require_role(UserRole.QA_LEAD, allow_project_key=True)), _: User=Depends(require_project_access())
```

Direct handler error branches (dependency/service errors can add others):

```python
HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Project membership not found')
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "update_project_member_role_api_v1_projects__project_id__members__user_id__patch",
  "parameters": [
    {
      "in": "path",
      "name": "project_id",
      "required": true,
      "schema": {
        "format": "uuid",
        "title": "Project Id",
        "type": "string"
      }
    },
    {
      "in": "path",
      "name": "user_id",
      "required": true,
      "schema": {
        "format": "uuid",
        "title": "User Id",
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
          "$ref": "#/components/schemas/UpdateProjectMemberRoleRequest"
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
            "$ref": "#/components/schemas/ProjectMemberResponse"
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

## GET `/api/v1/users`

List Users

List all users (paginated). Requires QA_LEAD or higher.

The body stays a bare list (other callers read it); the number of users
matching the filters, across every page, is the ``X-Total-Count`` header.

Source: [backend/app/routers/users.py:152](../../../backend/app/routers/users.py#L152).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_role.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
response: Response, is_active: Optional[bool]=Query(None), role: Optional[UserRole]=Query(None), q: Optional[str]=Query(None, max_length=100, description='Case-insensitive substring of the username, email or full name (ADM-03).'), page: int=Query(1, ge=1, description='Page number'), page_size: int=Query(50, ge=1, le=200, description='Items per page'), db: AsyncSession=Depends(get_db), current_user: User=Depends(require_role(UserRole.QA_LEAD))
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "list_users_api_v1_users_get",
  "parameters": [
    {
      "in": "query",
      "name": "is_active",
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
        "title": "Is Active"
      }
    },
    {
      "in": "query",
      "name": "role",
      "required": false,
      "schema": {
        "anyOf": [
          {
            "$ref": "#/components/schemas/UserRole"
          },
          {
            "type": "null"
          }
        ],
        "title": "Role"
      }
    },
    {
      "description": "Case-insensitive substring of the username, email or full name (ADM-03).",
      "in": "query",
      "name": "q",
      "required": false,
      "schema": {
        "anyOf": [
          {
            "maxLength": 100,
            "type": "string"
          },
          {
            "type": "null"
          }
        ],
        "description": "Case-insensitive substring of the username, email or full name (ADM-03).",
        "title": "Q"
      }
    },
    {
      "description": "Page number",
      "in": "query",
      "name": "page",
      "required": false,
      "schema": {
        "default": 1,
        "description": "Page number",
        "minimum": 1,
        "title": "Page",
        "type": "integer"
      }
    },
    {
      "description": "Items per page",
      "in": "query",
      "name": "page_size",
      "required": false,
      "schema": {
        "default": 50,
        "description": "Items per page",
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
            "items": {
              "$ref": "#/components/schemas/UserListResponse"
            },
            "title": "Response List Users Api V1 Users Get",
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

## POST `/api/v1/users`

Admin Create User

Admin creates a user directly with a temporary password. Requires ADMIN.

Source: [backend/app/routers/users.py:327](../../../backend/app/routers/users.py#L327).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_role.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
payload: AdminCreateUserRequest, db: AsyncSession=Depends(get_db), current_user: User=Depends(require_role(UserRole.ADMIN))
```

Direct handler error branches (dependency/service errors can add others):

```python
HTTPException(status_code=status.HTTP_409_CONFLICT, detail='Email or username already registered')
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "admin_create_user_api_v1_users_post",
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
          "$ref": "#/components/schemas/AdminCreateUserRequest"
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
            "$ref": "#/components/schemas/AdminCreateUserResponse"
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

## GET `/api/v1/users/invitations`

List Invitations

W17b ADM-01: pending (and expired) invitations, newest first. Never a token. ADMIN.

Declared before ``/{user_id}`` so ``invitations`` is not parsed as a user id.

Source: [backend/app/routers/users.py:194](../../../backend/app/routers/users.py#L194).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_role.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
db: AsyncSession=Depends(get_db), current_user: User=Depends(require_role(UserRole.ADMIN)), include_closed: bool=Query(False, description='Also list used and revoked invitations')
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "list_invitations_api_v1_users_invitations_get",
  "parameters": [
    {
      "description": "Also list used and revoked invitations",
      "in": "query",
      "name": "include_closed",
      "required": false,
      "schema": {
        "default": false,
        "description": "Also list used and revoked invitations",
        "title": "Include Closed",
        "type": "boolean"
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
              "$ref": "#/components/schemas/InvitationSummary"
            },
            "title": "Response List Invitations Api V1 Users Invitations Get",
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

## POST `/api/v1/users/invitations/{invitation_id}/resend`

Resend Invitation

Issue a new link (new token, new expiry); the previous link stops working. ADMIN.

Also the only way to get a copyable link again: the raw token is never
stored, so an old link cannot be shown twice.

Source: [backend/app/routers/users.py:537](../../../backend/app/routers/users.py#L537).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_role.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
invitation_id: uuid.UUID, db: AsyncSession=Depends(get_db), current_user: User=Depends(require_role(UserRole.ADMIN))
```

Direct handler error branches (dependency/service errors can add others):

```python
HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"This invitation was {('accepted' if state == 'used' else 'revoked')}; send a new invitation instead")
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "resend_invitation_api_v1_users_invitations__invitation_id__resend_post",
  "parameters": [
    {
      "in": "path",
      "name": "invitation_id",
      "required": true,
      "schema": {
        "format": "uuid",
        "title": "Invitation Id",
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
            "$ref": "#/components/schemas/InviteUserResponse"
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

## POST `/api/v1/users/invitations/{invitation_id}/revoke`

Revoke Invitation

Kill an invitation link. A used invitation cannot be revoked (the account exists). ADMIN.

Source: [backend/app/routers/users.py:519](../../../backend/app/routers/users.py#L519).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_role.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
invitation_id: uuid.UUID, db: AsyncSession=Depends(get_db), current_user: User=Depends(require_role(UserRole.ADMIN))
```

Direct handler error branches (dependency/service errors can add others):

```python
HTTPException(status_code=status.HTTP_409_CONFLICT, detail='This invitation was already accepted')
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "revoke_invitation_api_v1_users_invitations__invitation_id__revoke_post",
  "parameters": [
    {
      "in": "path",
      "name": "invitation_id",
      "required": true,
      "schema": {
        "format": "uuid",
        "title": "Invitation Id",
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
            "$ref": "#/components/schemas/InvitationSummary"
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

## POST `/api/v1/users/invite`

Invite User

Invite a user by email with a role and optional project memberships. Requires ADMIN.

W17b ADM-01: only ``sha256(token)`` is stored; the link carries the token in
a URL fragment; the link is emailed when SMTP is configured and always
returned so the admin can copy it (the offline path).

Source: [backend/app/routers/users.py:371](../../../backend/app/routers/users.py#L371).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_role.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
payload: InviteUserRequest, db: AsyncSession=Depends(get_db), current_user: User=Depends(require_role(UserRole.ADMIN))
```

Direct handler error branches (dependency/service errors can add others):

```python
HTTPException(status_code=status.HTTP_409_CONFLICT, detail='An active invitation already exists for this email. Resend or revoke it from Pending invitations.')
HTTPException(status_code=status.HTTP_409_CONFLICT, detail='Email already registered')
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "invite_user_api_v1_users_invite_post",
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
          "$ref": "#/components/schemas/InviteUserRequest"
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
            "$ref": "#/components/schemas/InviteUserResponse"
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

## GET `/api/v1/users/{user_id}`

Get User

Get a specific user by ID.

Source: [backend/app/routers/users.py:211](../../../backend/app/routers/users.py#L211).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_role.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
user_id: uuid.UUID, db: AsyncSession=Depends(get_db), current_user: User=Depends(require_role(UserRole.QA_LEAD))
```

Direct handler error branches (dependency/service errors can add others):

```python
HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='User not found')
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "get_user_api_v1_users__user_id__get",
  "parameters": [
    {
      "in": "path",
      "name": "user_id",
      "required": true,
      "schema": {
        "format": "uuid",
        "title": "User Id",
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
            "$ref": "#/components/schemas/UserListResponse"
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

## PATCH `/api/v1/users/{user_id}`

Update User Profile

Update a user's profile attributes. Requires ADMIN. Only non-None fields are applied.

Source: [backend/app/routers/users.py:277](../../../backend/app/routers/users.py#L277).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_role.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
user_id: uuid.UUID, payload: UpdateUserProfileRequest, db: AsyncSession=Depends(get_db), current_user: User=Depends(require_role(UserRole.ADMIN))
```

Direct handler error branches (dependency/service errors can add others):

```python
HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='Cannot change your own role')
HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='Cannot deactivate your own account')
HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='User not found')
HTTPException(status_code=status.HTTP_409_CONFLICT, detail='Email already in use')
HTTPException(status_code=status.HTTP_409_CONFLICT, detail='Username already in use')
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "update_user_profile_api_v1_users__user_id__patch",
  "parameters": [
    {
      "in": "path",
      "name": "user_id",
      "required": true,
      "schema": {
        "format": "uuid",
        "title": "User Id",
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
          "$ref": "#/components/schemas/UpdateUserProfileRequest"
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
            "$ref": "#/components/schemas/UserListResponse"
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

## GET `/api/v1/users/{user_id}/access-audit`

Get User Access Audit

Return access audit trail for a specific user.

Source: [backend/app/routers/users.py:643](../../../backend/app/routers/users.py#L643).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_role.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
user_id: uuid.UUID, limit: int=Query(50, ge=1, le=200), db: AsyncSession=Depends(get_db), _: User=Depends(require_role(UserRole.QA_LEAD))
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "get_user_access_audit_api_v1_users__user_id__access_audit_get",
  "parameters": [
    {
      "in": "path",
      "name": "user_id",
      "required": true,
      "schema": {
        "format": "uuid",
        "title": "User Id",
        "type": "string"
      }
    },
    {
      "in": "query",
      "name": "limit",
      "required": false,
      "schema": {
        "default": 50,
        "maximum": 200,
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
await get_access_audit_log(db, target_user_id=user_id, limit=limit)
```

Contract limit: at least one response has an unstructured schema. Read the linked handler/serializer for emitted fields; the empty schema is not a promise of an empty JSON object.

## GET `/api/v1/users/{user_id}/memberships`

Get User Memberships

Return all project memberships for a user in a single query.
Replaces the N+1 pattern of fetching each project's members individually.

Source: [backend/app/routers/users.py:615](../../../backend/app/routers/users.py#L615).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_role.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
user_id: uuid.UUID, db: AsyncSession=Depends(get_db), current_user: User=Depends(require_role(UserRole.QA_LEAD))
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "get_user_memberships_api_v1_users__user_id__memberships_get",
  "parameters": [
    {
      "in": "path",
      "name": "user_id",
      "required": true,
      "schema": {
        "format": "uuid",
        "title": "User Id",
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
[{'project_id': str(pm.project_id), 'project_name': p.name, 'role': _normalize_user_role(pm.role).value, 'created_at': pm.created_at.isoformat() if pm.created_at else None} for pm, p in rows]
```

Contract limit: at least one response has an unstructured schema. Read the linked handler/serializer for emitted fields; the empty schema is not a promise of an empty JSON object.

## POST `/api/v1/users/{user_id}/password-reset-link`

Issue Password Reset Link

W17b ADM-02: a single-use reset link for another user. ADMIN.

The administrator never sees or sets the password. Older unused links for
the user stop working. Refused for the caller's own account (change-password
exists for that). Audited without the token.

Source: [backend/app/routers/users.py:573](../../../backend/app/routers/users.py#L573).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_role.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
user_id: uuid.UUID, db: AsyncSession=Depends(get_db), current_user: User=Depends(require_role(UserRole.ADMIN))
```

Direct handler error branches (dependency/service errors can add others):

```python
HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='Use Change password for your own account')
HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='User not found')
HTTPException(status_code=status.HTTP_409_CONFLICT, detail='The account is deactivated; activate it first')
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "issue_password_reset_link_api_v1_users__user_id__password_reset_link_post",
  "parameters": [
    {
      "in": "path",
      "name": "user_id",
      "required": true,
      "schema": {
        "format": "uuid",
        "title": "User Id",
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
    "201": {
      "content": {
        "application/json": {
          "schema": {
            "$ref": "#/components/schemas/PasswordResetLinkResponse"
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

## PATCH `/api/v1/users/{user_id}/role`

Update User Role

Update a user's global role. Requires ADMIN.

Source: [backend/app/routers/users.py:225](../../../backend/app/routers/users.py#L225).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_role.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
user_id: uuid.UUID, payload: UpdateUserRoleRequest, db: AsyncSession=Depends(get_db), current_user: User=Depends(require_role(UserRole.ADMIN))
```

Direct handler error branches (dependency/service errors can add others):

```python
HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='Cannot change your own role')
HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='User not found')
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "update_user_role_api_v1_users__user_id__role_patch",
  "parameters": [
    {
      "in": "path",
      "name": "user_id",
      "required": true,
      "schema": {
        "format": "uuid",
        "title": "User Id",
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
          "$ref": "#/components/schemas/UpdateUserRoleRequest"
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
            "$ref": "#/components/schemas/UserListResponse"
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

## PATCH `/api/v1/users/{user_id}/status`

Update User Status

Activate or deactivate a user account. Requires ADMIN.

Source: [backend/app/routers/users.py:251](../../../backend/app/routers/users.py#L251).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_role.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
user_id: uuid.UUID, payload: UpdateUserStatusRequest, db: AsyncSession=Depends(get_db), current_user: User=Depends(require_role(UserRole.ADMIN))
```

Direct handler error branches (dependency/service errors can add others):

```python
HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='Cannot deactivate your own account')
HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='User not found')
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "update_user_status_api_v1_users__user_id__status_patch",
  "parameters": [
    {
      "in": "path",
      "name": "user_id",
      "required": true,
      "schema": {
        "format": "uuid",
        "title": "User Id",
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
          "$ref": "#/components/schemas/UpdateUserStatusRequest"
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
            "$ref": "#/components/schemas/UserListResponse"
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
