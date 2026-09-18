# Search API

[Documentation home](../../README.md) · [Regeneration](../../handoff/maintenance.md)

Generated from tracked source by `scripts/generate_handoff_reference.py`. Do not edit by hand. The baseline and validation limits are recorded in [verification](../../handoff/verification.md).

## GET `/api/v1/search`

Search Test Cases

Search test cases by name, suite, or error message.

search_type options:
- keyword  (default): ILIKE substring matching across name/suite/error columns.
- semantic: ChromaDB vector-similarity search — finds conceptually similar failures.
- hybrid:   Merges keyword + semantic results, deduplicates, and re-ranks by relevance.

The response always includes search_type to reflect the mode actually used
(may fall back to keyword if ChromaDB is unavailable).

Source: [backend/app/routers/search.py:311](../../../backend/app/routers/search.py#L311).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`.

Declared Python handler arguments (includes exact role/guard options):

```python
q: str=Query(..., min_length=1), project_id: str | None=None, status: str | None=None, days: int=Query(None, ge=1, le=365), search_type: str=Query('keyword', pattern='^(keyword|semantic|hybrid)$'), page: int=Query(1, ge=1), size: int=Query(20, ge=1, le=100), db: AsyncSession=Depends(get_db), current_user: User=Depends(get_current_active_user)
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "search_test_cases_api_v1_search_get",
  "parameters": [
    {
      "in": "query",
      "name": "q",
      "required": true,
      "schema": {
        "minLength": 1,
        "title": "Q",
        "type": "string"
      }
    },
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
      "in": "query",
      "name": "status",
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
        "title": "Status"
      }
    },
    {
      "in": "query",
      "name": "days",
      "required": false,
      "schema": {
        "maximum": 365,
        "minimum": 1,
        "title": "Days",
        "type": "integer"
      }
    },
    {
      "in": "query",
      "name": "search_type",
      "required": false,
      "schema": {
        "default": "keyword",
        "pattern": "^(keyword|semantic|hybrid)$",
        "title": "Search Type",
        "type": "string"
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
      "name": "size",
      "required": false,
      "schema": {
        "default": 20,
        "maximum": 100,
        "minimum": 1,
        "title": "Size",
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
await _keyword()
await hybrid_search(db, **common_kwargs)
await search_test_cases_query(db, **common_kwargs)
await semantic_search(db, **common_kwargs)
result[1] == 0
{'items': items, 'total': total, 'query': q, 'search_type': actual_type, 'page': page, 'size': size, 'pages': pages, 'result_status': 'complete' if actual_type == 'keyword' else 'partial', 'counts_are_exact': actual_type == 'keyword', 'failed_entity_types': [], 'scope': dict(RELEASE_SCOPE_DECLARATION)}
```

Contract limit: at least one response has an unstructured schema. Read the linked handler/serializer for emitted fields; the empty schema is not a promise of an empty JSON object.

## GET `/api/v1/search/entity-counts`

Get Entity Counts

Project-scoped totals for the /search page's chip + Index Health panels.

The page previously sourced these counts from ``response.entity_counts`` —
the per-query result counts — so the user saw 0 everywhere before they
typed anything. The chip count for ``Tests`` was meant to read "how many
test cases exist in this project", not "how many match the empty query".
This endpoint fills that gap with one round trip of cheap COUNT queries.

Filtering:
  * If ``project_id`` is provided, scope to that single project (after
    tenant-access validation by ``resolve_project_scope``).
  * If ``project_id`` is omitted (or the All-Projects sentinel resolved
    to ``None``), scope to the union of projects the caller can see;
    ADMIN gets the full instance.

Notes:
  * ``test_case`` joins against ``test_runs`` because test_cases hold a
    ``test_run_id`` FK, not a direct ``project_id`` column.
  * ``flaky_test`` counts ``flaky_quarantine_requests`` rows in *any*
    state — proposed/approved/quarantined/re_quarantined — because the
    /search page's ``Flaky`` chip is "everything the system has seen
    flagged as flaky", not just live quarantines. Released rows are
    excluded since those are no longer flagged-as-flaky.

Source: [backend/app/routers/search.py:143](../../../backend/app/routers/search.py#L143).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`.

Declared Python handler arguments (includes exact role/guard options):

```python
project_id: Optional[str]=Query(None), db: AsyncSession=Depends(get_db), current_user: User=Depends(get_current_active_user)
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "get_entity_counts_api_v1_search_entity_counts_get",
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
int(result.scalar_one() or 0)
stmt
stmt.where(False)
stmt.where(column == uuid.UUID(str(scoped_project_id)))
stmt.where(column.in_(allowed_project_ids))
{'test_case': test_case, 'test_run': test_run, 'suite': suite, 'defect': defect, 'flaky_test': flaky_test, 'release': release}
```

Contract limit: at least one response has an unstructured schema. Read the linked handler/serializer for emitted fields; the empty schema is not a promise of an empty JSON object.

## GET `/api/v1/search/global`

Global Search Endpoint

System-wide global search across multiple entity types.

Searches test cases, test runs, suites, defects, flaky tests, and releases.
Returns mixed results with entity badges and navigation URLs.

An empty ``q`` acts as a browse: the chips on the /search page show
project-scoped totals from ``/entity-counts``, and selecting a chip with
no query should surface real records of that type rather than a blank
panel. Adapters fall back to "most recent N" when ``q`` is empty.

Source: [backend/app/routers/search.py:440](../../../backend/app/routers/search.py#L440).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`.

Declared Python handler arguments (includes exact role/guard options):

```python
q: str=Query('', description='Search query — empty browses the most-recent items in scope'), project_id: Optional[str]=None, entity_types: Optional[str]=Query(None, description='Comma-separated entity types to search'), days: Optional[int]=Query(None, ge=1, le=365), page: int=Query(1, ge=1), size: int=Query(20, ge=1, le=100), db: AsyncSession=Depends(get_db), current_user: User=Depends(get_current_active_user)
```

Direct handler error branches (dependency/service errors can add others):

```python
HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Unsupported entity types: {', '.join(sorted(invalid_types))}")
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "global_search_endpoint_api_v1_search_global_get",
  "parameters": [
    {
      "description": "Search query — empty browses the most-recent items in scope",
      "in": "query",
      "name": "q",
      "required": false,
      "schema": {
        "default": "",
        "description": "Search query — empty browses the most-recent items in scope",
        "title": "Q",
        "type": "string"
      }
    },
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
      "description": "Comma-separated entity types to search",
      "in": "query",
      "name": "entity_types",
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
        "description": "Comma-separated entity types to search",
        "title": "Entity Types"
      }
    },
    {
      "in": "query",
      "name": "days",
      "required": false,
      "schema": {
        "anyOf": [
          {
            "maximum": 365,
            "minimum": 1,
            "type": "integer"
          },
          {
            "type": "null"
          }
        ],
        "title": "Days"
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
      "name": "size",
      "required": false,
      "schema": {
        "default": 20,
        "maximum": 100,
        "minimum": 1,
        "title": "Size",
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
result
```

Contract limit: at least one response has an unstructured schema. Read the linked handler/serializer for emitted fields; the empty schema is not a promise of an empty JSON object.

## GET `/api/v1/search/index-status`

Get Index Status

Return collection health within the caller's active project scope.

Source: [backend/app/routers/search.py:59](../../../backend/app/routers/search.py#L59).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`.

Declared Python handler arguments (includes exact role/guard options):

```python
project_id: str | None=None, db: AsyncSession=Depends(get_db), current_user: User=Depends(get_current_active_user)
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "get_index_status_api_v1_search_index_status_get",
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
await _get_index_status(project_id=str(scoped_project_id) if scoped_project_id else None, allowed_project_ids=active_project_ids)
```

Contract limit: at least one response has an unstructured schema. Read the linked handler/serializer for emitted fields; the empty schema is not a promise of an empty JSON object.

## POST `/api/v1/search/reindex`

Trigger Reindex

Manually trigger a search reindex via Celery task.

Pass ``full=true`` for a complete rebuild; default is incremental.

Authorization mirrors the blast radius rather than one flat role. This
endpoint previously took no user at all — every other endpoint in this
module depends on ``get_current_active_user`` — so a VIEWER holding zero
project memberships could queue an instance-wide rebuild.

* A named project needs QA_LEAD **and** membership: reindexing burns
  worker capacity, which VIEWER (read-only) has no business spending.
* No project named means *every* tenant's index, so that variant is
  ADMIN-only — otherwise a lead in one project rebuilds everyone else's.

Source: [backend/app/routers/search.py:86](../../../backend/app/routers/search.py#L86).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`.

Declared Python handler arguments (includes exact role/guard options):

```python
project_id: str | None=None, full: bool=False, db: AsyncSession=Depends(get_db), current_user: User=Depends(get_current_active_user)
```

Direct handler error branches (dependency/service errors can add others):

```python
HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail='An instance-wide reindex requires an instance ADMIN, not a project-bound API key. Pass project_id to reindex one project.')
HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail='Triggering a reindex requires QA_LEAD or higher')
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "trigger_reindex_api_v1_search_reindex_post",
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
      "in": "query",
      "name": "full",
      "required": false,
      "schema": {
        "default": false,
        "title": "Full",
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
{'task_id': task.id, 'status': 'queued', 'mode': 'full' if full else 'incremental'}
```

Contract limit: at least one response has an unstructured schema. Read the linked handler/serializer for emitted fields; the empty schema is not a promise of an empty JSON object.

## GET `/api/v1/search/similar/{test_case_id}`

Find Similar Failures

Find similar failures within the source test case's project.

Source: [backend/app/routers/search.py:256](../../../backend/app/routers/search.py#L256).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`.

Declared Python handler arguments (includes exact role/guard options):

```python
test_case_id: str, limit: int=Query(5, ge=1, le=20), db: AsyncSession=Depends(get_db), current_user: User=Depends(get_current_active_user)
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "find_similar_failures_api_v1_search_similar__test_case_id__get",
  "parameters": [
    {
      "in": "path",
      "name": "test_case_id",
      "required": true,
      "schema": {
        "title": "Test Case Id",
        "type": "string"
      }
    },
    {
      "in": "query",
      "name": "limit",
      "required": false,
      "schema": {
        "default": 5,
        "maximum": 20,
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
{'items': [], 'total': 0, 'query': test_case_id}
{'items': items[:limit], 'total': len(items), 'query': query_text[:100]}
```

Contract limit: at least one response has an unstructured schema. Read the linked handler/serializer for emitted fields; the empty schema is not a promise of an empty JSON object.
