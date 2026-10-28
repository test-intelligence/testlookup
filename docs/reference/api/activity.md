# Activity API

[Documentation home](../../README.md) · [Regeneration](../../maintainers/maintenance.md)

Generated from tracked source by `scripts/generate_reference.py`. Do not edit by hand. What the checks cover is recorded in [verification](../../maintainers/verification.md).

## GET `/api/v1/activity/event-types`

The activity event registry, grouped by category

Feeds the filter UI. Not project-scoped: the vocabulary is global.

Source: [backend/app/routers/activity.py:223](../../../backend/app/routers/activity.py#L223).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`.

Declared Python handler arguments (includes exact role/guard options):

```python
current_user: User=Depends(get_current_active_user)
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "list_event_types_api_v1_activity_event_types_get",
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
            "title": "Response List Event Types Api V1 Activity Event Types Get",
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

## GET `/api/v1/activity/mine`

What I did and what involves me, across my projects (keyset paginated)

The caller's own rows and the rows involving them (Inbox › Activity).

Every role. The QUERY ``project_id`` is authorized by
``resolve_project_scope`` (a non-member's id → 403) BEFORE any ledger read;
without it the read covers every project the caller can reach (ADMIN:
all; a project-bound key: its project). Part B (mentions) never reads
beyond the caller's OWN memberships. A read: nothing is written.

Source: [backend/app/routers/activity.py:304](../../../backend/app/routers/activity.py#L304).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `my_activity_filters`.

Declared Python handler arguments (includes exact role/guard options):

```python
filters=Depends(my_activity_filters), project_id: Optional[uuid.UUID]=Query(None, description="Pin one project (the Inbox's active project)"), db: AsyncSession=Depends(get_db), current_user: User=Depends(get_current_active_user)
```

Direct handler error branches (dependency/service errors can add others):

```python
HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "list_my_activity_api_v1_activity_mine_get",
  "parameters": [
    {
      "description": "Pin one project (the Inbox's active project)",
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
        "description": "Pin one project (the Inbox's active project)",
        "title": "Project Id"
      }
    },
    {
      "description": "all|mine|involving (default all)",
      "in": "query",
      "name": "view",
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
        "description": "all|mine|involving (default all)",
        "title": "View"
      }
    },
    {
      "description": "Default 7 days ago; at most 90",
      "in": "query",
      "name": "since",
      "required": false,
      "schema": {
        "anyOf": [
          {
            "format": "date-time",
            "type": "string"
          },
          {
            "type": "null"
          }
        ],
        "description": "Default 7 days ago; at most 90",
        "title": "Since"
      }
    },
    {
      "in": "query",
      "name": "until",
      "required": false,
      "schema": {
        "anyOf": [
          {
            "format": "date-time",
            "type": "string"
          },
          {
            "type": "null"
          }
        ],
        "title": "Until"
      }
    },
    {
      "description": "1-100 (default 50)",
      "in": "query",
      "name": "limit",
      "required": false,
      "schema": {
        "anyOf": [
          {
            "type": "integer"
          },
          {
            "type": "null"
          }
        ],
        "description": "1-100 (default 50)",
        "title": "Limit"
      }
    },
    {
      "description": "Opaque keyset cursor from a previous page",
      "in": "query",
      "name": "cursor",
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
        "description": "Opaque keyset cursor from a previous page",
        "title": "Cursor"
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
            "additionalProperties": true,
            "title": "Response List My Activity Api V1 Activity Mine Get",
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

## GET `/api/v1/projects/{project_id}/activity`

Project activity feed (keyset paginated)

One page of the project's activity, newest first.

Any member of the project can read this — see the module docstring.

Source: [backend/app/routers/activity.py:344](../../../backend/app/routers/activity.py#L344).

Dependency chain: `OAuth2PasswordBearer`, `activity_filters`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_project_access.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
project_id: uuid.UUID, filters: ActivityFilters=Depends(activity_filters), db: AsyncSession=Depends(get_db), current_user: User=Depends(get_current_active_user), _: User=Depends(require_project_access())
```

Direct handler error branches (dependency/service errors can add others):

```python
HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "list_activity_api_v1_projects__project_id__activity_get",
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
      "description": "Repeatable category filter",
      "in": "query",
      "name": "category",
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
        "description": "Repeatable category filter",
        "title": "Category"
      }
    },
    {
      "description": "Repeatable event-type filter",
      "in": "query",
      "name": "event_type",
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
        "description": "Repeatable event-type filter",
        "title": "Event Type"
      }
    },
    {
      "in": "query",
      "name": "actor_id",
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
        "title": "Actor Id"
      }
    },
    {
      "description": "user|api_key|service_account|system|agent",
      "in": "query",
      "name": "actor_type",
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
        "description": "user|api_key|service_account|system|agent",
        "title": "Actor Type"
      }
    },
    {
      "in": "query",
      "name": "entity_type",
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
        "title": "Entity Type"
      }
    },
    {
      "in": "query",
      "name": "entity_id",
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
        "title": "Entity Id"
      }
    },
    {
      "in": "query",
      "name": "release_id",
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
        "title": "Release Id"
      }
    },
    {
      "in": "query",
      "name": "since",
      "required": false,
      "schema": {
        "anyOf": [
          {
            "format": "date-time",
            "type": "string"
          },
          {
            "type": "null"
          }
        ],
        "title": "Since"
      }
    },
    {
      "in": "query",
      "name": "until",
      "required": false,
      "schema": {
        "anyOf": [
          {
            "format": "date-time",
            "type": "string"
          },
          {
            "type": "null"
          }
        ],
        "title": "Until"
      }
    },
    {
      "description": "Free text over the summary; ignored under 3 characters",
      "in": "query",
      "name": "q",
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
        "description": "Free text over the summary; ignored under 3 characters",
        "title": "Q"
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
      "description": "Opaque keyset cursor from a previous page",
      "in": "query",
      "name": "cursor",
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
        "description": "Opaque keyset cursor from a previous page",
        "title": "Cursor"
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
            "additionalProperties": true,
            "title": "Response List Activity Api V1 Projects  Project Id  Activity Get",
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

## GET `/api/v1/projects/{project_id}/activity/entity/{entity_type}/{entity_id}`

Everything that happened to one entity

Embedded by run and release detail pages.

Source: [backend/app/routers/activity.py:367](../../../backend/app/routers/activity.py#L367).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_project_access.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
project_id: uuid.UUID, entity_type: str, entity_id: str, limit: int=Query(50, ge=1, le=activity_query.MAX_LIMIT), db: AsyncSession=Depends(get_db), current_user: User=Depends(get_current_active_user), _: User=Depends(require_project_access())
```

Direct handler error branches (dependency/service errors can add others):

```python
HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"Unknown entity_type. Valid: {', '.join(activity_events.ENTITY_TYPES)}.")
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "list_entity_timeline_api_v1_projects__project_id__activity_entity__entity_type___entity_id__get",
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
      "name": "entity_type",
      "required": true,
      "schema": {
        "title": "Entity Type",
        "type": "string"
      }
    },
    {
      "in": "path",
      "name": "entity_id",
      "required": true,
      "schema": {
        "title": "Entity Id",
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
          "schema": {
            "additionalProperties": true,
            "title": "Response List Entity Timeline Api V1 Projects  Project Id  Activity Entity  Entity Type   Entity Id  Get",
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

## GET `/api/v1/projects/{project_id}/activity/export`

Export the filtered activity history (QA_LEAD+)

Stream the history out as CSV or NDJSON. Lead-on-THIS-project only.

Capped at ``EXPORT_MAX_ROWS``; a truncated export says so in a header
instead of silently returning a prefix that reads as the whole story.

Source: [backend/app/routers/activity.py:392](../../../backend/app/routers/activity.py#L392).

Dependency chain: `OAuth2PasswordBearer`, `activity_filters`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_project_access.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
project_id: uuid.UUID, request: Request, format: str=Query('csv', pattern='^(csv|ndjson)$'), filters: ActivityFilters=Depends(activity_filters), db: AsyncSession=Depends(get_db), current_user: User=Depends(get_current_active_user), _: User=Depends(require_project_access())
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "export_activity_api_v1_projects__project_id__activity_export_get",
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
      "in": "query",
      "name": "format",
      "required": false,
      "schema": {
        "default": "csv",
        "pattern": "^(csv|ndjson)$",
        "title": "Format",
        "type": "string"
      }
    },
    {
      "description": "Repeatable category filter",
      "in": "query",
      "name": "category",
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
        "description": "Repeatable category filter",
        "title": "Category"
      }
    },
    {
      "description": "Repeatable event-type filter",
      "in": "query",
      "name": "event_type",
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
        "description": "Repeatable event-type filter",
        "title": "Event Type"
      }
    },
    {
      "in": "query",
      "name": "actor_id",
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
        "title": "Actor Id"
      }
    },
    {
      "description": "user|api_key|service_account|system|agent",
      "in": "query",
      "name": "actor_type",
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
        "description": "user|api_key|service_account|system|agent",
        "title": "Actor Type"
      }
    },
    {
      "in": "query",
      "name": "entity_type",
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
        "title": "Entity Type"
      }
    },
    {
      "in": "query",
      "name": "entity_id",
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
        "title": "Entity Id"
      }
    },
    {
      "in": "query",
      "name": "release_id",
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
        "title": "Release Id"
      }
    },
    {
      "in": "query",
      "name": "since",
      "required": false,
      "schema": {
        "anyOf": [
          {
            "format": "date-time",
            "type": "string"
          },
          {
            "type": "null"
          }
        ],
        "title": "Since"
      }
    },
    {
      "in": "query",
      "name": "until",
      "required": false,
      "schema": {
        "anyOf": [
          {
            "format": "date-time",
            "type": "string"
          },
          {
            "type": "null"
          }
        ],
        "title": "Until"
      }
    },
    {
      "description": "Free text over the summary; ignored under 3 characters",
      "in": "query",
      "name": "q",
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
        "description": "Free text over the summary; ignored under 3 characters",
        "title": "Q"
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
      "description": "Opaque keyset cursor from a previous page",
      "in": "query",
      "name": "cursor",
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
        "description": "Opaque keyset cursor from a previous page",
        "title": "Cursor"
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
Response(content=body, media_type=media_type, headers=headers)
```

Contract limit: at least one response has an unstructured schema. Read the linked handler/serializer for emitted fields; the empty schema is not a promise of an empty JSON object.

## GET `/api/v1/projects/{project_id}/activity/stream`

Live tail of the project's activity (text/event-stream; signed-in sessions only)

New ledger rows of this project as Server-Sent Events, after their commit.

Declared ABOVE ``/{event_id}`` (else ``stream`` would 422 as a UUID). JWT
only: an API key polls the feed. Resume with the ``Last-Event-ID`` HEADER.
The request's pooled connection is released BEFORE the response starts and
the generator never touches Postgres: a held session per open tab would
starve the 2 + 1 pool. Redis is read with short non-blocking ``XRANGE``.

Source: [backend/app/routers/activity.py:492](../../../backend/app/routers/activity.py#L492).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_project_access.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
project_id: uuid.UUID, request: Request, db: AsyncSession=Depends(get_db), current_user: User=Depends(get_current_active_user), _: User=Depends(require_project_access())
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "stream_activity_api_v1_projects__project_id__activity_stream_get",
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
StreamingResponse(frames, media_type='text/event-stream', headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no', 'Connection': 'keep-alive'})
```

Contract limit: at least one response has an unstructured schema. Read the linked handler/serializer for emitted fields; the empty schema is not a promise of an empty JSON object.

## GET `/api/v1/projects/{project_id}/activity/{event_id}`

One activity event, with its before/after diff

The drawer's payload. Registered LAST so the literal-segment routes
above (``export``, ``entity``) are matched before this UUID pattern.

Source: [backend/app/routers/activity.py:564](../../../backend/app/routers/activity.py#L564).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_project_access.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
project_id: uuid.UUID, event_id: uuid.UUID, db: AsyncSession=Depends(get_db), current_user: User=Depends(get_current_active_user), _: User=Depends(require_project_access())
```

Direct handler error branches (dependency/service errors can add others):

```python
HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Activity event not found')
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "get_activity_event_api_v1_projects__project_id__activity__event_id__get",
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
      "name": "event_id",
      "required": true,
      "schema": {
        "format": "uuid",
        "title": "Event Id",
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
            "additionalProperties": true,
            "title": "Response Get Activity Event Api V1 Projects  Project Id  Activity  Event Id  Get",
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
