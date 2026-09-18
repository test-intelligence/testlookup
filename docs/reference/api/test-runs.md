# Test Runs API

[Documentation home](../../README.md) · [Regeneration](../../handoff/maintenance.md)

Generated from tracked source by `scripts/generate_handoff_reference.py`. Do not edit by hand. The baseline and validation limits are recorded in [verification](../../handoff/verification.md).

## GET `/api/v1/runs`

List Runs



Source: [backend/app/routers/runs.py:62](../../../backend/app/routers/runs.py#L62).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`.

Declared Python handler arguments (includes exact role/guard options):

```python
project_id: str | None=None, page: int=Query(1, ge=1), size: int=Query(20, ge=1, le=500), status: str | None=None, release_id: str | None=None, days: int | None=Query(30, ge=0, le=365, description='Show runs from last N days (0 = all time)'), suite_name: str | None=Query(None, min_length=1, description='Filter runs by suite name, case-insensitive'), db: AsyncSession=Depends(get_db), current_user: User=Depends(get_current_active_user)
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "list_runs_api_v1_runs_get",
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
        "maximum": 500,
        "minimum": 1,
        "title": "Size",
        "type": "integer"
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
      "name": "release_id",
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
        "title": "Release Id"
      }
    },
    {
      "description": "Show runs from last N days (0 = all time)",
      "in": "query",
      "name": "days",
      "required": false,
      "schema": {
        "anyOf": [
          {
            "maximum": 365,
            "minimum": 0,
            "type": "integer"
          },
          {
            "type": "null"
          }
        ],
        "default": 30,
        "description": "Show runs from last N days (0 = all time)",
        "title": "Days"
      }
    },
    {
      "description": "Filter runs by suite name, case-insensitive",
      "in": "query",
      "name": "suite_name",
      "required": false,
      "schema": {
        "anyOf": [
          {
            "minLength": 1,
            "type": "string"
          },
          {
            "type": "null"
          }
        ],
        "description": "Filter runs by suite name, case-insensitive",
        "title": "Suite Name"
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
{'items': [], 'total': 0, 'page': page, 'size': size, 'pages': 0}
{'items': items, 'total': total, 'page': page, 'size': size, 'pages': pages}
```

Contract limit: at least one response has an unstructured schema. Read the linked handler/serializer for emitted fields; the empty schema is not a promise of an empty JSON object.

## GET `/api/v1/runs/failed-ids`

List Failed Run Ids

Return just the IDs of FAILED runs in the project + day window.

Backs the "Trigger all FAILED across pages" shortcut on /runs. Returning
only IDs (not the full row) keeps the payload tiny so the UI can fan out
pipeline triggers in parallel without an oversized round-trip.

With ``only_pending=true``, runs that already have an AgentPipelineRun
in 'running' status, or any pipeline created in the last 2h, are
excluded — matching the Celery task's dedup window so the user doesn't
waste a click re-firing what's already in flight.

Source: [backend/app/routers/runs.py:125](../../../backend/app/routers/runs.py#L125).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`.

Declared Python handler arguments (includes exact role/guard options):

```python
project_id: str | None=None, days: int | None=Query(30, ge=0, le=365, description='Look back window in days (0 = all time)'), limit: int=Query(1000, ge=1, le=5000, description='Hard cap to prevent runaway fan-outs'), only_pending: bool=Query(False, description='When true, exclude runs that already have an active or recent agent pipeline (within the last 2h, matching the Celery dedup TTL)'), suite_name: str | None=Query(None, min_length=1, description='Filter failed runs by suite name, case-insensitive'), db: AsyncSession=Depends(get_db), current_user: User=Depends(get_current_active_user)
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "list_failed_run_ids_api_v1_runs_failed_ids_get",
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
      "description": "Look back window in days (0 = all time)",
      "in": "query",
      "name": "days",
      "required": false,
      "schema": {
        "anyOf": [
          {
            "maximum": 365,
            "minimum": 0,
            "type": "integer"
          },
          {
            "type": "null"
          }
        ],
        "default": 30,
        "description": "Look back window in days (0 = all time)",
        "title": "Days"
      }
    },
    {
      "description": "Hard cap to prevent runaway fan-outs",
      "in": "query",
      "name": "limit",
      "required": false,
      "schema": {
        "default": 1000,
        "description": "Hard cap to prevent runaway fan-outs",
        "maximum": 5000,
        "minimum": 1,
        "title": "Limit",
        "type": "integer"
      }
    },
    {
      "description": "When true, exclude runs that already have an active or recent agent pipeline (within the last 2h, matching the Celery dedup TTL)",
      "in": "query",
      "name": "only_pending",
      "required": false,
      "schema": {
        "default": false,
        "description": "When true, exclude runs that already have an active or recent agent pipeline (within the last 2h, matching the Celery dedup TTL)",
        "title": "Only Pending",
        "type": "boolean"
      }
    },
    {
      "description": "Filter failed runs by suite name, case-insensitive",
      "in": "query",
      "name": "suite_name",
      "required": false,
      "schema": {
        "anyOf": [
          {
            "minLength": 1,
            "type": "string"
          },
          {
            "type": "null"
          }
        ],
        "description": "Filter failed runs by suite name, case-insensitive",
        "title": "Suite Name"
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
{'ids': [], 'count': 0, 'truncated': False}
{'ids': rows[:limit], 'count': len(rows[:limit]), 'truncated': truncated}
```

Contract limit: at least one response has an unstructured schema. Read the linked handler/serializer for emitted fields; the empty schema is not a promise of an empty JSON object.

## DELETE `/api/v1/runs/{run_id}`

Delete Run

Delete one run across all five stores. Irreversible.

**202, not 204.** A synchronous delete of a multi-GB prefix times out at
the gateway, and the Mongo -> MinIO -> Postgres ordering means the caller
would get a 504 with the artifacts already gone and the run still listed.
The work runs as a Celery job; poll it at
``GET /api/v1/projects/{project_id}/deletion/jobs/{job_id}``.

Four guards, each a real hazard rather than defensive habit:

* **In flight.** ``IN_PROGRESS`` is refused — the drainer and the persist
  task are writing to this run right now.
* **Resurrection.** A tombstone is written in the same transaction as the
  row deletion, because five code paths re-create a ``TestRun`` from a
  caller-supplied id on a SELECT miss. Refusing ``IN_PROGRESS`` narrows
  that window; it does not close it.
* **Citations.** A run cited by a compliance pack, linked to a release, or
  named by a decision report is refused — deleting it strands evidence
  whose subject no longer exists.
* **Prefix scope.** ``minio_prefix`` is uploader-derived and unvalidated
  beyond non-empty. Prefixes outside the project's scope are refused and
  REPORTED, never deleted.

Source: [backend/app/routers/runs.py:745](../../../backend/app/routers/runs.py#L745).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_role.<locals>._check`, `require_run_access.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
run_id: uuid.UUID, body: DeleteRunRequest, db: AsyncSession=Depends(get_db), current_user: User=Depends(require_role(UserRole.ADMIN, allow_project_key=True)), _: User=Depends(require_run_access())
```

Direct handler error branches (dependency/service errors can add others):

```python
HTTPException(status_code=404, detail='Test run not found')
HTTPException(status_code=409, detail='Run is still executing. Deleting it now races the drainer and the persist task, which would re-create the row with none of its data. Stop the run first.')
HTTPException(status_code=409, detail={'run_id': str(run_id), 'blockers': blockers})
HTTPException(status_code=422, detail='confirm must be true — this deletes across five stores and cannot be undone')
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "delete_run_api_v1_runs__run_id__delete",
  "parameters": [
    {
      "in": "path",
      "name": "run_id",
      "required": true,
      "schema": {
        "format": "uuid",
        "title": "Run Id",
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
          "$ref": "#/components/schemas/DeleteRunRequest"
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
            "$ref": "#/components/schemas/DeleteRunAcceptedResponse"
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

## GET `/api/v1/runs/{run_id}`

Get Run



Source: [backend/app/routers/runs.py:238](../../../backend/app/routers/runs.py#L238).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_run_access.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
run_id: uuid.UUID, db: AsyncSession=Depends(get_db), _: User=Depends(require_run_access())
```

Direct handler error branches (dependency/service errors can add others):

```python
HTTPException(status_code=404, detail='Test run not found')
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "get_run_api_v1_runs__run_id__get",
  "parameters": [
    {
      "in": "path",
      "name": "run_id",
      "required": true,
      "schema": {
        "format": "uuid",
        "title": "Run Id",
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
run
```

Contract limit: at least one response has an unstructured schema. Read the linked handler/serializer for emitted fields; the empty schema is not a promise of an empty JSON object.

## GET `/api/v1/runs/{run_id}/attribution`

Run Attribution

Per-failure verdicts for this run: yours, flaky, infrastructure, or unknown.

At Google roughly **84% of pass→fail transitions involve a flaky test**, so a
raw "new failure" list is mostly noise and trains engineers to dismiss the
real ones. This composes five signals — the transition against the previous
run, the flakiness score, systemic co-failure cluster membership, overlap
with the commit range's changed files, and this project's measured
classifier calibration — into one verdict per failing test.

Every verdict carries **all five inputs and the votes behind it**, because
when a verdict disagrees with an engineer the useful question is *which
input was wrong*, and that is unanswerable from a bare label.

``UNCERTAIN`` is a first-class answer, returned whenever the signals
disagree or are too thin — not a failure to decide.

**Advisory only.** Nothing here suppresses, hides or auto-closes a failure:
a newly-flaky test reflects a real bug often enough that suppression is the
one irreversible mistake available.

Source: [backend/app/routers/runs.py:276](../../../backend/app/routers/runs.py#L276).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_run_access.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
run_id: uuid.UUID, db: AsyncSession=Depends(get_db), _: User=Depends(require_run_access())
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "run_attribution_api_v1_runs__run_id__attribution_get",
  "parameters": [
    {
      "in": "path",
      "name": "run_id",
      "required": true,
      "schema": {
        "format": "uuid",
        "title": "Run Id",
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
{'items': [{'test_case_id': str(case.id), 'test_name': case.test_name, 'suite_name': case.suite_name, **attribution.to_dict()} for case, attribution in results], 'total': len(results)}
```

Contract limit: at least one response has an unstructured schema. Read the linked handler/serializer for emitted fields; the empty schema is not a promise of an empty JSON object.

## GET `/api/v1/runs/{run_id}/downstream-status`

Get Run Downstream Status

Expose durable publication state for required post-ingestion work.

Source: [backend/app/routers/runs.py:250](../../../backend/app/routers/runs.py#L250).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_run_access.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
run_id: uuid.UUID, db: AsyncSession=Depends(get_db), _: User=Depends(require_run_access())
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "get_run_downstream_status_api_v1_runs__run_id__downstream_status_get",
  "parameters": [
    {
      "in": "path",
      "name": "run_id",
      "required": true,
      "schema": {
        "format": "uuid",
        "title": "Run Id",
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
await downstream_status_for_run(db, run_id=run_id)
```

Contract limit: at least one response has an unstructured schema. Read the linked handler/serializer for emitted fields; the empty schema is not a promise of an empty JSON object.

## POST `/api/v1/runs/{run_id}/recover-live`

Recover Live Run From Buffer

Re-enqueue ``persist_live_session`` for a live-stream run whose
per-test rows never landed in PostgreSQL.

Looks up the run, checks that it's a ``live_stream`` run with zero
``test_cases`` rows, then fires the persistence task with the buffered
events still sitting in Redis (TTL 25h). Idempotent — the task itself
re-checks whether work is already done before inserting.

Returns 422 when the run isn't recoverable (already populated / not a
live run / no buffer left in Redis).

Source: [backend/app/routers/runs.py:580](../../../backend/app/routers/runs.py#L580).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_run_access.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
run_id: uuid.UUID, db: AsyncSession=Depends(get_db), _: User=Depends(require_run_access())
```

Direct handler error branches (dependency/service errors can add others):

```python
HTTPException(status_code=404, detail='Test run not found')
HTTPException(status_code=422, detail='No buffered events found and no aggregates to synthesise from. Re-run the suite, or re-ingest the results as a file upload.')
HTTPException(status_code=422, detail='Recovery is only available for live-stream runs.')
HTTPException(status_code=422, detail=f'Run already has {tc_count} test case rows — nothing to recover.')
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "recover_live_run_from_buffer_api_v1_runs__run_id__recover_live_post",
  "parameters": [
    {
      "in": "path",
      "name": "run_id",
      "required": true,
      "schema": {
        "format": "uuid",
        "title": "Run Id",
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
{'queued': True, 'run_id': str(run_id), 'buffered_events': buffer_len, 'buffered_batches': batch_count, 'source': source, 'message': message}
```

Contract limit: at least one response has an unstructured schema. Read the linked handler/serializer for emitted fields; the empty schema is not a promise of an empty JSON object.

## GET `/api/v1/runs/{run_id}/regression-diff`

Get Regression Diff

Return a "What changed since last good run?" diff for the given test run.
P3-9: Business logic extracted to regression_diff_service.

In-flight live runs (no TestRun row yet — only a LiveSession) get a
graceful in-progress payload instead of a 404. The frontend renders
"Diff will be available once the run completes" rather than a
broken error toast. (Bug 2026-05-19 — Run Detail page hit 404s for
sessions clicked during the first ~30s before the drainer
materialised the TestRun row.)

Source: [backend/app/routers/runs.py:459](../../../backend/app/routers/runs.py#L459).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_run_access.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
run_id: uuid.UUID, db: AsyncSession=Depends(get_db), _: Any=Depends(require_run_access())
```

Direct handler error branches (dependency/service errors can add others):

```python
HTTPException(status_code=404, detail='Test run not found')
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "get_regression_diff_api_v1_runs__run_id__regression_diff_get",
  "parameters": [
    {
      "in": "path",
      "name": "run_id",
      "required": true,
      "schema": {
        "format": "uuid",
        "title": "Run Id",
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
await compute_regression_diff(run, db)
{'run_id': str(run_id), 'status': 'in_progress', 'diff_available': False, 'reason': 'live_run_in_progress', 'message': 'This run is still streaming. The regression diff becomes available once the run finalises.', 'added': [], 'removed': [], 'flipped_to_failing': [], 'flipped_to_passing': []}
```

Contract limit: at least one response has an unstructured schema. Read the linked handler/serializer for emitted fields; the empty schema is not a promise of an empty JSON object.

## POST `/api/v1/runs/{run_id}/release`

Set Run Release



Source: [backend/app/routers/runs.py:505](../../../backend/app/routers/runs.py#L505).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_role.<locals>._check`, `require_run_access.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
run_id: uuid.UUID, body: dict, db: AsyncSession=Depends(get_db), current_user: User=Depends(require_role(UserRole.QA_LEAD, allow_project_key=True)), _: User=Depends(require_run_access())
```

Direct handler error branches (dependency/service errors can add others):

```python
HTTPException(status_code=404, detail='Test run not found')
HTTPException(status_code=422, detail='release_name is required')
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "set_run_release_api_v1_runs__run_id__release_post",
  "parameters": [
    {
      "in": "path",
      "name": "run_id",
      "required": true,
      "schema": {
        "format": "uuid",
        "title": "Run Id",
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
          "additionalProperties": true,
          "title": "Body",
          "type": "object"
        }
      }
    },
    "required": true
  },
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
{'release_id': str(release.id), 'release_name': release.name, 'release_status': release.status, 'auto_created': created}
```

Contract limit: at least one response has an unstructured schema. Read the linked handler/serializer for emitted fields; the empty schema is not a promise of an empty JSON object.

## GET `/api/v1/runs/{run_id}/step-flips`

Get Run Step Flips

Run-level roll-up of cross-run step-flip (FLK-P6 slice 5).

READ-ONLY. Aggregates the per-test cross-run step-flip across the run's
fingerprint-anchored tests so a QA engineer triaging a whole run sees WHICH
TESTS have a flickering step — without opening each one. Resolves the run's
project via the PROVIDED ``run_id`` (verified by ``require_run_access`` — IDOR
ratchet); the underlying read is project-scoped. No DB writes. Returns 404
when ``run_id`` has no run; otherwise a roll-up listing only the tests with at
least one step flip (``truncated`` flags a run larger than the analysis cap).

Source: [backend/app/routers/runs.py:435](../../../backend/app/routers/runs.py#L435).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_run_access.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
run_id: uuid.UUID, db: AsyncSession=Depends(get_db), _: User=Depends(require_run_access())
```

Direct handler error branches (dependency/service errors can add others):

```python
HTTPException(status_code=404, detail='Test run not found')
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "get_run_step_flips_api_v1_runs__run_id__step_flips_get",
  "parameters": [
    {
      "in": "path",
      "name": "run_id",
      "required": true,
      "schema": {
        "format": "uuid",
        "title": "Run Id",
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
payload
```

Contract limit: at least one response has an unstructured schema. Read the linked handler/serializer for emitted fields; the empty schema is not a promise of an empty JSON object.

## GET `/api/v1/runs/{run_id}/tests`

List Test Cases



Source: [backend/app/routers/runs.py:262](../../../backend/app/routers/runs.py#L262).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_run_access.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
run_id: uuid.UUID, page: int=Query(1, ge=1), size: int=Query(50, ge=1, le=200), status: str | None=None, suite: str | None=None, db: AsyncSession=Depends(get_db), _: User=Depends(require_run_access())
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "list_test_cases_api_v1_runs__run_id__tests_get",
  "parameters": [
    {
      "in": "path",
      "name": "run_id",
      "required": true,
      "schema": {
        "format": "uuid",
        "title": "Run Id",
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
        "default": 50,
        "maximum": 200,
        "minimum": 1,
        "title": "Size",
        "type": "integer"
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
      "name": "suite",
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
        "title": "Suite"
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
            "$ref": "#/components/schemas/TestCaseListResponse"
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

## GET `/api/v1/runs/{run_id}/tests/{test_id}`

Get Test Case



Source: [backend/app/routers/runs.py:319](../../../backend/app/routers/runs.py#L319).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_run_access.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
run_id: uuid.UUID, test_id: uuid.UUID, db: AsyncSession=Depends(get_db), _: User=Depends(require_run_access())
```

Direct handler error branches (dependency/service errors can add others):

```python
HTTPException(status_code=404, detail='Test case not found')
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "get_test_case_api_v1_runs__run_id__tests__test_id__get",
  "parameters": [
    {
      "in": "path",
      "name": "run_id",
      "required": true,
      "schema": {
        "format": "uuid",
        "title": "Run Id",
        "type": "string"
      }
    },
    {
      "in": "path",
      "name": "test_id",
      "required": true,
      "schema": {
        "format": "uuid",
        "title": "Test Id",
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
test_case
```

Contract limit: at least one response has an unstructured schema. Read the linked handler/serializer for emitted fields; the empty schema is not a promise of an empty JSON object.

## GET `/api/v1/runs/{run_id}/tests/{test_id}/history`

Get Test Case History Endpoint

Cross-run history + flakiness + metadata for one logical test (Phase 2).

READ-ONLY. Resolves the test's ``test_fingerprint`` + project via the
PROVIDED ``run_id`` (verified by ``require_run_access`` — IDOR ratchet), then
returns a project-scoped timeline (``test_fingerprint`` is not salted, so the
history query JOINs ``test_runs`` on ``project_id`` — no cross-project
leakage), the computed flakiness value (reusing
``analytics_service``/``test_health_coach`` thresholds), and identity
metadata (owner / first-last seen / suite / timestamps). No DB writes.

Source: [backend/app/routers/runs.py:384](../../../backend/app/routers/runs.py#L384).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_run_access.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
run_id: uuid.UUID, test_id: uuid.UUID, db: AsyncSession=Depends(get_db), _: User=Depends(require_run_access())
```

Direct handler error branches (dependency/service errors can add others):

```python
HTTPException(status_code=404, detail='Test case not found')
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "get_test_case_history_endpoint_api_v1_runs__run_id__tests__test_id__history_get",
  "parameters": [
    {
      "in": "path",
      "name": "run_id",
      "required": true,
      "schema": {
        "format": "uuid",
        "title": "Run Id",
        "type": "string"
      }
    },
    {
      "in": "path",
      "name": "test_id",
      "required": true,
      "schema": {
        "format": "uuid",
        "title": "Test Id",
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
            "$ref": "#/components/schemas/TestCaseHistoryResponse"
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

## GET `/api/v1/runs/{run_id}/tests/{test_id}/rich-detail`

Get Enriched Test Case Detail Endpoint

Versioned additive detail contract with optional recursive steps.

Access is checked against the supplied run before the service query. The
endpoint is separate from the legacy flat detail route so existing clients
retain their response shape during gradual rollout.

Source: [backend/app/routers/runs.py:360](../../../backend/app/routers/runs.py#L360).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_run_access.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
run_id: uuid.UUID, test_id: uuid.UUID, db: AsyncSession=Depends(get_db), _: User=Depends(require_run_access())
```

Direct handler error branches (dependency/service errors can add others):

```python
HTTPException(status_code=404, detail='Test case not found')
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "get_enriched_test_case_detail_endpoint_api_v1_runs__run_id__tests__test_id__rich_detail_get",
  "parameters": [
    {
      "in": "path",
      "name": "run_id",
      "required": true,
      "schema": {
        "format": "uuid",
        "title": "Run Id",
        "type": "string"
      }
    },
    {
      "in": "path",
      "name": "test_id",
      "required": true,
      "schema": {
        "format": "uuid",
        "title": "Test Id",
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
            "$ref": "#/components/schemas/EnrichedTestCaseDetailResponse"
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

## GET `/api/v1/runs/{run_id}/tests/{test_id}/step-flips`

Get Test Case Step Flips

Cross-run step-flip report for one logical test (FLK-P6 slice 4).

READ-ONLY. Unlike ``/steps`` (a LATEST-RUN-ONLY snapshot), this reads the
retained per-run step outcomes (``test_step_runs``) and reports WHICH step
oscillated PASSED<->FAILED across runs, how often, and in which direction —
so a flickering step reads as step-level flakiness rather than a whole-test
verdict. Resolves the test's ``test_fingerprint`` + project via the PROVIDED
``run_id`` (verified by ``require_run_access`` — IDOR ratchet); the underlying
read is project-scoped. No DB writes. Returns 404 when ``test_id`` doesn't
belong to ``run_id``; an empty-window "insufficient history" report otherwise.

Source: [backend/app/routers/runs.py:409](../../../backend/app/routers/runs.py#L409).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_run_access.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
run_id: uuid.UUID, test_id: uuid.UUID, db: AsyncSession=Depends(get_db), _: User=Depends(require_run_access())
```

Direct handler error branches (dependency/service errors can add others):

```python
HTTPException(status_code=404, detail='Test case not found')
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "get_test_case_step_flips_api_v1_runs__run_id__tests__test_id__step_flips_get",
  "parameters": [
    {
      "in": "path",
      "name": "run_id",
      "required": true,
      "schema": {
        "format": "uuid",
        "title": "Run Id",
        "type": "string"
      }
    },
    {
      "in": "path",
      "name": "test_id",
      "required": true,
      "schema": {
        "format": "uuid",
        "title": "Test Id",
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
payload
```

Contract limit: at least one response has an unstructured schema. Read the linked handler/serializer for emitted fields; the empty schema is not a promise of an empty JSON object.

## GET `/api/v1/runs/{run_id}/tests/{test_id}/steps`

Get Test Case Steps

Granular step/attachment tree for one test (LATEST-RUN-ONLY snapshot).

Lazy / separate from the test-case detail payload — the detail endpoint
stays unchanged and clients fetch steps on demand. Guarded by
``require_run_access`` which verifies the PROVIDED ``run_id`` (IDOR ratchet).
Returns the ordered, nested step tree with per-step + test-level attachments.

Source: [backend/app/routers/runs.py:338](../../../backend/app/routers/runs.py#L338).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_run_access.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
run_id: uuid.UUID, test_id: uuid.UUID, db: AsyncSession=Depends(get_db), _: User=Depends(require_run_access())
```

Direct handler error branches (dependency/service errors can add others):

```python
HTTPException(status_code=404, detail='Test case not found')
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "get_test_case_steps_api_v1_runs__run_id__tests__test_id__steps_get",
  "parameters": [
    {
      "in": "path",
      "name": "run_id",
      "required": true,
      "schema": {
        "format": "uuid",
        "title": "Run Id",
        "type": "string"
      }
    },
    {
      "in": "path",
      "name": "test_id",
      "required": true,
      "schema": {
        "format": "uuid",
        "title": "Test Id",
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
tree
```

Contract limit: at least one response has an unstructured schema. Read the linked handler/serializer for emitted fields; the empty schema is not a promise of an empty JSON object.
