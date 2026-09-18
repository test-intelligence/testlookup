# Scim 2 0 API

[Documentation home](../../README.md) · [Regeneration](../../handoff/maintenance.md)

Generated from tracked source by `scripts/generate_handoff_reference.py`. Do not edit by hand. The baseline and validation limits are recorded in [verification](../../handoff/verification.md).

## GET `/api/v1/scim/v2/ResourceTypes`

Scim Resource Types

SCIM 2.0: List provisionable resource types.

Source: [backend/app/routers/scim.py:335](../../../backend/app/routers/scim.py#L335).

Dependency chain: `get_db`, `verify_scim_bearer`.

Declared Python handler arguments (includes exact role/guard options):

```python
request: Request, _scim_token: SCIMToken=Depends(verify_scim_bearer)
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "scim_resource_types_api_v1_scim_v2_ResourceTypes_get",
  "parameters": [
    {
      "in": "header",
      "name": "Authorization",
      "required": true,
      "schema": {
        "title": "Authorization",
        "type": "string"
      }
    }
  ],
  "responses": {
    "200": {
      "content": {
        "application/scim+json": {
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
discovery_list([user_resource_type(_scim_base_url(request))])
```

Contract limit: at least one response has an unstructured schema. Read the linked handler/serializer for emitted fields; the empty schema is not a promise of an empty JSON object.

## GET `/api/v1/scim/v2/ResourceTypes/{resource_type}`

Scim Resource Type

SCIM 2.0: Retrieve one provisionable resource type.

Source: [backend/app/routers/scim.py:344](../../../backend/app/routers/scim.py#L344).

Dependency chain: `get_db`, `verify_scim_bearer`.

Declared Python handler arguments (includes exact role/guard options):

```python
resource_type: str, request: Request, _scim_token: SCIMToken=Depends(verify_scim_bearer)
```

Direct handler error branches (dependency/service errors can add others):

```python
HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Resource type not found')
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "scim_resource_type_api_v1_scim_v2_ResourceTypes__resource_type__get",
  "parameters": [
    {
      "in": "path",
      "name": "resource_type",
      "required": true,
      "schema": {
        "title": "Resource Type",
        "type": "string"
      }
    },
    {
      "in": "header",
      "name": "Authorization",
      "required": true,
      "schema": {
        "title": "Authorization",
        "type": "string"
      }
    }
  ],
  "responses": {
    "200": {
      "content": {
        "application/scim+json": {
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
user_resource_type(_scim_base_url(request))
```

Contract limit: at least one response has an unstructured schema. Read the linked handler/serializer for emitted fields; the empty schema is not a promise of an empty JSON object.

## GET `/api/v1/scim/v2/Schemas`

Scim Schemas

SCIM 2.0: List supported resource schemas.

Source: [backend/app/routers/scim.py:356](../../../backend/app/routers/scim.py#L356).

Dependency chain: `get_db`, `verify_scim_bearer`.

Declared Python handler arguments (includes exact role/guard options):

```python
request: Request, _scim_token: SCIMToken=Depends(verify_scim_bearer)
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "scim_schemas_api_v1_scim_v2_Schemas_get",
  "parameters": [
    {
      "in": "header",
      "name": "Authorization",
      "required": true,
      "schema": {
        "title": "Authorization",
        "type": "string"
      }
    }
  ],
  "responses": {
    "200": {
      "content": {
        "application/scim+json": {
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
discovery_list([user_schema(_scim_base_url(request))])
```

Contract limit: at least one response has an unstructured schema. Read the linked handler/serializer for emitted fields; the empty schema is not a promise of an empty JSON object.

## GET `/api/v1/scim/v2/Schemas/{schema_uri}`

Scim Schema

SCIM 2.0: Retrieve one supported resource schema.

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "scim_schema_api_v1_scim_v2_Schemas__schema_uri__get",
  "parameters": [
    {
      "in": "path",
      "name": "schema_uri",
      "required": true,
      "schema": {
        "title": "Schema Uri",
        "type": "string"
      }
    },
    {
      "in": "header",
      "name": "Authorization",
      "required": true,
      "schema": {
        "title": "Authorization",
        "type": "string"
      }
    }
  ],
  "responses": {
    "200": {
      "content": {
        "application/scim+json": {
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

Contract limit: at least one response has an unstructured schema. Read the linked handler/serializer for emitted fields; the empty schema is not a promise of an empty JSON object.

## GET `/api/v1/scim/v2/ServiceProviderConfig`

Scim Service Provider Config

SCIM 2.0: Discover implemented protocol capabilities.

Source: [backend/app/routers/scim.py:326](../../../backend/app/routers/scim.py#L326).

Dependency chain: `get_db`, `verify_scim_bearer`.

Declared Python handler arguments (includes exact role/guard options):

```python
request: Request, _scim_token: SCIMToken=Depends(verify_scim_bearer)
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "scim_service_provider_config_api_v1_scim_v2_ServiceProviderConfig_get",
  "parameters": [
    {
      "in": "header",
      "name": "Authorization",
      "required": true,
      "schema": {
        "title": "Authorization",
        "type": "string"
      }
    }
  ],
  "responses": {
    "200": {
      "content": {
        "application/scim+json": {
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
service_provider_configuration(_scim_base_url(request))
```

Contract limit: at least one response has an unstructured schema. Read the linked handler/serializer for emitted fields; the empty schema is not a promise of an empty JSON object.

## GET `/api/v1/scim/v2/Users`

Scim List

SCIM 2.0: List users.

Source: [backend/app/routers/scim.py:377](../../../backend/app/routers/scim.py#L377).

Dependency chain: `get_db`, `verify_scim_bearer`.

Declared Python handler arguments (includes exact role/guard options):

```python
request: Request, startIndex: int=Query(1, description='1-based start index (values below 1 become 1)'), count: int=Query(100, description="Requested page size (coerced to the provider's 200-item cap)"), filter: str | None=None, scim_token: SCIMToken=Depends(verify_scim_bearer), db: AsyncSession=Depends(get_db), attributes: str | None=None, excludedAttributes: str | None=None
```

Direct handler error branches (dependency/service errors can add others):

```python
HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail={'detail': str(exc), 'scimType': 'invalidFilter'})
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "scim_list_api_v1_scim_v2_Users_get",
  "parameters": [
    {
      "description": "1-based start index (values below 1 become 1)",
      "in": "query",
      "name": "startIndex",
      "required": false,
      "schema": {
        "default": 1,
        "description": "1-based start index (values below 1 become 1)",
        "title": "Startindex",
        "type": "integer"
      }
    },
    {
      "description": "Requested page size (coerced to the provider's 200-item cap)",
      "in": "query",
      "name": "count",
      "required": false,
      "schema": {
        "default": 100,
        "description": "Requested page size (coerced to the provider's 200-item cap)",
        "title": "Count",
        "type": "integer"
      }
    },
    {
      "in": "query",
      "name": "filter",
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
        "title": "Filter"
      }
    },
    {
      "in": "query",
      "name": "attributes",
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
        "title": "Attributes"
      }
    },
    {
      "in": "query",
      "name": "excludedAttributes",
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
        "title": "Excludedattributes"
      }
    },
    {
      "in": "header",
      "name": "Authorization",
      "required": true,
      "schema": {
        "title": "Authorization",
        "type": "string"
      }
    }
  ],
  "responses": {
    "200": {
      "content": {
        "application/scim+json": {
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
SCIMListResponse(totalResults=total, startIndex=start_index, itemsPerPage=len(users), Resources=resources)
{'schemas': ['urn:ietf:params:scim:api:messages:2.0:ListResponse'], 'totalResults': total, 'startIndex': start_index, 'itemsPerPage': len(users), 'Resources': resources}
```

Contract limit: at least one response has an unstructured schema. Read the linked handler/serializer for emitted fields; the empty schema is not a promise of an empty JSON object.

## POST `/api/v1/scim/v2/Users`

Scim Create

SCIM 2.0: Create a user.

Source: [backend/app/routers/scim.py:459](../../../backend/app/routers/scim.py#L459).

Dependency chain: `get_db`, `verify_scim_bearer`.

Declared Python handler arguments (includes exact role/guard options):

```python
payload: SCIMUserRequest, request: Request, scim_token: SCIMToken=Depends(verify_scim_bearer), db: AsyncSession=Depends(get_db), attributes: str | None=None, excludedAttributes: str | None=None
```

Direct handler error branches (dependency/service errors can add others):

```python
HTTPException(status_code=400, detail='groups contain conflicting or invalid values')
HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='At least one email is required')
HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='emails must contain valid values and at most one primary')
HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='externalId is required for an IdP-bound SCIM token')
HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "scim_create_api_v1_scim_v2_Users_post",
  "parameters": [
    {
      "in": "query",
      "name": "attributes",
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
        "title": "Attributes"
      }
    },
    {
      "in": "query",
      "name": "excludedAttributes",
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
        "title": "Excludedattributes"
      }
    },
    {
      "in": "header",
      "name": "Authorization",
      "required": true,
      "schema": {
        "title": "Authorization",
        "type": "string"
      }
    }
  ],
  "requestBody": {
    "content": {
      "application/json": {
        "schema": {
          "$ref": "#/components/schemas/SCIMUserRequest"
        }
      }
    },
    "required": true
  },
  "responses": {
    "201": {
      "content": {
        "application/scim+json": {
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
_scim_projected_user(user_to_scim_resource(user, base_url, identities.get(user.id)), projection)
```

Contract limit: at least one response has an unstructured schema. Read the linked handler/serializer for emitted fields; the empty schema is not a promise of an empty JSON object.

## DELETE `/api/v1/scim/v2/Users/{user_id}`

Scim Delete

SCIM 2.0: Deactivate (soft-delete) a user.

Source: [backend/app/routers/scim.py:790](../../../backend/app/routers/scim.py#L790).

Dependency chain: `get_db`, `verify_scim_bearer`.

Declared Python handler arguments (includes exact role/guard options):

```python
user_id: uuid.UUID, request: Request, scim_token: SCIMToken=Depends(verify_scim_bearer), db: AsyncSession=Depends(get_db)
```

Direct handler error branches (dependency/service errors can add others):

```python
HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "scim_delete_api_v1_scim_v2_Users__user_id__delete",
  "parameters": [
    {
      "in": "path",
      "name": "user_id",
      "required": true,
      "schema": {
        "format": "uuid",
        "title": "User Id",
        "type": "string"
      }
    },
    {
      "in": "header",
      "name": "Authorization",
      "required": true,
      "schema": {
        "title": "Authorization",
        "type": "string"
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
  }
}
```

## GET `/api/v1/scim/v2/Users/{user_id}`

Scim Get

SCIM 2.0: Get a single user.

Source: [backend/app/routers/scim.py:434](../../../backend/app/routers/scim.py#L434).

Dependency chain: `get_db`, `verify_scim_bearer`.

Declared Python handler arguments (includes exact role/guard options):

```python
user_id: uuid.UUID, request: Request, scim_token: SCIMToken=Depends(verify_scim_bearer), db: AsyncSession=Depends(get_db), attributes: str | None=None, excludedAttributes: str | None=None
```

Direct handler error branches (dependency/service errors can add others):

```python
HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='User not found')
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "scim_get_api_v1_scim_v2_Users__user_id__get",
  "parameters": [
    {
      "in": "path",
      "name": "user_id",
      "required": true,
      "schema": {
        "format": "uuid",
        "title": "User Id",
        "type": "string"
      }
    },
    {
      "in": "query",
      "name": "attributes",
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
        "title": "Attributes"
      }
    },
    {
      "in": "query",
      "name": "excludedAttributes",
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
        "title": "Excludedattributes"
      }
    },
    {
      "in": "header",
      "name": "Authorization",
      "required": true,
      "schema": {
        "title": "Authorization",
        "type": "string"
      }
    }
  ],
  "responses": {
    "200": {
      "content": {
        "application/scim+json": {
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
_scim_projected_user(user_to_scim_resource(user, base_url, identities.get(user.id)), projection)
```

Contract limit: at least one response has an unstructured schema. Read the linked handler/serializer for emitted fields; the empty schema is not a promise of an empty JSON object.

## PATCH `/api/v1/scim/v2/Users/{user_id}`

Scim Patch

SCIM 2.0: Patch (partial update) a user.

Source: [backend/app/routers/scim.py:601](../../../backend/app/routers/scim.py#L601).

Dependency chain: `get_db`, `verify_scim_bearer`.

Declared Python handler arguments (includes exact role/guard options):

```python
user_id: uuid.UUID, payload: SCIMPatchRequestPayload, request: Request, scim_token: SCIMToken=Depends(verify_scim_bearer), db: AsyncSession=Depends(get_db), attributes: str | None=None, excludedAttributes: str | None=None
```

Direct handler error branches (dependency/service errors can add others):

```python
HTTPException(status_code=400, detail='At least one PATCH operation is required')
HTTPException(status_code=400, detail='active must be a boolean')
HTTPException(status_code=400, detail='emails must contain a valid value')
HTTPException(status_code=400, detail='groups must be an array')
HTTPException(status_code=400, detail=f'Unsupported PATCH operation: {op.op}')
HTTPException(status_code=400, detail=f'Unsupported PATCH path: {op.path}')
HTTPException(status_code=400, detail=str(exc))
HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='externalId updates require an IdP-bound SCIM token')
HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "scim_patch_api_v1_scim_v2_Users__user_id__patch",
  "parameters": [
    {
      "in": "path",
      "name": "user_id",
      "required": true,
      "schema": {
        "format": "uuid",
        "title": "User Id",
        "type": "string"
      }
    },
    {
      "in": "query",
      "name": "attributes",
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
        "title": "Attributes"
      }
    },
    {
      "in": "query",
      "name": "excludedAttributes",
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
        "title": "Excludedattributes"
      }
    },
    {
      "in": "header",
      "name": "Authorization",
      "required": true,
      "schema": {
        "title": "Authorization",
        "type": "string"
      }
    }
  ],
  "requestBody": {
    "content": {
      "application/json": {
        "schema": {
          "$ref": "#/components/schemas/SCIMPatchRequestPayload"
        }
      }
    },
    "required": true
  },
  "responses": {
    "200": {
      "content": {
        "application/scim+json": {
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
_scim_projected_user(user_to_scim_resource(user, base_url, identities.get(user.id)), projection)
```

Contract limit: at least one response has an unstructured schema. Read the linked handler/serializer for emitted fields; the empty schema is not a promise of an empty JSON object.

## PUT `/api/v1/scim/v2/Users/{user_id}`

Scim Replace

SCIM 2.0: Replace (full update) a user.

Source: [backend/app/routers/scim.py:526](../../../backend/app/routers/scim.py#L526).

Dependency chain: `get_db`, `verify_scim_bearer`.

Declared Python handler arguments (includes exact role/guard options):

```python
user_id: uuid.UUID, payload: SCIMUserRequest, request: Request, scim_token: SCIMToken=Depends(verify_scim_bearer), db: AsyncSession=Depends(get_db), attributes: str | None=None, excludedAttributes: str | None=None
```

Direct handler error branches (dependency/service errors can add others):

```python
HTTPException(status_code=400, detail='groups contain conflicting or invalid values')
HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='At least one email is required')
HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='emails must contain valid values and at most one primary')
HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='externalId is required for an IdP-bound SCIM token')
HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "scim_replace_api_v1_scim_v2_Users__user_id__put",
  "parameters": [
    {
      "in": "path",
      "name": "user_id",
      "required": true,
      "schema": {
        "format": "uuid",
        "title": "User Id",
        "type": "string"
      }
    },
    {
      "in": "query",
      "name": "attributes",
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
        "title": "Attributes"
      }
    },
    {
      "in": "query",
      "name": "excludedAttributes",
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
        "title": "Excludedattributes"
      }
    },
    {
      "in": "header",
      "name": "Authorization",
      "required": true,
      "schema": {
        "title": "Authorization",
        "type": "string"
      }
    }
  ],
  "requestBody": {
    "content": {
      "application/json": {
        "schema": {
          "$ref": "#/components/schemas/SCIMUserRequest"
        }
      }
    },
    "required": true
  },
  "responses": {
    "200": {
      "content": {
        "application/scim+json": {
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
_scim_projected_user(user_to_scim_resource(user, base_url, identities.get(user.id)), projection)
```

Contract limit: at least one response has an unstructured schema. Read the linked handler/serializer for emitted fields; the empty schema is not a promise of an empty JSON object.
