# Sdk Downloads API

[Documentation home](../../README.md) · [Regeneration](../../maintainers/maintenance.md)

Generated from tracked source by `scripts/generate_reference.py`. Do not edit by hand. What the checks cover is recorded in [verification](../../maintainers/verification.md).

## GET `/api/v1/sdk`

List available client SDKs

Return metadata for all available SDK downloads.

Source: [backend/app/routers/sdk.py:122](../../../backend/app/routers/sdk.py#L122).

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

## GET `/api/v1/sdk/cli/info`

The CLI wheel this deployment serves

W17 DEV-02: the newest staged CLI wheel (name, version, download path), or
``available: false`` when this deployment was built without one (the guide
then shows the from-a-clone install).

Source: [backend/app/routers/sdk.py:165](../../../backend/app/routers/sdk.py#L165).

Dependency chain: .

Declared Python handler arguments (includes exact role/guard options):

```python

```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "cli_info_api_v1_sdk_cli_info_get",
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
JSONResponse(content={'available': False, 'version': None, 'filename': None, 'download_url': None})
JSONResponse(content={'available': True, 'version': version, 'filename': name, 'download_url': f'/api/v1/sdk/cli/{name}'})
```

Contract limit: at least one response has an unstructured schema. Read the linked handler/serializer for emitted fields; the empty schema is not a promise of an empty JSON object.

## GET `/api/v1/sdk/cli/{filename}`

Download the CLI wheel

W17 DEV-02: one staged CLI wheel, by its exact file name (pip needs it).

Only a name from the staged list is served: anything else -- another
package, a path, ``..`` -- is a 404, never a file read.

Source: [backend/app/routers/sdk.py:179](../../../backend/app/routers/sdk.py#L179).

Dependency chain: .

Declared Python handler arguments (includes exact role/guard options):

```python
filename: str
```

Direct handler error branches (dependency/service errors can add others):

```python
HTTPException(status_code=404, detail='No such CLI wheel on this deployment.')
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "download_cli_wheel_api_v1_sdk_cli__filename__get",
  "parameters": [
    {
      "in": "path",
      "name": "filename",
      "required": true,
      "schema": {
        "title": "Filename",
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
FileResponse(path=os.path.join(SDK_BASE_PATH, _SDK_CONFIGS['cli']['path'], filename), filename=filename, media_type=_SDK_CONFIGS['cli']['media_type'])
```

Contract limit: at least one response has an unstructured schema. Read the linked handler/serializer for emitted fields; the empty schema is not a promise of an empty JSON object.

## GET `/api/v1/sdk/{lang}`

Download a client SDK

Download the SDK for the specified language.

- **python** — returns a ZIP containing the reporter and its required sibling modules
- **java** — returns `testlookup-reporter-1.0.0-all.jar` (fat JAR) if built,
  otherwise falls back to a ZIP archive of the source directory
- **go / js** — returns a ZIP archive of the SDK directory

Source: [backend/app/routers/sdk.py:196](../../../backend/app/routers/sdk.py#L196).

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
HTTPException(status_code=503, detail='This deployment was built without the CLI wheel.')
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
await download_cli_wheel(wheels[0][0])
```

Contract limit: at least one response has an unstructured schema. Read the linked handler/serializer for emitted fields; the empty schema is not a promise of an empty JSON object.
