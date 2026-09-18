# Sdk Downloads API

[Documentation home](../../README.md) · [Regeneration](../../handoff/maintenance.md)

Generated from tracked source by `scripts/generate_handoff_reference.py`. Do not edit by hand. The baseline and validation limits are recorded in [verification](../../handoff/verification.md).

## GET `/api/v1/sdk`

List available client SDKs

Return metadata for all available SDK downloads.

Source: [backend/app/routers/sdk.py:91](../../../backend/app/routers/sdk.py#L91).

Dependency chain: .

Declared Python handler arguments (includes exact role/guard options):

```python

```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "list_sdks_api_v1_sdk_get",
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
JSONResponse(content={'sdks': available})
```

Contract limit: at least one response has an unstructured schema. Read the linked handler/serializer for emitted fields; the empty schema is not a promise of an empty JSON object.

## GET `/api/v1/sdk/{lang}`

Download a client SDK

Download the SDK for the specified language.

- **python** — returns a ZIP containing the reporter and its required sibling modules
- **java** — returns `testlookup-reporter-1.0.0-all.jar` (fat JAR) if built,
  otherwise falls back to a ZIP archive of the source directory
- **go / js** — returns a ZIP archive of the SDK directory

Source: [backend/app/routers/sdk.py:124](../../../backend/app/routers/sdk.py#L124).

Dependency chain: .

Declared Python handler arguments (includes exact role/guard options):

```python
lang: str
```

Direct handler error branches (dependency/service errors can add others):

```python
HTTPException(status_code=404, detail=f"Unknown SDK language '{lang}'. Available: {list(_SDK_CONFIGS)}")
HTTPException(status_code=503, detail="SDK files are not mounted in this environment. Add '- ./client:/app/client_sdks:ro' to the backend volumes in docker-compose.yml.")
HTTPException(status_code=503, detail='Python SDK files are incomplete in this environment.')
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "download_sdk_api_v1_sdk__lang__get",
  "parameters": [
    {
      "in": "path",
      "name": "lang",
      "required": true,
      "schema": {
        "title": "Lang",
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
FileResponse(path=jar_full, filename=cfg['filename'], media_type=cfg['media_type'], headers=_no_cache_headers)
Response(content=data, media_type='application/octet-stream', headers={'Content-Disposition': f'''attachment; filename="{cfg['fallback_filename']}"''', 'Content-Length': str(len(data)), **_no_cache_headers})
Response(content=data, media_type='application/octet-stream', headers={'Content-Disposition': f'''attachment; filename="{cfg['filename']}"''', 'Content-Length': str(len(data)), **_no_cache_headers})
```

Contract limit: at least one response has an unstructured schema. Read the linked handler/serializer for emitted fields; the empty schema is not a promise of an empty JSON object.
