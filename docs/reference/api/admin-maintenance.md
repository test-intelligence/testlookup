# Admin Maintenance API

[Documentation home](../../README.md) · [Regeneration](../../handoff/maintenance.md)

Generated from tracked source by `scripts/generate_handoff_reference.py`. Do not edit by hand. The baseline and validation limits are recorded in [verification](../../handoff/verification.md).

## GET `/api/v1/admin/maintenance/ai-cache`

Read Ai Cache Stats

The AI similarity cache's health, and what the pre-M15 shared collection holds.

Re-audit M15, code review: ``legacy_unscoped_documents`` -- entries written
to the old collection every tenant shared, which may belong to any tenant --
was computed by ``get_semantic_cache_stats`` and returned by nothing, so
the signal to purge that collection could not be seen. Instance admins
only: the counts span tenants. A cache that cannot be read is a 503, never
a page of zeros.

Source: [backend/app/routers/admin_maintenance.py:223](../../../backend/app/routers/admin_maintenance.py#L223).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_role.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
current_user: User=Depends(get_current_active_user)
```

Direct handler error branches (dependency/service errors can add others):

```python
HTTPException(status_code=503, detail={'message': 'The AI similarity cache could not be read. That is not the same as an empty cache.', 'hint': 'ChromaDB is optional. If this deployment does not run it, there is no AI similarity cache and nothing to purge.', **stats})
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "read_ai_cache_stats_api_v1_admin_maintenance_ai_cache_get",
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
stats
```

Contract limit: at least one response has an unstructured schema. Read the linked handler/serializer for emitted fields; the empty schema is not a promise of an empty JSON object.

## POST `/api/v1/admin/maintenance/backfill-placeholder-test-cases`

Trigger Placeholder Backfill

Fire ``worker.tasks.backfill_placeholder_test_cases`` immediately.

Synthesizes placeholder ``TestCase`` rows for every historical
``TestRun`` where ``failed_tests + broken_tests > 0`` but no
``test_cases`` exist (the Phase 4.5 live-stream buffer-eviction
scenario). Beat schedule fires this hourly at :10 — use this
endpoint right after a deploy if you don't want to wait.

Idempotent — subsequent calls produce zero rows once the first run
has filled the gaps. Returns the Celery task id so the caller can
correlate with worker logs.

Source: [backend/app/routers/admin_maintenance.py:35](../../../backend/app/routers/admin_maintenance.py#L35).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_role.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
max_runs_per_project: int=500, current_user: User=Depends(get_current_active_user)
```

Direct handler error branches (dependency/service errors can add others):

```python
HTTPException(status_code=500, detail=f'Failed to queue task: {exc}')
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "trigger_placeholder_backfill_api_v1_admin_maintenance_backfill_placeholder_test_cases_post",
  "parameters": [
    {
      "in": "query",
      "name": "max_runs_per_project",
      "required": false,
      "schema": {
        "default": 500,
        "title": "Max Runs Per Project",
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
{'queued': True, 'task_id': result.id, 'task_name': 'backfill_placeholder_test_cases', 'actor_user_id': str(current_user.id), 'note': 'Synthesizing placeholder rows for runs whose live-stream buffer was evicted before persistence. Re-run ``backfill-unassigned-failures`` (also under this prefix) once this finishes to assign the new rows to QA Leads.'}
```

Contract limit: at least one response has an unstructured schema. Read the linked handler/serializer for emitted fields; the empty schema is not a promise of an empty JSON object.

## POST `/api/v1/admin/maintenance/backfill-unassigned-failures`

Trigger Unassigned Failures Backfill

Fire ``worker.tasks.backfill_unassigned_failures`` immediately.

Pass 1 provisions a default QA-lead user on every project missing
one (see ``default_qa_lead_service``). Pass 2 walks recent
FAILED/BROKEN ``TestCase`` rows whose ``assigned_to_user_id`` is
NULL and runs the suite-owner resolver across them. The result is
that ``/my-failures`` populates without having to wait for the
next 15-minute beat tick.

Idempotent — only writes to NULL rows, only provisions missing
leads, never over-writes a manual reassignment.

Source: [backend/app/routers/admin_maintenance.py:87](../../../backend/app/routers/admin_maintenance.py#L87).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_role.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
max_runs_per_project: int=200, current_user: User=Depends(get_current_active_user)
```

Direct handler error branches (dependency/service errors can add others):

```python
HTTPException(status_code=500, detail=f'Failed to queue task: {exc}')
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "trigger_unassigned_failures_backfill_api_v1_admin_maintenance_backfill_unassigned_failures_post",
  "parameters": [
    {
      "in": "query",
      "name": "max_runs_per_project",
      "required": false,
      "schema": {
        "default": 200,
        "title": "Max Runs Per Project",
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
{'queued': True, 'task_id': result.id, 'task_name': 'backfill_unassigned_failures', 'actor_user_id': str(current_user.id)}
```

Contract limit: at least one response has an unstructured schema. Read the linked handler/serializer for emitted fields; the empty schema is not a promise of an empty JSON object.

## GET `/api/v1/admin/maintenance/dlq`

Read Dead Letters

Newest entries of both dead-letter stores (re-audit M2).

Both stores were write-only: ``/health/ingestion`` counted one and nothing
counted the other, so an operator could learn that work had permanently
failed, but never what. Instance admins only -- the entries are
cross-tenant (run and project ids, error text, sanitized event payloads).
A store that cannot be read is a 503, never an empty list.

Source: [backend/app/routers/admin_maintenance.py:169](../../../backend/app/routers/admin_maintenance.py#L169).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_role.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
limit: int=Query(50, ge=1, le=500), current_user: User=Depends(get_current_active_user)
```

Direct handler error branches (dependency/service errors can add others):

```python
HTTPException(status_code=503, detail='The dead-letter stores could not be read (Redis unavailable). That is not the same as having no failures.')
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "read_dead_letters_api_v1_admin_maintenance_dlq_get",
  "parameters": [
    {
      "in": "query",
      "name": "limit",
      "required": false,
      "schema": {
        "default": 50,
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
{'limit': limit, 'sources': {'persist_live_session': {'count': await get_dlq_count('persist_live_session'), 'entries': persist}, 'stream': {'count': await get_stream_dlq_count(), 'entries': stream}}}
```

Contract limit: at least one response has an unstructured schema. Read the linked handler/serializer for emitted fields; the empty schema is not a promise of an empty JSON object.

## POST `/api/v1/admin/maintenance/drain-active-live-sessions`

Trigger Drain Active Sessions

Fire ``worker.tasks.drain_active_live_sessions`` immediately.

Normally runs every 30s. Useful when an operator wants to confirm
the drain is functional after a deploy without waiting for the
next tick.

Source: [backend/app/routers/admin_maintenance.py:133](../../../backend/app/routers/admin_maintenance.py#L133).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_role.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
current_user: User=Depends(get_current_active_user)
```

Direct handler error branches (dependency/service errors can add others):

```python
HTTPException(status_code=500, detail=f'Failed to queue task: {exc}')
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "trigger_drain_active_sessions_api_v1_admin_maintenance_drain_active_live_sessions_post",
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
{'queued': True, 'task_id': result.id, 'task_name': 'drain_active_live_sessions', 'actor_user_id': str(current_user.id)}
```

Contract limit: at least one response has an unstructured schema. Read the linked handler/serializer for emitted fields; the empty schema is not a promise of an empty JSON object.

## POST `/api/v1/admin/maintenance/outbox/requeue`

Requeue Failed Outbox Operations

Put failed post-ingestion work back in the run outbox (re-audit N23).

A finished run's follow-up work -- notifications, the completion webhook,
suite comparisons, the AI pipeline -- is published from a durable outbox,
and an intent that keeps failing is marked ``failed`` and never retried.
Until N23 every ``agent_pipeline`` intent failed that way with
``broker_TypeError``, so the fix alone would not have analysed one of those
runs. List them with this call (it is a dry run unless ``dry_run`` is
false), then requeue them in slices of at most 500, oldest first. Each
requeued ``agent_pipeline`` intent starts one AI pipeline.

Requeue a stranded N27 intent only after its pipeline is marked failed: a
pipeline left ``running`` refuses the retry until the stale-pipeline reaper
(every 10 minutes, after 30) has failed it.

Source: [backend/app/routers/admin_maintenance.py:316](../../../backend/app/routers/admin_maintenance.py#L316).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_role.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
body: OutboxRequeueRequest, db: AsyncSession=Depends(get_db), current_user: User=Depends(get_current_active_user)
```

Direct handler error branches (dependency/service errors can add others):

```python
HTTPException(status_code=422, detail=str(exc))
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "requeue_failed_outbox_operations_api_v1_admin_maintenance_outbox_requeue_post",
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
          "$ref": "#/components/schemas/OutboxRequeueRequest"
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
{'operation': body.operation, 'dry_run': body.dry_run, 'matched': len(rows), 'requeued': 0 if body.dry_run else len(rows), 'rows': rows}
```

Contract limit: at least one response has an unstructured schema. Read the linked handler/serializer for emitted fields; the empty schema is not a promise of an empty JSON object.
