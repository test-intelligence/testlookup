# Live Stream API

[Documentation home](../../README.md) · [Regeneration](../../maintainers/maintenance.md)

Generated from tracked source by `scripts/generate_reference.py`. Do not edit by hand. What the checks cover is recorded in [verification](../../maintainers/verification.md).

## GET `/api/v1/stream/active`

List Active Sessions



Source: [backend/app/routers/stream.py:264](../../../backend/app/routers/stream.py#L264).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`.

Declared Python handler arguments (includes exact role/guard options):

```python
project_id: Optional[str]=None, suite_name: Annotated[Optional[list[str]], Query(description=f'Repeatable (OR, at most {MAX_SUITES}), 1-{MAX_SUITE_NAME_LENGTH} characters: sessions in any of these suites; trimmed, case-insensitive.')]=None, days: int=Query(7, ge=0, le=365, description='Cutoff for *completed* sessions/runs to include alongside the always-current active set. 1 = last 24 hours; 0 = no cutoff. Default 7 preserves the prior hardcoded window.'), db: AsyncSession=Depends(get_db), current_user=Depends(get_current_active_user)
```

Direct handler error branches (dependency/service errors can add others):

```python
HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='Invalid project_id — expected a UUID')
HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail='You do not have access to this project')
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "list_active_sessions_api_v1_stream_active_get",
  "parameters": [
    {
      "in": "query",
      "name": "project_id",
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
        "title": "Project Id"
      }
    },
    {
      "description": "Repeatable (OR, at most 50), 1-500 characters: sessions in any of these suites; trimmed, case-insensitive.",
      "in": "query",
      "name": "suite_name",
      "required": false,
      "schema": {
        "anyOf": [
          {
            "items": {
              "type": "string"
            },
            "type": "array"
          },
          {
            "type": "null"
          }
        ],
        "description": "Repeatable (OR, at most 50), 1-500 characters: sessions in any of these suites; trimmed, case-insensitive.",
        "title": "Suite Name"
      }
    },
    {
      "description": "Cutoff for *completed* sessions/runs to include alongside the always-current active set. 1 = last 24 hours; 0 = no cutoff. Default 7 preserves the prior hardcoded window.",
      "in": "query",
      "name": "days",
      "required": false,
      "schema": {
        "default": 7,
        "description": "Cutoff for *completed* sessions/runs to include alongside the always-current active set. 1 = last 24 hours; 0 = no cutoff. Default 7 preserves the prior hardcoded window.",
        "maximum": 365,
        "minimum": 0,
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
          "schema": {
            "$ref": "#/components/schemas/ActiveSessionsResponse"
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

## POST `/api/v1/stream/events/batch`

Ingest Event Batch



Source: [backend/app/routers/stream.py:143](../../../backend/app/routers/stream.py#L143).

Dependency chain: .

Declared Python handler arguments (includes exact role/guard options):

```python
batch: LiveEventBatch, x_session_token: str=Header(..., alias='X-Session-Token')
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "ingest_event_batch_api_v1_stream_events_batch_post",
  "parameters": [
    {
      "in": "header",
      "name": "X-Session-Token",
      "required": true,
      "schema": {
        "title": "X-Session-Token",
        "type": "string"
      }
    }
  ],
  "requestBody": {
    "content": {
      "application/json": {
        "schema": {
          "$ref": "#/components/schemas/LiveEventBatch"
        }
      }
    },
    "required": true
  },
  "responses": {
    "202": {
      "content": {
        "application/json": {
          "schema": {
            "$ref": "#/components/schemas/LiveEventBatchResponse"
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

## POST `/api/v1/stream/http-exchanges`

Ingest Http Exchanges

W23d AGT-14: the captured HTTP exchanges of ONE live test (opt-in capture).

Authenticated like ``POST /ingest`` (JWT or API key, the ingest role). The
BODY ``session_id`` names the live session; the project, the run and the
test case id are DERIVED from that session, never read from the body. A key
bound to another project is refused, the caller must be a member of the
session's project, and the body ``run_id`` must be the session's own. The
exchanges were already cut and redacted by the model; the store redacts
them again (idempotent) and never fails the request on a Mongo error.

Source: [backend/app/routers/stream.py:207](../../../backend/app/routers/stream.py#L207).

Dependency chain: `OAuth2PasswordBearer`, `get_api_key_context`, `get_db`.

Declared Python handler arguments (includes exact role/guard options):

```python
payload: LiveHttpExchangesIn, db: AsyncSession=Depends(get_db), auth: tuple[User, uuid.UUID | None]=Depends(get_api_key_context)
```

Direct handler error branches (dependency/service errors can add others):

```python
HTTPException(status_code=404, detail='Session not found')
HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail='This API key is restricted to a different project')
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "ingest_http_exchanges_api_v1_stream_http_exchanges_post",
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
          "$ref": "#/components/schemas/LiveHttpExchangesIn"
        }
      }
    },
    "required": true
  },
  "responses": {
    "202": {
      "content": {
        "application/json": {
          "schema": {
            "$ref": "#/components/schemas/LiveHttpExchangesResponse"
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

## POST `/api/v1/stream/ingest`

Ingest Via Api Key

Stream test results using only an API key — no /sessions ceremony.

Auth: ``X-API-Key`` only. The key must be project-scoped (so the server
can derive ``project_id`` itself) and carry the ``stream:write`` scope.
The first call for a given ``run_id`` auto-creates a live session;
subsequent calls reuse it.

Source: [backend/app/routers/stream.py:166](../../../backend/app/routers/stream.py#L166).

Dependency chain: `get_db`, `get_streaming_api_key_context`.

Declared Python handler arguments (includes exact role/guard options):

```python
payload: LiveStreamIngestRequest, db: AsyncSession=Depends(get_db), auth: StreamingApiKeyContext=Depends(get_streaming_api_key_context)
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "ingest_via_api_key_api_v1_stream_ingest_post",
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
          "$ref": "#/components/schemas/LiveStreamIngestRequest"
        }
      }
    },
    "required": true
  },
  "responses": {
    "202": {
      "content": {
        "application/json": {
          "schema": {
            "$ref": "#/components/schemas/LiveStreamIngestResponse"
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

## POST `/api/v1/stream/sessions`

Create Session



Source: [backend/app/routers/stream.py:54](../../../backend/app/routers/stream.py#L54).

Dependency chain: `OAuth2PasswordBearer`, `get_api_key_context`, `get_db`.

Declared Python handler arguments (includes exact role/guard options):

```python
payload: LiveSessionCreate, db: AsyncSession=Depends(get_db), auth: tuple[User, None]=Depends(get_api_key_context)
```

Direct handler error branches (dependency/service errors can add others):

```python
HTTPException(status_code=403, detail='Inactive user account')
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "create_session_api_v1_stream_sessions_post",
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
          "$ref": "#/components/schemas/LiveSessionCreate"
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
            "$ref": "#/components/schemas/LiveSessionResponse"
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

## DELETE `/api/v1/stream/sessions/{session_id}`

Close Session



Source: [backend/app/routers/stream.py:108](../../../backend/app/routers/stream.py#L108).

Dependency chain: `OAuth2PasswordBearer`, `get_api_key_context`, `get_db`.

Declared Python handler arguments (includes exact role/guard options):

```python
session_id: str, db: AsyncSession=Depends(get_db), auth: tuple[User, uuid.UUID | None]=Depends(get_api_key_context)
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "close_session_api_v1_stream_sessions__session_id__delete",
  "parameters": [
    {
      "in": "path",
      "name": "session_id",
      "required": true,
      "schema": {
        "title": "Session Id",
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

## GET `/api/v1/stream/sessions/{session_id}`

Get Session



Source: [backend/app/routers/stream.py:92](../../../backend/app/routers/stream.py#L92).

Dependency chain: `OAuth2PasswordBearer`, `get_api_key_context`, `get_db`.

Declared Python handler arguments (includes exact role/guard options):

```python
session_id: str, db: AsyncSession=Depends(get_db), auth: tuple[User, uuid.UUID | None]=Depends(get_api_key_context)
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "get_session_api_v1_stream_sessions__session_id__get",
  "parameters": [
    {
      "in": "path",
      "name": "session_id",
      "required": true,
      "schema": {
        "title": "Session Id",
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
await stream_service.get_session(db, session_id, bound_project_id=bound_project_id, user=current_user)
```

Contract limit: at least one response has an unstructured schema. Read the linked handler/serializer for emitted fields; the empty schema is not a promise of an empty JSON object.

## GET `/api/v1/stream/sse/{project_id}`

Sse Stream



Source: [backend/app/routers/stream.py:339](../../../backend/app/routers/stream.py#L339).

Dependency chain: .

Declared Python handler arguments (includes exact role/guard options):

```python
project_id: str, request: Request, token: str
```

Direct handler error branches (dependency/service errors can add others):

```python
HTTPException(status_code=400, detail='Invalid Last-Event-ID')
HTTPException(status_code=400, detail='Invalid project or user ID')
HTTPException(status_code=401, detail='Invalid token')
HTTPException(status_code=403, detail='You do not have access to this project')
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "sse_stream_api_v1_stream_sse__project_id__get",
  "parameters": [
    {
      "in": "path",
      "name": "project_id",
      "required": true,
      "schema": {
        "title": "Project Id",
        "type": "string"
      }
    },
    {
      "in": "query",
      "name": "token",
      "required": true,
      "schema": {
        "title": "Token",
        "type": "string"
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
StreamingResponse(generate(), media_type='text/event-stream', headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no', 'Connection': 'keep-alive'})
```

Contract limit: at least one response has an unstructured schema. Read the linked handler/serializer for emitted fields; the empty schema is not a promise of an empty JSON object.
