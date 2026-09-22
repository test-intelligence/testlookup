# System API

[Documentation home](../../README.md) · [Regeneration](../../handoff/maintenance.md)

Generated from tracked source by `scripts/generate_handoff_reference.py`. Do not edit by hand. The baseline and validation limits are recorded in [verification](../../handoff/verification.md).

## GET `/`

Root



Source: [backend/app/main.py:327](../../../backend/app/main.py#L327).

Dependency chain: .

Declared Python handler arguments (includes exact role/guard options):

```python

```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "root__get",
  "responses": {
    "200": {
      "content": {
        "application/json": {
          "schema": {}
        }
      },
      "description": "Successful Response"
    }
  }
}
```

Handler return expressions (source excerpts, not an inferred wire schema):

```python
JSONResponse({'name': settings.APP_NAME, 'version': settings.APP_VERSION, 'api_docs': '/api-docs', 'user_docs': '/docs'})
```

Contract limit: at least one response has an unstructured schema. Read the linked handler/serializer for emitted fields; the empty schema is not a promise of an empty JSON object.
