# Run Intelligence API

[Documentation home](../../README.md) · [Regeneration](../../handoff/maintenance.md)

Generated from tracked source by `scripts/generate_handoff_reference.py`. Do not edit by hand. The baseline and validation limits are recorded in [verification](../../handoff/verification.md).

## GET `/api/v1/runs/{run_id}/baseline-diff`

Get Run Baseline Diff

Return a deterministic baseline diff for the given test run.

Compares against the most recent prior PASSED run for the same project.
No LLM calls — all classification is deterministic using existing AI analyses.

Response includes:
- pass_rate_delta, new_failures, resolved_failures
- regression_classification (run-level)
- regression_clusters (per-cluster: cluster_id, label, size, classification)
- classified_new_failures (new failures enriched with their cluster classification)
- suites_impacted_delta, current_suite_count, baseline_suite_count

Source: [backend/app/routers/run_intelligence.py:265](../../../backend/app/routers/run_intelligence.py#L265).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_run_access.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
run_id: uuid.UUID, db: Any=Depends(get_db), _: Any=Depends(require_run_access())
```

Direct handler error branches (dependency/service errors can add others):

```python
HTTPException(status_code=404, detail='No baseline (prior passing run) found for this project.')
HTTPException(status_code=404, detail=f'TestRun {run_id} not found')
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "get_run_baseline_diff_api_v1_runs__run_id__baseline_diff_get",
  "parameters": [
    {
      "in": "path",
      "name": "run_id",
      "required": true,
      "schema": {
        "format": "uuid",
        "title": "Run Id",
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
diff
```

Contract limit: at least one response has an unstructured schema. Read the linked handler/serializer for emitted fields; the empty schema is not a promise of an empty JSON object.

## GET `/api/v1/runs/{run_id}/decision-reports`

List Run Decision Reports

List immutable published DecisionReport versions for an authorized run.

Source: [backend/app/routers/run_intelligence.py:185](../../../backend/app/routers/run_intelligence.py#L185).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_run_access.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
run_id: uuid.UUID, response: Response, limit: int=Query(default=50, ge=1, le=100), db: Any=Depends(get_db), _: Any=Depends(require_run_access())
```

Direct handler error branches (dependency/service errors can add others):

```python
HTTPException(status_code=503, detail='decision_report_versions_unavailable')
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "list_run_decision_reports_api_v1_runs__run_id__decision_reports_get",
  "parameters": [
    {
      "in": "path",
      "name": "run_id",
      "required": true,
      "schema": {
        "format": "uuid",
        "title": "Run Id",
        "type": "string"
      }
    },
    {
      "in": "query",
      "name": "limit",
      "required": false,
      "schema": {
        "default": 50,
        "maximum": 100,
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
result
```

Contract limit: at least one response has an unstructured schema. Read the linked handler/serializer for emitted fields; the empty schema is not a promise of an empty JSON object.

## GET `/api/v1/runs/{run_id}/export`

Export Intelligence Report

Export a customer-facing intelligence report as a structured JSON payload.

Combines: run summary, structured AI analysis, baseline diff, release decision,
criticality dimensions, role actions, and provenance — in a single downloadable document.

Source: [backend/app/routers/run_intelligence.py:338](../../../backend/app/routers/run_intelligence.py#L338).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_run_access.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
run_id: uuid.UUID, mode: str=Query(default='manager', description='Summary mode: executive | developer | manager'), db: Any=Depends(get_db), _: Any=Depends(require_run_access())
```

Direct handler error branches (dependency/service errors can add others):

```python
HTTPException(status_code=404, detail=str(exc))
HTTPException(status_code=409, detail=refusal_detail(distribution))
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "export_intelligence_report_api_v1_runs__run_id__export_get",
  "parameters": [
    {
      "in": "path",
      "name": "run_id",
      "required": true,
      "schema": {
        "format": "uuid",
        "title": "Run Id",
        "type": "string"
      }
    },
    {
      "description": "Summary mode: executive | developer | manager",
      "in": "query",
      "name": "mode",
      "required": false,
      "schema": {
        "default": "manager",
        "description": "Summary mode: executive | developer | manager",
        "title": "Mode",
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
Response(content=content, media_type='application/json', headers={'Content-Disposition': f'attachment; filename={filename}'})
```

Contract limit: at least one response has an unstructured schema. Read the linked handler/serializer for emitted fields; the empty schema is not a promise of an empty JSON object.

## GET `/api/v1/runs/{run_id}/intelligence`

Get Run Intelligence Endpoint

Return a unified Run Intelligence snapshot for the given test run.

Aggregates:
- 4-layer structured summary (MongoDB)
- Failure clusters with criticality levels
- AI analyses (category breakdown, top findings)
- Release decision + 7-dimension risk scores
- What changed since last good run (deterministic baseline diff)
- Lightweight defect candidates
- Pipeline stage history with skip context
- Provenance (schema_version, fallback_used, tools_used_count)

Source: [backend/app/routers/run_intelligence.py:78](../../../backend/app/routers/run_intelligence.py#L78).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_run_access.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
run_id: uuid.UUID, response: Response, include: str=Query(default='', description='Comma-separated optional expansions: test_cases,evidence,history'), report_version: int | None=Query(default=None, ge=1, description='Immutable DecisionReport version to display; omitted means latest'), db: Any=Depends(get_db), _: Any=Depends(require_run_access())
```

Direct handler error branches (dependency/service errors can add others):

```python
HTTPException(status_code=404, detail=str(exc))
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "get_run_intelligence_endpoint_api_v1_runs__run_id__intelligence_get",
  "parameters": [
    {
      "in": "path",
      "name": "run_id",
      "required": true,
      "schema": {
        "format": "uuid",
        "title": "Run Id",
        "type": "string"
      }
    },
    {
      "description": "Comma-separated optional expansions: test_cases,evidence,history",
      "in": "query",
      "name": "include",
      "required": false,
      "schema": {
        "default": "",
        "description": "Comma-separated optional expansions: test_cases,evidence,history",
        "title": "Include",
        "type": "string"
      }
    },
    {
      "description": "Immutable DecisionReport version to display; omitted means latest",
      "in": "query",
      "name": "report_version",
      "required": false,
      "schema": {
        "anyOf": [
          {
            "minimum": 1,
            "type": "integer"
          },
          {
            "type": "null"
          }
        ],
        "description": "Immutable DecisionReport version to display; omitted means latest",
        "title": "Report Version"
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
await _with_review(db, response, run_id, cached)
await _with_review(db, response, run_id, result)
await _with_review(db, response, run_id, stale)
{**result, **envelope.fields()}
```

Contract limit: at least one response has an unstructured schema. Read the linked handler/serializer for emitted fields; the empty schema is not a promise of an empty JSON object.

## POST `/api/v1/runs/{run_id}/intelligence/refresh`

Refresh Intelligence

Force-refresh the intelligence snapshot for a run.
Invalidates the cached snapshot and recomputes from live data.
Returns the fresh intelligence payload.

Source: [backend/app/routers/run_intelligence.py:298](../../../backend/app/routers/run_intelligence.py#L298).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_run_access.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
run_id: uuid.UUID, response: Response, db: Any=Depends(get_db), _: Any=Depends(require_run_access())
```

Direct handler error branches (dependency/service errors can add others):

```python
HTTPException(status_code=404, detail=str(exc))
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "refresh_intelligence_api_v1_runs__run_id__intelligence_refresh_post",
  "parameters": [
    {
      "in": "path",
      "name": "run_id",
      "required": true,
      "schema": {
        "format": "uuid",
        "title": "Run Id",
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
await _with_review(db, response, run_id, result)
```

Contract limit: at least one response has an unstructured schema. Read the linked handler/serializer for emitted fields; the empty schema is not a promise of an empty JSON object.

## GET `/api/v1/runs/{run_id}/summary`

Get Run Summary By Mode

Return a mode-specific AI-generated summary.

- executive: concise 3-sentence summary + release impact
- developer: evidence pack, stack traces, fix recommendations
- manager:   business impact, criticality, immediate mitigation

Falls back to a deterministic PostgreSQL-derived summary when the AI pipeline
has not yet produced a MongoDB document for this run.

Source: [backend/app/routers/run_intelligence.py:230](../../../backend/app/routers/run_intelligence.py#L230).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_run_access.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
run_id: uuid.UUID, response: Response, mode: str=Query(default='executive', description='Summary mode: executive | developer | manager'), db: Any=Depends(get_db), _: Any=Depends(require_run_access())
```

Direct handler error branches (dependency/service errors can add others):

```python
HTTPException(status_code=404, detail=str(exc))
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "get_run_summary_by_mode_api_v1_runs__run_id__summary_get",
  "parameters": [
    {
      "in": "path",
      "name": "run_id",
      "required": true,
      "schema": {
        "format": "uuid",
        "title": "Run Id",
        "type": "string"
      }
    },
    {
      "description": "Summary mode: executive | developer | manager",
      "in": "query",
      "name": "mode",
      "required": false,
      "schema": {
        "default": "executive",
        "description": "Summary mode: executive | developer | manager",
        "title": "Mode",
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
await _with_review(db, response, run_id, result, ai_generated=not bool(result.get('fallback_used')))
```

Contract limit: at least one response has an unstructured schema. Read the linked handler/serializer for emitted fields; the empty schema is not a promise of an empty JSON object.
