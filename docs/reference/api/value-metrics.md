# Value Metrics API

[Documentation home](../../README.md) · [Regeneration](../../handoff/maintenance.md)

Generated from tracked source by `scripts/generate_handoff_reference.py`. Do not edit by hand. The baseline and validation limits are recorded in [verification](../../handoff/verification.md).

## GET `/api/v1/projects/{project_id}/value-metrics/assumptions`

Get Value Metric Assumptions

Effective hours-saved assumptions for a project (defaults when no
row exists — the UI always renders the form).

Source: [backend/app/routers/value_metric_assumptions.py:44](../../../backend/app/routers/value_metric_assumptions.py#L44).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_project_access.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
project_id: uuid.UUID, db: AsyncSession=Depends(get_db), current_user: User=Depends(get_current_active_user), _: User=Depends(require_project_access())
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "get_value_metric_assumptions_api_v1_projects__project_id__value_metrics_assumptions_get",
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
          "schema": {
            "$ref": "#/components/schemas/ValueMetricAssumptionsRead"
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

## PUT `/api/v1/projects/{project_id}/value-metrics/assumptions`

Put Value Metric Assumptions

Upsert the project's assumptions row. QA_LEAD+. Omitted fields keep
their current (or default) value; returns the effective assumptions.

Source: [backend/app/routers/value_metric_assumptions.py:62](../../../backend/app/routers/value_metric_assumptions.py#L62).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_project_access.<locals>._check`, `require_role.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
project_id: uuid.UUID, payload: ValueMetricAssumptionsWrite, db: AsyncSession=Depends(get_db), current_user: User=Depends(require_role(UserRole.QA_LEAD, allow_project_key=True)), _: User=Depends(require_project_access())
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "put_value_metric_assumptions_api_v1_projects__project_id__value_metrics_assumptions_put",
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
          "$ref": "#/components/schemas/ValueMetricAssumptionsWrite"
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
            "$ref": "#/components/schemas/ValueMetricAssumptionsRead"
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

## GET `/api/v1/value-metrics`

Get Metrics

Return operational value metrics for a project (or all) over a time
window, including the US-12.1 engineer-hours-saved model (``months``
bounds the monthly trend).

``release_id`` / ``suite_name`` are accepted, authorised and declared in
``meta.ignored_filters`` with the reason: these are time-based counts
over the project, and most of them have no release or suite to filter.

Source: [backend/app/routers/value_metrics.py:49](../../../backend/app/routers/value_metrics.py#L49).

Dependency chain: `OAuth2PasswordBearer`, `analytics_scope.<locals>.dependency`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`.

Declared Python handler arguments (includes exact role/guard options):

```python
months: int=Query(6, ge=1, le=24), scope: AnalyticsScope=Depends(analytics_scope(_VALUE_SCOPE)), db: AsyncSession=Depends(get_db)
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "get_metrics_api_v1_value_metrics_get",
  "parameters": [
    {
      "in": "query",
      "name": "months",
      "required": false,
      "schema": {
        "default": 6,
        "maximum": 24,
        "minimum": 1,
        "title": "Months",
        "type": "integer"
      }
    },
    {
      "description": "One project (single-valued). Omit for every project you can read.",
      "in": "query",
      "name": "project_id",
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
        "description": "One project (single-valued). Omit for every project you can read.",
        "title": "Project Id"
      }
    },
    {
      "description": "Repeatable (OR, at most 20): a release UUID or 'unattributed' for runs no release claims.",
      "in": "query",
      "name": "release_id",
      "required": false,
      "schema": {
        "anyOf": [
          {
            "items": {
              "type": "string"
            },
            "type": "array"
          },
          {
            "type": "null"
          }
        ],
        "description": "Repeatable (OR, at most 20): a release UUID or 'unattributed' for runs no release claims.",
        "title": "Release Id"
      }
    },
    {
      "description": "Repeatable (OR, at most 50), 1-500 characters; matched case-insensitively on the effective suite.",
      "in": "query",
      "name": "suite_name",
      "required": false,
      "schema": {
        "anyOf": [
          {
            "items": {
              "type": "string"
            },
            "type": "array"
          },
          {
            "type": "null"
          }
        ],
        "description": "Repeatable (OR, at most 50), 1-500 characters; matched case-insensitively on the effective suite.",
        "title": "Suite Name"
      }
    },
    {
      "description": "Window in days, 1-365.",
      "in": "query",
      "name": "days",
      "required": false,
      "schema": {
        "default": 30,
        "description": "Window in days, 1-365.",
        "title": "Days",
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
with_meta(payload, meta)
{'meta': meta}
```

Contract limit: at least one response has an unstructured schema. Read the linked handler/serializer for emitted fields; the empty schema is not a promise of an empty JSON object.

## GET `/api/v1/value-metrics/by-team`

Get Metrics By Team

Tier 2 item 11 — partition value metrics by owning team.

Uses ``ServiceOwnershipRule`` rows to bucket tests + defects by
team. Requires a ``project_id`` because ownership rules are
project-scoped. Returns a per-team rollup with test count, pass
rate, defect count, MTTR hours, and estimated minutes saved so
the dashboard can render per-team tiles without any further
transformation.

Source: [backend/app/routers/value_metrics.py:95](../../../backend/app/routers/value_metrics.py#L95).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`.

Declared Python handler arguments (includes exact role/guard options):

```python
project_id: str=Query(...), days: int=Query(30, ge=1, le=365), db: AsyncSession=Depends(get_db), current_user: User=Depends(get_current_active_user)
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "get_metrics_by_team_api_v1_value_metrics_by_team_get",
  "parameters": [
    {
      "in": "query",
      "name": "project_id",
      "required": true,
      "schema": {
        "title": "Project Id",
        "type": "string"
      }
    },
    {
      "in": "query",
      "name": "days",
      "required": false,
      "schema": {
        "default": 30,
        "maximum": 365,
        "minimum": 1,
        "title": "Days",
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
await get_team_value_metrics(db, project_id=pid, days=days)
```

Contract limit: at least one response has an unstructured schema. Read the linked handler/serializer for emitted fields; the empty schema is not a promise of an empty JSON object.

## GET `/api/v1/value-metrics/export`

Export Metrics

Export value metrics as a downloadable JSON report.

Source: [backend/app/routers/value_metrics.py:118](../../../backend/app/routers/value_metrics.py#L118).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`.

Declared Python handler arguments (includes exact role/guard options):

```python
project_id: Optional[str]=Query(None), days: int=Query(30, ge=1, le=365), db: AsyncSession=Depends(get_db), current_user: User=Depends(get_current_active_user)
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "export_metrics_api_v1_value_metrics_export_get",
  "parameters": [
    {
      "in": "query",
      "name": "project_id",
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
        "title": "Project Id"
      }
    },
    {
      "in": "query",
      "name": "days",
      "required": false,
      "schema": {
        "default": 30,
        "maximum": 365,
        "minimum": 1,
        "title": "Days",
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
Response(content='{"report_type": "value_metrics"}', media_type='application/json', headers={'Content-Disposition': f'attachment; filename=value-metrics-{days}d.json'})
Response(content=content, media_type='application/json', headers={'Content-Disposition': f'attachment; filename=value-metrics-{days}d.json'})
```

Contract limit: at least one response has an unstructured schema. Read the linked handler/serializer for emitted fields; the empty schema is not a promise of an empty JSON object.

## GET `/api/v1/value-metrics/methodology`

Get Methodology Page

US-12.1: the hours-saved model documented — legs, formulas, caveats,
defaults, research anchors. Static content; login-only, not
project-scoped.

Source: [backend/app/routers/value_metrics.py:85](../../../backend/app/routers/value_metrics.py#L85).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`.

Declared Python handler arguments (includes exact role/guard options):

```python
current_user: User=Depends(get_current_active_user)
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "get_methodology_page_api_v1_value_metrics_methodology_get",
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
get_methodology()
```

Contract limit: at least one response has an unstructured schema. Read the linked handler/serializer for emitted fields; the empty schema is not a promise of an empty JSON object.
