# Observability API

[Documentation home](../../README.md) · [Regeneration](../../maintainers/maintenance.md)

Generated from tracked source by `scripts/generate_reference.py`. Do not edit by hand. What the checks cover is recorded in [verification](../../maintainers/verification.md).

## POST `/api/v1/observability/frontend`

Receive frontend telemetry batch

Accepts and logs a batch of frontend errors and Web Vitals.
Returns 202 for an accepted batch — the browser fires-and-forgets this call.
A batch with more than 50 errors or 50 vitals is refused with 422.

Source: [backend/app/routers/observability.py:130](../../../backend/app/routers/observability.py#L130).

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
