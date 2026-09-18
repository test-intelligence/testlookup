# Release Attribution Rules API

[Documentation home](../../README.md) · [Regeneration](../../handoff/maintenance.md)

Generated from tracked source by `scripts/generate_handoff_reference.py`. Do not edit by hand. The baseline and validation limits are recorded in [verification](../../handoff/verification.md).

## GET `/api/v1/projects/{project_id}/attribution-rules`

List Attribution Rules

Rules for this project, in the order the ladder evaluates them.

Listed in evaluation order rather than by creation, because which rule wins
is the whole question when two could match — sorting by anything else would
show a list that does not explain the outcome.

Source: [backend/app/routers/release_attribution_rules.py:152](../../../backend/app/routers/release_attribution_rules.py#L152).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_project_access.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
project_id: uuid.UUID, db: AsyncSession=Depends(get_db), _: User=Depends(get_current_active_user), __: User=Depends(require_project_access())
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "list_attribution_rules_api_v1_projects__project_id__attribution_rules_get",
  "parameters": [
    {
      "in": "path",
      "name": "project_id",
      "required": true,
      "schema": {
        "format": "uuid",
        "title": "Project Id",
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
{'items': [_serialize(r) for r in rows], 'total': len(rows)}
```

Contract limit: at least one response has an unstructured schema. Read the linked handler/serializer for emitted fields; the empty schema is not a promise of an empty JSON object.

## POST `/api/v1/projects/{project_id}/attribution-rules`

Create Attribution Rule

Create a rule. Rung 3 can fire for this project from the next run on.

Source: [backend/app/routers/release_attribution_rules.py:183](../../../backend/app/routers/release_attribution_rules.py#L183).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_project_access.<locals>._check`, `require_role.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
project_id: uuid.UUID, body: AttributionRuleIn, db: AsyncSession=Depends(get_db), current_user: User=Depends(require_role(UserRole.QA_LEAD, allow_project_key=True)), __: User=Depends(require_project_access())
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "create_attribution_rule_api_v1_projects__project_id__attribution_rules_post",
  "parameters": [
    {
      "in": "path",
      "name": "project_id",
      "required": true,
      "schema": {
        "format": "uuid",
        "title": "Project Id",
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
          "$ref": "#/components/schemas/AttributionRuleIn"
        }
      }
    },
    "required": true
  },
  "responses": {
    "201": {
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
_serialize(rule)
```

Contract limit: at least one response has an unstructured schema. Read the linked handler/serializer for emitted fields; the empty schema is not a promise of an empty JSON object.

## POST `/api/v1/projects/{project_id}/attribution-rules/preview`

Preview Attribution Rule

Try a rule against recent runs WITHOUT saving it.

The failure mode of an attribution rule is not that it misses — it is that
it captures everything, and every run still gets attributed so nothing looks
wrong. ``matched`` against ``considered`` is the one number that tells those
apart before the rule starts deciding where real runs land.

Evaluated with ``rule_matches`` — the same predicate the ladder uses — on an
UNSAVED instance, so the preview cannot disagree with the rule it previews.

Source: [backend/app/routers/release_attribution_rules.py:320](../../../backend/app/routers/release_attribution_rules.py#L320).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_project_access.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
project_id: uuid.UUID, body: AttributionRuleIn, limit: int=Query(PREVIEW_RUN_LIMIT, ge=1, le=PREVIEW_RUN_LIMIT), db: AsyncSession=Depends(get_db), _: User=Depends(get_current_active_user), __: User=Depends(require_project_access())
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "preview_attribution_rule_api_v1_projects__project_id__attribution_rules_preview_post",
  "parameters": [
    {
      "in": "path",
      "name": "project_id",
      "required": true,
      "schema": {
        "format": "uuid",
        "title": "Project Id",
        "type": "string"
      }
    },
    {
      "in": "query",
      "name": "limit",
      "required": false,
      "schema": {
        "default": 200,
        "maximum": 200,
        "minimum": 1,
        "title": "Limit",
        "type": "integer"
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
          "$ref": "#/components/schemas/AttributionRuleIn"
        }
      }
    },
    "required": true
  },
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
{'considered': len(runs), 'matched': len(matched), 'field_present': sum((1 for r in runs if release_attribution._run_value(r, body.match_field))), 'sample': [{'run_id': str(r.id), 'build_number': r.build_number, 'branch': r.branch, 'environment': r.environment} for r in matched[:10]]}
```

Contract limit: at least one response has an unstructured schema. Read the linked handler/serializer for emitted fields; the empty schema is not a promise of an empty JSON object.

## DELETE `/api/v1/projects/{project_id}/attribution-rules/{rule_id}`

Delete Attribution Rule

Delete a rule.

Deliberately does NOT re-attribute the runs it already matched. A link
carries ``link_source=rule_match`` and the release it produced; rewriting
history because a rule was retired would change what a past gate decision
was based on, silently.

Source: [backend/app/routers/release_attribution_rules.py:284](../../../backend/app/routers/release_attribution_rules.py#L284).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_project_access.<locals>._check`, `require_role.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
project_id: uuid.UUID, rule_id: str, db: AsyncSession=Depends(get_db), current_user: User=Depends(require_role(UserRole.QA_LEAD, allow_project_key=True)), __: User=Depends(require_project_access())
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "delete_attribution_rule_api_v1_projects__project_id__attribution_rules__rule_id__delete",
  "parameters": [
    {
      "in": "path",
      "name": "project_id",
      "required": true,
      "schema": {
        "format": "uuid",
        "title": "Project Id",
        "type": "string"
      }
    },
    {
      "in": "path",
      "name": "rule_id",
      "required": true,
      "schema": {
        "title": "Rule Id",
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

## PUT `/api/v1/projects/{project_id}/attribution-rules/{rule_id}`

Update Attribution Rule



Source: [backend/app/routers/release_attribution_rules.py:234](../../../backend/app/routers/release_attribution_rules.py#L234).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_project_access.<locals>._check`, `require_role.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
project_id: uuid.UUID, rule_id: str, body: AttributionRuleUpdate, db: AsyncSession=Depends(get_db), current_user: User=Depends(require_role(UserRole.QA_LEAD, allow_project_key=True)), __: User=Depends(require_project_access())
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "update_attribution_rule_api_v1_projects__project_id__attribution_rules__rule_id__put",
  "parameters": [
    {
      "in": "path",
      "name": "project_id",
      "required": true,
      "schema": {
        "format": "uuid",
        "title": "Project Id",
        "type": "string"
      }
    },
    {
      "in": "path",
      "name": "rule_id",
      "required": true,
      "schema": {
        "title": "Rule Id",
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
          "$ref": "#/components/schemas/AttributionRuleUpdate"
        }
      }
    },
    "required": true
  },
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
_serialize(rule)
```

Contract limit: at least one response has an unstructured schema. Read the linked handler/serializer for emitted fields; the empty schema is not a promise of an empty JSON object.
