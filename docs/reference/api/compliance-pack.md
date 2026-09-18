# Compliance Pack API

[Documentation home](../../README.md) · [Regeneration](../../handoff/maintenance.md)

Generated from tracked source by `scripts/generate_handoff_reference.py`. Do not edit by hand. The baseline and validation limits are recorded in [verification](../../handoff/verification.md).

## DELETE `/api/v1/compliance-packs/{pack_id}`

Delete Compliance Pack

Delete a pack whose retention window has already passed.

**Refused while the window is still open** — 409, naming the expiry. Retire
it first if it really should go now. That two-step exists because the
window was previously enforced only on the way out: nothing could shorten
it, and with no delete route nothing tested it on the way in either.

S9 adds the legal-hold check alongside this. It is deliberately absent
rather than stubbed: a hold check against a table that does not exist
reads as a working guard while never firing.

Source: [backend/app/routers/compliance_packs.py:205](../../../backend/app/routers/compliance_packs.py#L205).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_role.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
pack_id: uuid.UUID, current_user: User=Depends(require_role(UserRole.ADMIN, allow_project_key=True)), db: AsyncSession=Depends(get_db)
```

Direct handler error branches (dependency/service errors can add others):

```python
HTTPException(status_code=404, detail='Compliance pack not found')
HTTPException(status_code=409, detail={'blockers': blockers})
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "delete_compliance_pack_api_v1_compliance_packs__pack_id__delete",
  "parameters": [
    {
      "in": "path",
      "name": "pack_id",
      "required": true,
      "schema": {
        "format": "uuid",
        "title": "Pack Id",
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
    "204": {
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

## GET `/api/v1/compliance-packs/{pack_id}/download`

Download Compliance Pack

Stream a pack ZIP back to the caller.

Enforces tenant isolation via the pack's ``project_id``. The response
includes the SHA-256 of the manifest in the ``X-Manifest-Sha256``
header so automated verifiers can assert before reading the body.

Source: [backend/app/routers/compliance_packs.py:112](../../../backend/app/routers/compliance_packs.py#L112).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`.

Declared Python handler arguments (includes exact role/guard options):

```python
pack_id: uuid.UUID, current_user: User=Depends(get_current_active_user), db: AsyncSession=Depends(get_db)
```

Direct handler error branches (dependency/service errors can add others):

```python
HTTPException(status_code=404, detail='Compliance pack not found')
HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail='Failed to load compliance pack from object storage')
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "download_compliance_pack_api_v1_compliance_packs__pack_id__download_get",
  "parameters": [
    {
      "in": "path",
      "name": "pack_id",
      "required": true,
      "schema": {
        "format": "uuid",
        "title": "Pack Id",
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
StreamingResponse(iter([data]), media_type='application/zip', headers={'Content-Disposition': f'attachment; filename="{filename}"', 'Content-Length': str(len(data)), 'X-Manifest-Sha256': pack.manifest_sha256, 'X-Pack-Id': str(pack.id)})
```

Contract limit: at least one response has an unstructured schema. Read the linked handler/serializer for emitted fields; the empty schema is not a promise of an empty JSON object.

## POST `/api/v1/compliance-packs/{pack_id}/retire`

Retire Compliance Pack

Bring a pack's retention window forward to now (S4).

Does not delete. The nightly purge already deletes packs past their
window, using an object-then-row ordering that makes a failed object
delete retryable; duplicating that here would mean two implementations of
the one sequence that must not be got wrong.

ADMIN, typed confirmation, and a reason — a pack is audit evidence
generated with a seven-year default, so shortening that is a deliberate
act and is recorded as one.

Source: [backend/app/routers/compliance_packs.py:152](../../../backend/app/routers/compliance_packs.py#L152).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_role.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
pack_id: uuid.UUID, body: RetireCompliancePackRequest, current_user: User=Depends(require_role(UserRole.ADMIN, allow_project_key=True)), db: AsyncSession=Depends(get_db)
```

Direct handler error branches (dependency/service errors can add others):

```python
HTTPException(status_code=404, detail='Compliance pack not found')
HTTPException(status_code=422, detail='confirmation_id must match the pack id exactly')
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "retire_compliance_pack_api_v1_compliance_packs__pack_id__retire_post",
  "parameters": [
    {
      "in": "path",
      "name": "pack_id",
      "required": true,
      "schema": {
        "format": "uuid",
        "title": "Pack Id",
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
          "$ref": "#/components/schemas/RetireCompliancePackRequest"
        }
      }
    },
    "required": true
  },
  "responses": {
    "200": {
      "content": {
        "application/json": {
          "schema": {
            "$ref": "#/components/schemas/CompliancePackLifecycleResponse"
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

## POST `/api/v1/releases/{release_id}/compliance-pack`

Generate Compliance Pack

Generate a new compliance pack for a release. QA_LEAD+ only.

Source: [backend/app/routers/compliance_packs.py:52](../../../backend/app/routers/compliance_packs.py#L52).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_release_access.<locals>._check`, `require_role.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
release_id: uuid.UUID, payload: CompliancePackGenerateRequest, current_user: User=Depends(require_role(UserRole.QA_LEAD, allow_project_key=True)), db: AsyncSession=Depends(get_db), _: User=Depends(require_release_access())
```

Direct handler error branches (dependency/service errors can add others):

```python
HTTPException(status_code=404, detail='Release not found')
HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc))
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "generate_compliance_pack_api_v1_releases__release_id__compliance_pack_post",
  "parameters": [
    {
      "in": "path",
      "name": "release_id",
      "required": true,
      "schema": {
        "format": "uuid",
        "title": "Release Id",
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
          "$ref": "#/components/schemas/CompliancePackGenerateRequest"
        }
      }
    },
    "required": true
  },
  "responses": {
    "201": {
      "content": {
        "application/json": {
          "schema": {
            "$ref": "#/components/schemas/CompliancePackRead"
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

## GET `/api/v1/releases/{release_id}/compliance-packs`

List Compliance Packs

List every historical pack ever generated for a release.

Source: [backend/app/routers/compliance_packs.py:95](../../../backend/app/routers/compliance_packs.py#L95).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_release_access.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
release_id: uuid.UUID, current_user: User=Depends(get_current_active_user), db: AsyncSession=Depends(get_db), _: User=Depends(require_release_access())
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "list_compliance_packs_api_v1_releases__release_id__compliance_packs_get",
  "parameters": [
    {
      "in": "path",
      "name": "release_id",
      "required": true,
      "schema": {
        "format": "uuid",
        "title": "Release Id",
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
            "items": {
              "$ref": "#/components/schemas/CompliancePackRead"
            },
            "title": "Response List Compliance Packs Api V1 Releases  Release Id  Compliance Packs Get",
            "type": "array"
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
