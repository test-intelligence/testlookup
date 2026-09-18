# Observability API

[Documentation home](../../README.md) · [Regeneration](../../handoff/maintenance.md)

Generated from tracked source by `scripts/generate_handoff_reference.py`. Do not edit by hand. The baseline and validation limits are recorded in [verification](../../handoff/verification.md).

## POST `/api/v1/observability/frontend`

Receive frontend telemetry batch

Accepts and logs a batch of frontend errors and Web Vitals.
Always returns 202 — the browser fires-and-forgets this call.

Source: [backend/app/routers/observability.py:63](../../../backend/app/routers/observability.py#L63).

Dependency chain: .

Declared Python handler arguments (includes exact role/guard options):

```python
payload: FrontendTelemetryBatch, request: Request
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "receive_frontend_telemetry_api_v1_observability_frontend_post",
  "requestBody": {
    "content": {
      "application/json": {
        "schema": {
          "$ref": "#/components/schemas/FrontendTelemetryBatch"
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
{'received': {'errors': len(payload.errors), 'vitals': len(payload.vitals)}}
```

Contract limit: at least one response has an unstructured schema. Read the linked handler/serializer for emitted fields; the empty schema is not a promise of an empty JSON object.
