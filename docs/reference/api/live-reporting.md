# Live Reporting API

[Documentation home](../../README.md) · [Regeneration](../../handoff/maintenance.md)

Generated from tracked source by `scripts/generate_handoff_reference.py`. Do not edit by hand. The baseline and validation limits are recorded in [verification](../../handoff/verification.md).

## POST `/ws/events/{run_id}`

Ingest Live Event

Receive a live test execution event from a test runner.
This endpoint is a thin producer — it enqueues the event to Redis Streams
and returns 202 immediately (~1ms). The actual processing happens
asynchronously in the LiveEventStreamConsumer background task.

Supported event types:
  - run_start:    {type, project_id, build_number, total_tests}
  - test_result:  {type, test_name, status, duration_ms, error_message, test_case_id?}
  - run_complete: {type}

Any event may carry an ``event_id`` (1-200 printable characters, unique
within its run): a POST retried with the same one is counted once. Without
one, ``run_start`` and ``run_complete`` are recognised by their content, and
every ``test_result`` is a new result.

With a project key the run is saved like an SDK run (re-audit N14). Its run
id can be used again once the run completes: a ``run_start`` begins a new
run under it, and any other event for the finished run is refused with 409.
``test_case_id``, and a ``test_result``'s ``total_tests``, are honoured only
on the legacy path: the SDK stream's events carry neither.

Authentication (re-audit H1). Preferred: a project-scoped ``X-API-Key``
carrying the ``stream:write`` scope — the project is then derived from the
key server-side and a caller cannot name someone else's. Legacy: the shared
``X-Webhook-Secret``, which authenticates a caller but names no tenant, so
it could previously inject fabricated results into ANY project. Set
``LIVE_EVENTS_REQUIRE_PROJECT_KEY=true`` to refuse it outright.

Source: [backend/app/routers/live.py:510](../../../backend/app/routers/live.py#L510).

Dependency chain: `get_db`.

Declared Python handler arguments (includes exact role/guard options):

```python
run_id: str, event: dict=Body(...), x_api_key: Optional[str]=Header(None, alias='X-API-Key'), x_webhook_secret: Optional[str]=Header(None, alias='X-Webhook-Secret'), db: AsyncSession=Depends(get_db)
```

Direct handler error branches (dependency/service errors can add others):

```python
HTTPException(400, detail='project_id required for run_start event')
HTTPException(400, detail=str(exc))
HTTPException(status.HTTP_401_UNAUTHORIZED, detail='Live events require a project-scoped X-API-Key (or the legacy X-Webhook-Secret).')
HTTPException(status.HTTP_403_FORBIDDEN, detail='Invalid webhook secret')
HTTPException(status.HTTP_403_FORBIDDEN, detail='This deployment requires a project-scoped API key for live events. The shared webhook secret names no project.')
HTTPException(status.HTTP_403_FORBIDDEN, detail='This run belongs to a different project')
HTTPException(status.HTTP_409_CONFLICT, detail=f"run_id '{run_id}' is already in use by another project. Use a run_id unique to this project.")
HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail='Cannot establish run ownership right now — retry')
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "ingest_live_event_ws_events__run_id__post",
  "parameters": [
    {
      "in": "path",
      "name": "run_id",
      "required": true,
      "schema": {
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
    },
    {
      "in": "header",
      "name": "X-Webhook-Secret",
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
        "title": "X-Webhook-Secret"
      }
    }
  ],
  "requestBody": {
    "content": {
      "application/json": {
        "schema": {
          "additionalProperties": true,
          "title": "Event",
          "type": "object"
        }
      }
    },
    "required": true
  },
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
  }
}
```

Handler return expressions (source excerpts, not an inferred wire schema):

```python
{'accepted': True, 'run_id': run_id, 'event_type': event_type, 'session_id': outcome.session_id}
{'accepted': True, 'run_id': run_id, 'event_type': event_type}
```

Contract limit: at least one response has an unstructured schema. Read the linked handler/serializer for emitted fields; the empty schema is not a promise of an empty JSON object.
