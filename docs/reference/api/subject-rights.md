# Subject Rights API

[Documentation home](../../README.md) · [Regeneration](../../maintainers/maintenance.md)

Generated from tracked source by `scripts/generate_reference.py`. Do not edit by hand. What the checks cover is recorded in [verification](../../maintainers/verification.md).

## POST `/api/v1/me/data-export`

Start My Data Export

Queue an export of everything TestLookup holds about the caller (Art. 15 / 20). 409 ``export_running``
names the live job in ``detail.job_id``.

Source: [backend/app/routers/subject_rights.py:129](../../../backend/app/routers/subject_rights.py#L129).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_signed_in_session.<locals>.signed_in_session`.

Declared Python handler arguments (includes exact role/guard options):

```python
db: AsyncSession=Depends(get_db), current_user: User=Depends(_signed_in_session)
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "start_my_data_export_api_v1_me_data_export_post",
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
    "202": {
      "content": {
        "application/json": {
          "schema": {
            "$ref": "#/components/schemas/SubjectJobRead"
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

## GET `/api/v1/me/data-exports`

List My Data Exports

The caller's own exports of themselves, newest first (≤ 10; ``total`` counts all). Writes nothing.

Source: [backend/app/routers/subject_rights.py:139](../../../backend/app/routers/subject_rights.py#L139).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_signed_in_session.<locals>.signed_in_session`.

Declared Python handler arguments (includes exact role/guard options):

```python
db: AsyncSession=Depends(get_db), current_user: User=Depends(_signed_in_session)
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "list_my_data_exports_api_v1_me_data_exports_get",
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
            "$ref": "#/components/schemas/SubjectJobList"
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

## GET `/api/v1/me/erasure-request`

Get My Erasure Request

The caller's newest erasure request (pending, withdrawn, declined or accepted), or null. Reads only.

Source: [backend/app/routers/subject_erasure_requests.py:58](../../../backend/app/routers/subject_erasure_requests.py#L58).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_signed_in_session.<locals>.signed_in_session`.

Declared Python handler arguments (includes exact role/guard options):

```python
current_user: User=Depends(_signed_in_session), db: AsyncSession=Depends(get_db)
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "get_my_erasure_request_api_v1_me_erasure_request_get",
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
            "anyOf": [
              {
                "$ref": "#/components/schemas/MyErasureRequest"
              },
              {
                "type": "null"
              }
            ],
            "title": "Response Get My Erasure Request Api V1 Me Erasure Request Get"
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

## POST `/api/v1/me/erasure-request`

File My Erasure Request

Ask an administrator to erase the caller's account. One pending request per person: 409
``request_pending`` names it in ``detail.request_id``. Every other active administrator gets a bell row.

Source: [backend/app/routers/subject_erasure_requests.py:69](../../../backend/app/routers/subject_erasure_requests.py#L69).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_signed_in_session.<locals>.signed_in_session`.

Declared Python handler arguments (includes exact role/guard options):

```python
current_user: User=Depends(_signed_in_session), db: AsyncSession=Depends(get_db)
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "file_my_erasure_request_api_v1_me_erasure_request_post",
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
    "201": {
      "content": {
        "application/json": {
          "schema": {
            "$ref": "#/components/schemas/MyErasureRequest"
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

## POST `/api/v1/me/erasure-request/withdraw`

Withdraw My Erasure Request

Withdraw the caller's pending erasure request; 409 ``no_pending_request`` when there is none.

Source: [backend/app/routers/subject_erasure_requests.py:85](../../../backend/app/routers/subject_erasure_requests.py#L85).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_signed_in_session.<locals>.signed_in_session`.

Declared Python handler arguments (includes exact role/guard options):

```python
current_user: User=Depends(_signed_in_session), db: AsyncSession=Depends(get_db)
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "withdraw_my_erasure_request_api_v1_me_erasure_request_withdraw_post",
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
            "$ref": "#/components/schemas/MyErasureRequest"
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

## GET `/api/v1/subject-data-jobs/{subject_job_id}`

Get Subject Data Job

One export or erasure job (the cards poll it). Writes nothing.

Source: [backend/app/routers/subject_rights.py:194](../../../backend/app/routers/subject_rights.py#L194).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_signed_in_session.<locals>.signed_in_session`, `require_subject_job_access.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
subject_job_id: uuid.UUID, db: AsyncSession=Depends(get_db), job: SubjectRightsJob=Depends(require_subject_job_access())
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "get_subject_data_job_api_v1_subject_data_jobs__subject_job_id__get",
  "parameters": [
    {
      "in": "path",
      "name": "subject_job_id",
      "required": true,
      "schema": {
        "format": "uuid",
        "title": "Subject Job Id",
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
            "$ref": "#/components/schemas/SubjectJobRead"
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

## GET `/api/v1/subject-data-jobs/{subject_job_id}/download`

Download Subject Export

Stream a succeeded, unexpired export. 409 not an export / not ready / failed, 410 expired (no audit row on
a refusal); then ONE ``access_audit_logs`` row is COMMITTED — the attempt is recorded even when storage
cannot give the first byte (502 ``archive_unreadable``). The job row is never touched.

Source: [backend/app/routers/subject_rights.py:211](../../../backend/app/routers/subject_rights.py#L211).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_signed_in_session.<locals>.signed_in_session`, `require_subject_job_access.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
subject_job_id: uuid.UUID, db: AsyncSession=Depends(get_db), job: SubjectRightsJob=Depends(require_subject_job_access()), current_user: User=Depends(_signed_in_session)
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "download_subject_export_api_v1_subject_data_jobs__subject_job_id__download_get",
  "parameters": [
    {
      "in": "path",
      "name": "subject_job_id",
      "required": true,
      "schema": {
        "format": "uuid",
        "title": "Subject Job Id",
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
StreamingResponse(_body(), media_type='application/zip', headers=headers)
```

Contract limit: at least one response has an unstructured schema. Read the linked handler/serializer for emitted fields; the empty schema is not a promise of an empty JSON object.

## GET `/api/v1/users/erasure-requests`

List Erasure Requests

Every erasure request of the instance: pending first (oldest first), then decided (newest decided
first), with how many match in all. Reads only.

Source: [backend/app/routers/subject_erasure_requests.py:101](../../../backend/app/routers/subject_erasure_requests.py#L101).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_role.<locals>._check`, `require_signed_in_session.<locals>.signed_in_session`.

Declared Python handler arguments (includes exact role/guard options):

```python
status_filter: str=Query('all', alias='status', pattern=_FILTER_PATTERN), current_user: User=Depends(_signed_in_session), _admin: User=Depends(require_role(UserRole.ADMIN)), db: AsyncSession=Depends(get_db)
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "list_erasure_requests_api_v1_users_erasure_requests_get",
  "parameters": [
    {
      "in": "query",
      "name": "status",
      "required": false,
      "schema": {
        "default": "all",
        "pattern": "^(pending|decided|all)$",
        "title": "Status",
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
            "$ref": "#/components/schemas/ErasureRequestList"
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

## POST `/api/v1/users/erasure-requests/{erasure_request_id}/decline`

Decline Erasure Request

Decline a pending request with one of ``DECLINE_CODES``; the person gets a bell row and reads the code's
sentence on their Profile. Never the caller's own request (409 ``own_request``).

Source: [backend/app/routers/subject_erasure_requests.py:115](../../../backend/app/routers/subject_erasure_requests.py#L115).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_role.<locals>._check`, `require_signed_in_session.<locals>.signed_in_session`.

Declared Python handler arguments (includes exact role/guard options):

```python
erasure_request_id: uuid.UUID, payload: Optional[ErasureRequestDecline]=Body(default=None), current_user: User=Depends(_signed_in_session), _admin: User=Depends(require_role(UserRole.ADMIN)), db: AsyncSession=Depends(get_db)
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "decline_erasure_request_api_v1_users_erasure_requests__erasure_request_id__decline_post",
  "parameters": [
    {
      "in": "path",
      "name": "erasure_request_id",
      "required": true,
      "schema": {
        "format": "uuid",
        "title": "Erasure Request Id",
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
          "anyOf": [
            {
              "$ref": "#/components/schemas/ErasureRequestDecline"
            },
            {
              "type": "null"
            }
          ],
          "title": "Payload"
        }
      }
    }
  },
  "responses": {
    "200": {
      "content": {
        "application/json": {
          "schema": {
            "$ref": "#/components/schemas/ErasureRequestRead"
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

## POST `/api/v1/users/{user_id}/data-export`

Start User Data Export

Queue an export of one person's data (any user, the admin included). 404 ``user_not_found``.

Source: [backend/app/routers/subject_rights.py:159](../../../backend/app/routers/subject_rights.py#L159).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_role.<locals>._check`, `require_signed_in_session.<locals>.signed_in_session`.

Declared Python handler arguments (includes exact role/guard options):

```python
user_id: uuid.UUID, db: AsyncSession=Depends(get_db), current_user: User=Depends(_signed_in_session)
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "start_user_data_export_api_v1_users__user_id__data_export_post",
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
    "202": {
      "content": {
        "application/json": {
          "schema": {
            "$ref": "#/components/schemas/SubjectJobRead"
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

## GET `/api/v1/users/{user_id}/data-jobs`

List User Data Jobs

One person's export / erasure jobs, newest first (≤ 10; ``total`` counts all); ``?kind=export|erasure``
(FastAPI's 422 otherwise — the pattern is built from ``JOB_KINDS``). Writes nothing.

Source: [backend/app/routers/subject_rights.py:175](../../../backend/app/routers/subject_rights.py#L175).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_role.<locals>._check`, `require_signed_in_session.<locals>.signed_in_session`.

Declared Python handler arguments (includes exact role/guard options):

```python
user_id: uuid.UUID, kind: Optional[str]=Query(None, pattern=_KIND_PATTERN), db: AsyncSession=Depends(get_db)
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "list_user_data_jobs_api_v1_users__user_id__data_jobs_get",
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
      "name": "kind",
      "required": false,
      "schema": {
        "anyOf": [
          {
            "pattern": "^(export|erasure)$",
            "type": "string"
          },
          {
            "type": "null"
          }
        ],
        "title": "Kind"
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
            "$ref": "#/components/schemas/SubjectJobList"
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
