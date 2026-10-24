# Search API

[Documentation home](../../README.md) · [Regeneration](../../maintainers/maintenance.md)

Generated from tracked source by `scripts/generate_reference.py`. Do not edit by hand. What the checks cover is recorded in [verification](../../maintainers/verification.md).

## GET `/api/v1/search`

Search Test Cases

Search test cases by name, suite, or error message.

search_type options:
- keyword  (default): ILIKE substring matching across name/suite/error columns.
- semantic: ChromaDB vector-similarity search — finds conceptually similar failures.
- hybrid:   Merges keyword + semantic results, deduplicates, and re-ranks by relevance.

The response always includes search_type to reflect the mode actually used
(may fall back to keyword if ChromaDB is unavailable), plus
``requested_search_type`` and, when they differ, WHY (``fallback_reason``:
``semantic_unavailable`` -- the index raised; ``no_semantic_match`` -- it
answered nothing; ``no_text`` -- the query is filters only) with the
sentence the page shows (``fallback_message``).

``q`` takes the structured syntax; this test-result path applies
``status:`` (one value), ``since:`` and ``type:`` naming test results.
Any other filter means nothing is searched, and ``parsed.notes`` says so.
``record=true`` (the /search page's own page-1 searches) records the
search after the response (a recent + a usage row, no query text in the
row).

Source: [backend/app/routers/search.py:530](../../../backend/app/routers/search.py#L530).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`.

Declared Python handler arguments (includes exact role/guard options):

```python
q: str=Query(..., min_length=1), project_id: str | None=None, status: str | None=None, days: int=Query(None, ge=1, le=365), search_type: str=Query('keyword', pattern='^(keyword|semantic|hybrid)$'), page: int=Query(1, ge=1), size: int=Query(20, ge=1, le=100), db: AsyncSession=Depends(get_db), current_user: User=Depends(get_current_active_user), record: bool=False, background_tasks: BackgroundTasks=None
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
      "in": "query",
      "name": "record",
      "required": false,
      "schema": {
        "default": false,
        "title": "Record",
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
await _keyword()
await hybrid_search(db, **common_kwargs)
await search_test_cases_query(db, **common_kwargs)
await semantic_search(db, **common_kwargs, raise_on_provider_error=True)
response
result[1] == 0
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

Source: [backend/app/routers/search.py:331](../../../backend/app/routers/search.py#L331).

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

``q`` takes the structured syntax (``status:`` ``suite:`` ``branch:``
``env:`` ``type:`` ``since:`` ``release:`` ``owner:``); the ``parsed``
block lists the applied filters, the ignored tokens, the entity types
that could not apply a filter (and so were not searched) and a sentence
for each. A ``release:`` value is resolved only inside the caller's
scope. ``record=true`` (the /search page's own page-1 searches) records
the search after the response.

Source: [backend/app/routers/search.py:745](../../../backend/app/routers/search.py#L745).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`.

Declared Python handler arguments (includes exact role/guard options):

```python
q: str=Query('', description='Search query — empty browses the most-recent items in scope'), project_id: Optional[str]=None, entity_types: Optional[str]=Query(None, description='Comma-separated entity types to search'), days: Optional[int]=Query(None, ge=1, le=365), page: int=Query(1, ge=1), size: int=Query(20, ge=1, le=100), db: AsyncSession=Depends(get_db), current_user: User=Depends(get_current_active_user), record: bool=False, background_tasks: BackgroundTasks=None
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
      "in": "query",
      "name": "record",
      "required": false,
      "schema": {
        "default": false,
        "title": "Record",
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
result
```

Contract limit: at least one response has an unstructured schema. Read the linked handler/serializer for emitted fields; the empty schema is not a promise of an empty JSON object.

## GET `/api/v1/search/index-status`

Get Index Status

Return the semantic (ChromaDB) index's health within the caller's active project scope.

``status``: ``healthy`` (with ``document_count`` and ``last_indexed_at``),
``not_configured`` (no ChromaDB server answers at the configured address,
e.g. a ``make dev`` stack without the ``local-llm`` profile) or
``unavailable`` (a server answered but the read failed). The index holds
test cases only; global search is keyword over every entity either way.

W12 SRCH-01/-02: also the active collection / generation / embedder (from
``get_index_status``) and whether Hybrid / Semantic can answer here:
``semantic_available``, ``semantic_reason`` and its sentence
(``core/search_vocab.semantic_availability``). Every key is always present.

Source: [backend/app/routers/search.py:217](../../../backend/app/routers/search.py#L217).

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
index_status
```

Contract limit: at least one response has an unstructured schema. Read the linked handler/serializer for emitted fields; the empty schema is not a promise of an empty JSON object.

## DELETE `/api/v1/search/recent`

Clear Recent Searches

Forget the caller's recent searches (their own Redis list only).

Source: [backend/app/routers/search.py:889](../../../backend/app/routers/search.py#L889).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`.

Declared Python handler arguments (includes exact role/guard options):

```python
current_user: User=Depends(get_current_active_user)
```

Direct handler error branches (dependency/service errors can add others):

```python
HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=RECENTS_UNAVAILABLE)
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "clear_recent_searches_api_v1_search_recent_delete",
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
    }
  ]
}
```

## GET `/api/v1/search/recent`

List Recent Searches

The caller's own last searches (Redis, newest first, at most 20, kept 30 days).

Keyed by the CALLER's id. Each entry's project is re-checked against the
projects the reader can access NOW: an entry of a project they lost is
left out of the answer (this GET writes nothing, so it is filtered, not
deleted). A Redis fault answers an empty list.

Source: [backend/app/routers/search.py:865](../../../backend/app/routers/search.py#L865).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`.

Declared Python handler arguments (includes exact role/guard options):

```python
db: AsyncSession=Depends(get_db), current_user: User=Depends(get_current_active_user)
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "list_recent_searches_api_v1_search_recent_get",
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
            "$ref": "#/components/schemas/RecentSearchesResponse"
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

Source: [backend/app/routers/search.py:274](../../../backend/app/routers/search.py#L274).

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

Find similar FAILURES within the source test case's project.

The source's project is authorised (``resolve_project_scope``) before any
Chroma read; a malformed, unknown or textless id answers an empty list.
The semantic read asks for ``min(limit * 4, 40)`` candidates and keeps
FAILED / BROKEN results only, the source excluded, the top ``limit`` in
relevance order. ``semantic_status`` says whether the index answered:
``ok``, ``not_configured`` (no ChromaDB server answers) or ``unavailable``
-- an outage is never shown as "nothing similar".

Source: [backend/app/routers/search.py:444](../../../backend/app/routers/search.py#L444).

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
{'items': items, 'total': len(items), 'query': query_text[:100], 'semantic_status': semantic_status}
```

Contract limit: at least one response has an unstructured schema. Read the linked handler/serializer for emitted fields; the empty schema is not a promise of an empty JSON object.

## GET `/api/v1/search/suggested`

Suggested Searches

What the /search page offers while its box is empty, for one project.

The tests failing most in the last 7 days (top 5, each with its failure
count), the newest open defects with a Jira key (top 5) and the active
release. The project is authorised FIRST (400 / 403).

Source: [backend/app/routers/search.py:901](../../../backend/app/routers/search.py#L901).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`.

Declared Python handler arguments (includes exact role/guard options):

```python
project_id: str=Query(..., description='The ONE project the suggestions are for'), db: AsyncSession=Depends(get_db), current_user: User=Depends(get_current_active_user)
```

Direct handler error branches (dependency/service errors can add others):

```python
HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='Invalid project ID')
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "suggested_searches_api_v1_search_suggested_get",
  "parameters": [
    {
      "description": "The ONE project the suggestions are for",
      "in": "query",
      "name": "project_id",
      "required": true,
      "schema": {
        "description": "The ONE project the suggestions are for",
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
            "$ref": "#/components/schemas/SearchSuggestionsResponse"
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

## GET `/api/v1/search/usage`

Search Usage

Searches, the zero-result rate and the latency percentiles of recorded searches.

QA lead and up. The project is authorised FIRST; without one a QA lead
sees the projects they belong to and an ADMIN the whole deployment. No
search in the window answers ``measured: false`` (never ``0 %``).

Source: [backend/app/routers/search.py:922](../../../backend/app/routers/search.py#L922).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_role.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
project_id: Optional[str]=Query(None), days: int=Query(1, description='1 (today, UTC), 7 or 30'), db: AsyncSession=Depends(get_db), current_user: User=Depends(require_role(UserRole.QA_LEAD))
```

Direct handler error branches (dependency/service errors can add others):

```python
HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=USAGE_DAYS_SENTENCE)
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "search_usage_api_v1_search_usage_get",
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
      "description": "1 (today, UTC), 7 or 30",
      "in": "query",
      "name": "days",
      "required": false,
      "schema": {
        "default": 1,
        "description": "1 (today, UTC), 7 or 30",
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
            "$ref": "#/components/schemas/SearchUsageRead"
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
