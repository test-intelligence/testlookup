# Ingest API

[Documentation home](../../README.md) · [Regeneration](../../maintainers/maintenance.md)

Generated from tracked source by `scripts/generate_reference.py`. Do not edit by hand. What the checks cover is recorded in [verification](../../maintainers/verification.md).

## POST `/api/v1/ingest`

Ingest test results (JSON batch)

Accept a JSON batch of test results and queue for async processing.

The batch is dispatched to a Celery worker which creates the TestRun,
upserts test cases, runs post-ingestion tagging, and triggers the
AI analysis pipeline.

Source: [backend/app/routers/ingest.py:239](../../../backend/app/routers/ingest.py#L239).

Dependency chain: `OAuth2PasswordBearer`, `get_api_key_context`, `get_current_user_or_api_key`, `get_db`.

Declared Python handler arguments (includes exact role/guard options):

```python
payload: IngestPayload, idempotency_key: str | None=Header(None, alias='Idempotency-Key', max_length=128, pattern='^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$', description='Reuse for identical logical upload retries within receipt retention.'), db: AsyncSession=Depends(get_db), auth: tuple[User, None]=Depends(get_api_key_context)
```

Direct handler error branches (dependency/service errors can add others):

```python
HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail='This API key is restricted to a different project')
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "ingest_batch_api_v1_ingest_post",
  "parameters": [
    {
      "description": "Reuse for identical logical upload retries within receipt retention.",
      "in": "header",
      "name": "Idempotency-Key",
      "required": false,
      "schema": {
        "anyOf": [
          {
            "maxLength": 128,
            "pattern": "^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$",
            "type": "string"
          },
          {
            "type": "null"
          }
        ],
        "description": "Reuse for identical logical upload retries within receipt retention.",
        "title": "Idempotency-Key"
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
    "409": {
      "description": "The upload identity conflicts with existing content or a deleted run."
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
    },
    "503": {
      "description": "Upload admission or queue confirmation unavailable. If Location and X-Upload-Task-ID are present, processing may still start: poll the existing upload before resubmitting.",
      "headers": {
        "Location": {
          "description": "Existing upload status URL, when dispatch is unconfirmed",
          "schema": {
            "type": "string"
          }
        },
        "X-Upload-Run-ID": {
          "description": "Existing run identifier, when dispatch is unconfirmed",
          "schema": {
            "type": "string"
          }
        },
        "X-Upload-Task-ID": {
          "description": "Existing upload task identifier, when dispatch is unconfirmed",
          "schema": {
            "type": "string"
          }
        }
      }
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

Source: [backend/app/routers/ingest.py:494](../../../backend/app/routers/ingest.py#L494).

Dependency chain: `OAuth2PasswordBearer`, `get_api_key_context`, `get_current_user_or_api_key`, `get_db`.

Declared Python handler arguments (includes exact role/guard options):

```python
file: UploadFile=File(...), project_id: str=Form(...), build_number: str=Form(..., min_length=1, max_length=BUILD_NUMBER_MAX_LENGTH), branch: str=Form(None, max_length=255), commit_hash: str=Form(None, max_length=64), release_name: str=Form(None, max_length=255), format: str=Form('auto'), run_ai: bool=Form(True), ci_provider: str=Form(None, max_length=30), ci_repo: str=Form(None, max_length=300), pr_number: int=Form(None, ge=1), ci_actor: str=Form(None, max_length=120), ci_run_url: str=Form(None, max_length=1000), jenkins_job: str=Form(None, max_length=500), environment: str=Form(None, max_length=100), executed_at: datetime=Form(None), commit_range: str=Form(None), run_context: str=Form(None, max_length=16384), idempotency_key: str | None=Header(None, alias='Idempotency-Key', max_length=128, pattern='^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$', description='Reuse for identical logical upload retries within receipt retention.'), db: AsyncSession=Depends(get_db), auth: tuple[User, None]=Depends(get_api_key_context)
```

Direct handler error branches (dependency/service errors can add others):

```python
HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Unsupported format '{format}'. Expected one of: " + ', '.join(sorted(_SUPPORTED_FORMATS)))
HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail='This API key is restricted to a different project')
HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail='build_number must not be empty')
HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=exc.detail())
HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=f"{detected_format.title()} ingestion is disabled. Ask an admin to enable the '{flag_key}' feature flag.")
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "ingest_file_api_v1_ingest_file_post",
  "parameters": [
    {
      "description": "Reuse for identical logical upload retries within receipt retention.",
      "in": "header",
      "name": "Idempotency-Key",
      "required": false,
      "schema": {
        "anyOf": [
          {
            "maxLength": 128,
            "pattern": "^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$",
            "type": "string"
          },
          {
            "type": "null"
          }
        ],
        "description": "Reuse for identical logical upload retries within receipt retention.",
        "title": "Idempotency-Key"
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
    "409": {
      "description": "The upload identity conflicts with existing content or a deleted run."
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
    },
    "503": {
      "description": "Upload admission or queue confirmation unavailable. If Location and X-Upload-Task-ID are present, processing may still start: poll the existing upload before resubmitting.",
      "headers": {
        "Location": {
          "description": "Existing upload status URL, when dispatch is unconfirmed",
          "schema": {
            "type": "string"
          }
        },
        "X-Upload-Run-ID": {
          "description": "Existing run identifier, when dispatch is unconfirmed",
          "schema": {
            "type": "string"
          }
        },
        "X-Upload-Task-ID": {
          "description": "Existing upload task identifier, when dispatch is unconfirmed",
          "schema": {
            "type": "string"
          }
        }
      }
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

Source: [backend/app/routers/ingest.py:778](../../../backend/app/routers/ingest.py#L778).

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

## POST `/api/v1/ingest/uploads/{task_id}/cleanup`

Remove a terminal upload's temporary queued payload



Source: [backend/app/routers/ingest.py:904](../../../backend/app/routers/ingest.py#L904).

Dependency chain: `OAuth2PasswordBearer`, `get_api_key_context`, `get_current_user_or_api_key`, `get_db`.

Declared Python handler arguments (includes exact role/guard options):

```python
task_id: str, db: AsyncSession=Depends(get_db), auth: tuple[User, None]=Depends(get_api_key_context)
```

Direct handler error branches (dependency/service errors can add others):

```python
HTTPException(409, detail='The upload is unresolved; its queued payload must be retained')
HTTPException(503, detail='Upload payload cleanup is temporarily unavailable')
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "cleanup_upload_api_v1_ingest_uploads__task_id__cleanup_post",
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
{'task_id': str(row.task_id), 'cleaned': cleaned}
```

Contract limit: at least one response has an unstructured schema. Read the linked handler/serializer for emitted fields; the empty schema is not a promise of an empty JSON object.

## POST `/api/v1/ingest/uploads/{task_id}/retry`

Resume a retained upload using its original identity



Source: [backend/app/routers/ingest.py:850](../../../backend/app/routers/ingest.py#L850).

Dependency chain: `OAuth2PasswordBearer`, `get_api_key_context`, `get_current_user_or_api_key`, `get_db`.

Declared Python handler arguments (includes exact role/guard options):

```python
task_id: str, db: AsyncSession=Depends(get_db), auth: tuple[User, None]=Depends(get_api_key_context)
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "retry_upload_api_v1_ingest_uploads__task_id__retry_post",
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
    "409": {
      "description": "The upload identity conflicts with existing content or a deleted run."
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
    },
    "503": {
      "description": "Upload admission or queue confirmation unavailable. If Location and X-Upload-Task-ID are present, processing may still start: poll the existing upload before resubmitting.",
      "headers": {
        "Location": {
          "description": "Existing upload status URL, when dispatch is unconfirmed",
          "schema": {
            "type": "string"
          }
        },
        "X-Upload-Run-ID": {
          "description": "Existing run identifier, when dispatch is unconfirmed",
          "schema": {
            "type": "string"
          }
        },
        "X-Upload-Task-ID": {
          "description": "Existing upload task identifier, when dispatch is unconfirmed",
          "schema": {
            "type": "string"
          }
        }
      }
    }
  },
  "security": [
    {
      "JWT": []
    }
  ]
}
```
