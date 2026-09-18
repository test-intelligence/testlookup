# Ingest API

[Documentation home](../../README.md) · [Regeneration](../../handoff/maintenance.md)

Generated from tracked source by `scripts/generate_handoff_reference.py`. Do not edit by hand. The baseline and validation limits are recorded in [verification](../../handoff/verification.md).

## POST `/api/v1/ingest`

Ingest test results (JSON batch)

Accept a JSON batch of test results and queue for async processing.

The batch is dispatched to a Celery worker which creates the TestRun,
upserts test cases, runs post-ingestion tagging, and triggers the
AI analysis pipeline.

Source: [backend/app/routers/ingest.py:87](../../../backend/app/routers/ingest.py#L87).

Dependency chain: `OAuth2PasswordBearer`, `get_api_key_context`, `get_current_user_or_api_key`, `get_db`.

Declared Python handler arguments (includes exact role/guard options):

```python
payload: IngestPayload, db: AsyncSession=Depends(get_db), auth: tuple[User, None]=Depends(get_api_key_context)
```

Direct handler error branches (dependency/service errors can add others):

```python
HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail='This API key is restricted to a different project')
HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail='Upload storage is temporarily unavailable')
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "ingest_batch_api_v1_ingest_post",
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
          "$ref": "#/components/schemas/IngestPayload"
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
            "$ref": "#/components/schemas/IngestResponse"
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

## POST `/api/v1/ingest/file`

Upload test result file (JUnit/TestNG XML, Allure/Cypress/Playwright JSON)

Upload a test result file for async parsing and ingestion.

Supported formats: ``junit`` | ``testng`` | ``allure`` | ``cypress`` |
``playwright`` | ``pytest`` | ``robot`` | ``cucumber`` | ``nunit`` |
``trx`` | ``xunit``. Use
``format=auto`` (default) for content-based detection.
The Cypress and Playwright parsers are gated behind the ``cypress_ingest``
and ``playwright_ingest`` feature flags respectively — 503 is returned if
a disabled format is requested.

Source: [backend/app/routers/ingest.py:283](../../../backend/app/routers/ingest.py#L283).

Dependency chain: `OAuth2PasswordBearer`, `get_api_key_context`, `get_current_user_or_api_key`, `get_db`.

Declared Python handler arguments (includes exact role/guard options):

```python
file: UploadFile=File(...), project_id: str=Form(...), build_number: str=Form(..., min_length=1, max_length=BUILD_NUMBER_MAX_LENGTH), branch: str=Form(None, max_length=255), commit_hash: str=Form(None, max_length=64), release_name: str=Form(None, max_length=255), format: str=Form('auto'), run_ai: bool=Form(True), ci_provider: str=Form(None, max_length=30), ci_repo: str=Form(None, max_length=300), pr_number: int=Form(None, ge=1), ci_actor: str=Form(None, max_length=120), ci_run_url: str=Form(None, max_length=1000), jenkins_job: str=Form(None, max_length=500), environment: str=Form(None, max_length=100), executed_at: datetime=Form(None), commit_range: str=Form(None), db: AsyncSession=Depends(get_db), auth: tuple[User, None]=Depends(get_api_key_context)
```

Direct handler error branches (dependency/service errors can add others):

```python
HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Unsupported format '{format}'. Expected one of: " + ', '.join(sorted(_SUPPORTED_FORMATS)))
HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail='This API key is restricted to a different project')
HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail='Upload could not be queued')
HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail='Upload storage is temporarily unavailable')
HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=f"{detected_format.title()} ingestion is disabled. Ask an admin to enable the '{flag_key}' feature flag.")
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "ingest_file_api_v1_ingest_file_post",
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
      "multipart/form-data": {
        "schema": {
          "$ref": "#/components/schemas/Body_ingest_file_api_v1_ingest_file_post"
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
            "$ref": "#/components/schemas/IngestResponse"
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

## GET `/api/v1/ingest/uploads/{task_id}`

Poll the status of an uploaded report's async processing

Return the async parse/ingest status for an upload task.

Project-scoped: 404 if unknown/expired, 403 if the caller can't access the
run's project (so a leaked/guessed task_id can't reveal another tenant's
run).

Source: [backend/app/routers/ingest.py:555](../../../backend/app/routers/ingest.py#L555).

Dependency chain: `OAuth2PasswordBearer`, `get_api_key_context`, `get_current_user_or_api_key`, `get_db`.

Declared Python handler arguments (includes exact role/guard options):

```python
task_id: str, db: AsyncSession=Depends(get_db), auth: 'tuple[User, uuid.UUID | None]'=Depends(get_api_key_context)
```

Direct handler error branches (dependency/service errors can add others):

```python
HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail='This API key is restricted to a different project')
HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Upload task not found or expired')
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "get_upload_status_api_v1_ingest_uploads__task_id__get",
  "parameters": [
    {
      "in": "path",
      "name": "task_id",
      "required": true,
      "schema": {
        "title": "Task Id",
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
            "$ref": "#/components/schemas/UploadStatusResponse"
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
