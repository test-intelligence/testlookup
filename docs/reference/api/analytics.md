# Analytics API

[Documentation home](../../README.md) · [Regeneration](../../handoff/maintenance.md)

Generated from tracked source by `scripts/generate_handoff_reference.py`. Do not edit by hand. The baseline and validation limits are recorded in [verification](../../handoff/verification.md).

## GET `/api/v1/analytics/ai-summary`

Ai Analysis Summary

Return summary of AI analysis results for the project.

Source: [backend/app/routers/analytics.py:584](../../../backend/app/routers/analytics.py#L584).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`.

Declared Python handler arguments (includes exact role/guard options):

```python
project_id: str | None=None, days: int=Query(30, ge=1, le=365), db: AsyncSession=Depends(get_db), current_user: User=Depends(get_current_active_user)
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "ai_analysis_summary_api_v1_analytics_ai_summary_get",
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
await analytics_service.ai_analysis_summary(db, str(scoped) if scoped else None, days, allowed_project_ids=allowed)
```

Contract limit: at least one response has an unstructured schema. Read the linked handler/serializer for emitted fields; the empty schema is not a promise of an empty JSON object.

## POST `/api/v1/analytics/classify-uncategorized`

Classify Uncategorized Failures

Assign ``payload.category`` to every failing test case in the project's
window that's currently unlabelled (``failure_category IS NULL`` or
``UNKNOWN``). Mirrors ``AIAnalysis.failure_category`` for any AI rows
backing those test cases.

Used by the Failures page "Classify" CTA when the AI classifier left a
large chunk of failures uncategorized — lets the user tag them all in
one shot rather than per-test.

Source: [backend/app/routers/analytics.py:689](../../../backend/app/routers/analytics.py#L689).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`.

Declared Python handler arguments (includes exact role/guard options):

```python
payload: ClassifyUncategorizedRequest, db: AsyncSession=Depends(get_db), current_user: User=Depends(get_current_active_user)
```

Direct handler error branches (dependency/service errors can add others):

```python
HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail='You do not have access to this project')
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "classify_uncategorized_failures_api_v1_analytics_classify_uncategorized_post",
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
          "$ref": "#/components/schemas/ClassifyUncategorizedRequest"
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
            "$ref": "#/components/schemas/ClassifyUncategorizedResponse"
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

## GET `/api/v1/analytics/coverage`

Coverage Stats

Return test suite coverage stats aggregated over the period.

Source: [backend/app/routers/analytics.py:458](../../../backend/app/routers/analytics.py#L458).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`.

Declared Python handler arguments (includes exact role/guard options):

```python
project_id: str | None=None, days: int=Query(30, ge=1, le=365), suite_name: str | None=Query(None, min_length=1), release_id: str | None=Query(None, description='Scope to one release'), db: AsyncSession=Depends(get_db), current_user: User=Depends(get_current_active_user)
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "coverage_stats_api_v1_analytics_coverage_get",
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
      "in": "query",
      "name": "suite_name",
      "required": false,
      "schema": {
        "anyOf": [
          {
            "minLength": 1,
            "type": "string"
          },
          {
            "type": "null"
          }
        ],
        "title": "Suite Name"
      }
    },
    {
      "description": "Scope to one release",
      "in": "query",
      "name": "release_id",
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
        "description": "Scope to one release",
        "title": "Release Id"
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
await analytics_service.coverage_stats(db, str(scoped) if scoped else None, days, suite_name=suite_name, allowed_project_ids=allowed, release_id=release_id)
```

Contract limit: at least one response has an unstructured schema. Read the linked handler/serializer for emitted fields; the empty schema is not a promise of an empty JSON object.

## GET `/api/v1/analytics/defects`

List Defects

Return defects for a project with optional resolution status filter.

Source: [backend/app/routers/analytics.py:515](../../../backend/app/routers/analytics.py#L515).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`.

Declared Python handler arguments (includes exact role/guard options):

```python
project_id: str | None=None, resolution_status: str | None=None, page: int=Query(1, ge=1), size: int=Query(20, ge=1, le=100), db: AsyncSession=Depends(get_db), current_user: User=Depends(get_current_active_user)
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "list_defects_api_v1_analytics_defects_get",
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
      "name": "resolution_status",
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
        "title": "Resolution Status"
      }
    },
    {
      "in": "query",
      "name": "page",
      "required": false,
      "schema": {
        "default": 1,
        "minimum": 1,
        "title": "Page",
        "type": "integer"
      }
    },
    {
      "in": "query",
      "name": "size",
      "required": false,
      "schema": {
        "default": 20,
        "maximum": 100,
        "minimum": 1,
        "title": "Size",
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
await analytics_service.list_defects(db, str(scoped) if scoped else None, resolution_status, page, size, allowed_project_ids=allowed)
```

Contract limit: at least one response has an unstructured schema. Read the linked handler/serializer for emitted fields; the empty schema is not a promise of an empty JSON object.

## POST `/api/v1/analytics/defects`

Create Defect

Manual Defect Intake. Creates an OPEN defect for the project,
best-effort attaching to the most-recent matching TestCase when
``test_name`` (and optionally ``suite_name``) are supplied.

Source: [backend/app/routers/analytics.py:536](../../../backend/app/routers/analytics.py#L536).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_role.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
payload: DefectIntakeRequest, db: AsyncSession=Depends(get_db), current_user: User=Depends(get_current_active_user)
```

Direct handler error branches (dependency/service errors can add others):

```python
HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail='You do not have access to this project')
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "create_defect_api_v1_analytics_defects_post",
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
          "$ref": "#/components/schemas/DefectIntakeRequest"
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
            "$ref": "#/components/schemas/DefectIntakeResponse"
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

## GET `/api/v1/analytics/failure-categories`

Failure Categories

Return distribution of failure categories for AI-analysed test cases.

Source: [backend/app/routers/analytics.py:311](../../../backend/app/routers/analytics.py#L311).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`.

Declared Python handler arguments (includes exact role/guard options):

```python
project_id: str | None=None, days: int=Query(30, ge=1, le=365), suite_name: str | None=Query(None, min_length=1), release_id: str | None=Query(None, description='Scope to one release'), db: AsyncSession=Depends(get_db), current_user: User=Depends(get_current_active_user)
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "failure_categories_api_v1_analytics_failure_categories_get",
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
      "in": "query",
      "name": "suite_name",
      "required": false,
      "schema": {
        "anyOf": [
          {
            "minLength": 1,
            "type": "string"
          },
          {
            "type": "null"
          }
        ],
        "title": "Suite Name"
      }
    },
    {
      "description": "Scope to one release",
      "in": "query",
      "name": "release_id",
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
        "description": "Scope to one release",
        "title": "Release Id"
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
await analytics_service.failure_categories(db, str(scoped) if scoped else None, days, suite_name=suite_name, allowed_project_ids=allowed, release_id=release_id)
```

Contract limit: at least one response has an unstructured schema. Read the linked handler/serializer for emitted fields; the empty schema is not a promise of an empty JSON object.

## GET `/api/v1/analytics/flake-load`

Flake Load

What share of recent runs carried at least one retried test?

This is deliberately a **load**, not a debt burndown. Flaky-test insertion
rate tracks the fix rate even under sustained investment, so a "remaining"
count trending to zero is a promise that will never be kept — it will sit
near a floor forever and teach users the tool is broken rather than that
the target was wrong. A load has no implied zero: it is read against a
budget the team chooses, like an error budget.

Returns ``flake_load: null`` with a reason below the minimum run count,
since a share computed from three runs is noise wearing a percentage sign.

Source: [backend/app/routers/analytics.py:77](../../../backend/app/routers/analytics.py#L77).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`.

Declared Python handler arguments (includes exact role/guard options):

```python
project_id: str=Query(..., description='Project to measure — load is never a fleet average'), days: int=Query(30, ge=7, le=365), db: AsyncSession=Depends(get_db), current_user: User=Depends(get_current_active_user)
```

Direct handler error branches (dependency/service errors can add others):

```python
HTTPException(status_code=400, detail='Invalid project ID')
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "flake_load_api_v1_analytics_flake_load_get",
  "parameters": [
    {
      "description": "Project to measure — load is never a fleet average",
      "in": "query",
      "name": "project_id",
      "required": true,
      "schema": {
        "description": "Project to measure — load is never a fleet average",
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
        "minimum": 7,
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
await get_flake_load(db, scoped, window_days=days)
```

Contract limit: at least one response has an unstructured schema. Read the linked handler/serializer for emitted fields; the empty schema is not a promise of an empty JSON object.

## GET `/api/v1/analytics/flaky-scores`

Flaky Scores

Continuous 0–1 flakiness scores for this project, most flaky first.

Each row carries its **component breakdown and the weights used**, so the
number can be decomposed and recomputed — a score nobody can audit is one
users are asked to trust on faith.

``confidence`` is separate from ``score`` on purpose. A test seen 5 times
and one seen 500 can both produce 0.5; collapsing that distinction is how a
thin-history guess starts looking like a measurement. Fingerprints below the
evidence floor are not scored at all and simply do not appear here.

The response also carries this project's **suppression decision**: measured
classifier specificity swings from 100% to no-better-than-random across
projects, so how much authority a flaky verdict carries is a per-project
question. It is never "may act" — see the ``policy`` field.

Source: [backend/app/routers/analytics.py:109](../../../backend/app/routers/analytics.py#L109).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`.

Declared Python handler arguments (includes exact role/guard options):

```python
project_id: str=Query(..., description='Project to score — never a fleet average'), limit: int=Query(50, ge=1, le=200), release_id: str | None=Query(None, description='Only flaky tests that ran in this release'), db: AsyncSession=Depends(get_db), current_user: User=Depends(get_current_active_user)
```

Direct handler error branches (dependency/service errors can add others):

```python
HTTPException(status_code=400, detail='Invalid project ID')
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "flaky_scores_api_v1_analytics_flaky_scores_get",
  "parameters": [
    {
      "description": "Project to score — never a fleet average",
      "in": "query",
      "name": "project_id",
      "required": true,
      "schema": {
        "description": "Project to score — never a fleet average",
        "title": "Project Id",
        "type": "string"
      }
    },
    {
      "in": "query",
      "name": "limit",
      "required": false,
      "schema": {
        "default": 50,
        "maximum": 200,
        "minimum": 1,
        "title": "Limit",
        "type": "integer"
      }
    },
    {
      "description": "Only flaky tests that ran in this release",
      "in": "query",
      "name": "release_id",
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
        "description": "Only flaky tests that ran in this release",
        "title": "Release Id"
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
{'items': [{'test_fingerprint': row.test_fingerprint, 'test_name': row.test_name, 'score': row.score, 'components': row.components, 'weights': row.weights, 'observation_count': row.observation_count, 'confidence': row.confidence, 'computed_at': row.computed_at} for row in rows], 'total': len(rows), 'suppression': decision.to_dict(), 'scope': {'membership': 'release' if release_id else 'project', 'score': 'project_window', 'release_id': release_id, 'note': 'Scores are computed project-wide over the scoring window and are NOT recomputed per release: a single release rarely reaches the evidence floor a score needs. A release filter selects which already-scored tests ran in that release, not how flaky they were during it.' if release_id else None}}
```

Contract limit: at least one response has an unstructured schema. Read the linked handler/serializer for emitted fields; the empty schema is not a promise of an empty JSON object.

## GET `/api/v1/analytics/flaky-tests`

Flaky Tests

Return tests with highest flakiness rate (intermittent pass/fail pattern).

Source: [backend/app/routers/analytics.py:47](../../../backend/app/routers/analytics.py#L47).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`.

Declared Python handler arguments (includes exact role/guard options):

```python
project_id: str | None=None, days: int=Query(30, ge=1, le=365), limit: int=Query(20, ge=1, le=100), suite_name: str | None=Query(None, min_length=1), release_id: str | None=Query(None, description='Scope to one release'), db: AsyncSession=Depends(get_db), current_user: User=Depends(get_current_active_user)
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "flaky_tests_api_v1_analytics_flaky_tests_get",
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
      "in": "query",
      "name": "limit",
      "required": false,
      "schema": {
        "default": 20,
        "maximum": 100,
        "minimum": 1,
        "title": "Limit",
        "type": "integer"
      }
    },
    {
      "in": "query",
      "name": "suite_name",
      "required": false,
      "schema": {
        "anyOf": [
          {
            "minLength": 1,
            "type": "string"
          },
          {
            "type": "null"
          }
        ],
        "title": "Suite Name"
      }
    },
    {
      "description": "Scope to one release",
      "in": "query",
      "name": "release_id",
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
        "description": "Scope to one release",
        "title": "Release Id"
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
await analytics_service.flaky_tests(db, str(scoped) if scoped else None, days, limit, suite_name=suite_name, allowed_project_ids=allowed, release_id=release_id)
```

Contract limit: at least one response has an unstructured schema. Read the linked handler/serializer for emitted fields; the empty schema is not a promise of an empty JSON object.

## GET `/api/v1/analytics/kind-evidence`

Kind Evidence

Evidence-checklist record backing the AI-classified failure kind
(AI-4) — for the kind-badge popovers. Two lookup modes:

* ``project_id`` + ``test_fingerprint`` (the /failures aggregates carry
  fingerprints): resolves the fingerprint's most recent analyzed failure
  in the project.
* ``test_case_id`` (run-detail rows carry the id directly).

Uses the stored ``routing_metadata.kind_evidence`` blob when the
pipeline persisted one, computing on demand otherwise (no backfill).
Returns ``{found: false}`` when no analyzed failure matches — the UI
renders the plain badge then.

Source: [backend/app/routers/analytics.py:365](../../../backend/app/routers/analytics.py#L365).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`.

Declared Python handler arguments (includes exact role/guard options):

```python
project_id: str | None=None, test_fingerprint: str | None=Query(None, min_length=1), test_case_id: str | None=Query(None, min_length=1), db: AsyncSession=Depends(get_db), current_user: User=Depends(get_current_active_user)
```

Direct handler error branches (dependency/service errors can add others):

```python
HTTPException(status_code=422, detail='Invalid test_case_id format')
HTTPException(status_code=422, detail='Provide test_case_id, or project_id + test_fingerprint')
HTTPException(status_code=422, detail='project_id is required')
HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail='You do not have access to this project')
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "kind_evidence_api_v1_analytics_kind_evidence_get",
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
      "name": "test_fingerprint",
      "required": false,
      "schema": {
        "anyOf": [
          {
            "minLength": 1,
            "type": "string"
          },
          {
            "type": "null"
          }
        ],
        "title": "Test Fingerprint"
      }
    },
    {
      "in": "query",
      "name": "test_case_id",
      "required": false,
      "schema": {
        "anyOf": [
          {
            "minLength": 1,
            "type": "string"
          },
          {
            "type": "null"
          }
        ],
        "title": "Test Case Id"
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
{'found': False, 'test_case_id': None, 'kind_evidence': None}
{'found': evidence is not None, 'test_case_id': str(tc_id), 'kind_evidence': evidence}
{'found': evidence is not None, 'test_case_id': str(tc_uuid), 'kind_evidence': evidence}
```

Contract limit: at least one response has an unstructured schema. Read the linked handler/serializer for emitted fields; the empty schema is not a promise of an empty JSON object.

## POST `/api/v1/analytics/notify-owner`

Notify Suite Owner

Fire an email at the suite owner of ``payload.test_name`` so they can
triage the recurring failure. Resolution chain mirrors the suite-owner
review feature: explicit ``test_suite_owners`` row → ``Project.manager_user_id``.
Returns ``{queued: false, reason}`` when no owner can be resolved instead
of erroring, so the UI can show a clear actionable message.

Source: [backend/app/routers/analytics.py:604](../../../backend/app/routers/analytics.py#L604).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`.

Declared Python handler arguments (includes exact role/guard options):

```python
payload: NotifyTestOwnerRequest, db: AsyncSession=Depends(get_db), current_user: User=Depends(get_current_active_user)
```

Direct handler error branches (dependency/service errors can add others):

```python
HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail='You do not have access to this project')
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "notify_suite_owner_api_v1_analytics_notify_owner_post",
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
          "$ref": "#/components/schemas/NotifyTestOwnerRequest"
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
            "$ref": "#/components/schemas/NotifyTestOwnerResponse"
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

## GET `/api/v1/analytics/suite-detail`

Suite Detail

Return detailed breakdown for a single test suite:
  - Summary KPIs (unique tests, executions, pass rate, avg duration)
  - Per-test-case aggregates with flakiness flag
  - Last 10 test runs that included this suite

Source: [backend/app/routers/analytics.py:484](../../../backend/app/routers/analytics.py#L484).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`.

Declared Python handler arguments (includes exact role/guard options):

```python
project_id: str | None=None, suite_name: str='', days: int=Query(30, ge=1, le=365), release_id: str | None=Query(None, description='Scope to one release'), db: AsyncSession=Depends(get_db), current_user: User=Depends(get_current_active_user)
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "suite_detail_api_v1_analytics_suite_detail_get",
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
      "name": "suite_name",
      "required": false,
      "schema": {
        "default": "",
        "title": "Suite Name",
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
      "description": "Scope to one release",
      "in": "query",
      "name": "release_id",
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
        "description": "Scope to one release",
        "title": "Release Id"
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
await analytics_service.suite_detail(db, str(scoped) if scoped else None, suite_name, days, allowed_project_ids=allowed, release_id=release_id)
```

Contract limit: at least one response has an unstructured schema. Read the linked handler/serializer for emitted fields; the empty schema is not a promise of an empty JSON object.

## GET `/api/v1/analytics/systemic-clusters`

Systemic Clusters

Tests that fail TOGETHER across runs, with the shared cause named.

Most flaky failures are systemic rather than independent, so the useful
triage unit is the cluster: "these 14 tests flip together and it smells
like an external dependency" is one investigation where 14 individual
flags are 14.

**An empty list is a normal, frequent answer.** In the source study only 10
of 22 projects containing flaky tests contained any cluster at all. Callers
must render "no clusters" as a real result — never lower the bar until
something appears, and never present a weak grouping as a cluster, which
would send someone hunting a pattern that is not there.

``cause_family`` may be ``unknown``: a cluster is still actionable without
a named cause, and inventing one would be worse than admitting we cannot
tell from the failure text.

Source: [backend/app/routers/analytics.py:229](../../../backend/app/routers/analytics.py#L229).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`.

Declared Python handler arguments (includes exact role/guard options):

```python
project_id: str=Query(..., description='Project to read — clusters are per project'), db: AsyncSession=Depends(get_db), current_user: User=Depends(get_current_active_user)
```

Direct handler error branches (dependency/service errors can add others):

```python
HTTPException(status_code=400, detail='Invalid project ID')
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "systemic_clusters_api_v1_analytics_systemic_clusters_get",
  "parameters": [
    {
      "description": "Project to read — clusters are per project",
      "in": "query",
      "name": "project_id",
      "required": true,
      "schema": {
        "description": "Project to read — clusters are per project",
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
{'items': [{'cluster_key': cluster.cluster_key, 'label': cluster.label, 'cause_family': cluster.cause_family, 'size': cluster.size, 'cohesion': cluster.cohesion, 'co_failure_runs': cluster.co_failure_runs, 'window_days': cluster.window_days, 'computed_at': cluster.computed_at, 'members': [{'test_fingerprint': m.test_fingerprint, 'test_name': m.test_name, 'failure_runs': m.failure_runs} for m in members_by_cluster.get(cluster.id, [])]} for cluster in clusters], 'total': len(clusters), 'empty_is_normal': 'Most projects have no systemic clusters. An empty list means no group of tests met the co-failure cohesion bar, not that clustering failed.'}
```

Contract limit: at least one response has an unstructured schema. Read the linked handler/serializer for emitted fields; the empty schema is not a promise of an empty JSON object.

## GET `/api/v1/analytics/top-failing`

Top Failing Tests

Return tests with the highest total failure count in the period.

Source: [backend/app/routers/analytics.py:337](../../../backend/app/routers/analytics.py#L337).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`.

Declared Python handler arguments (includes exact role/guard options):

```python
project_id: str | None=None, days: int=Query(30, ge=1, le=365), limit: int=Query(15, ge=1, le=50), suite_name: str | None=Query(None, min_length=1), release_id: str | None=Query(None, description='Scope to one release'), db: AsyncSession=Depends(get_db), current_user: User=Depends(get_current_active_user)
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "top_failing_tests_api_v1_analytics_top_failing_get",
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
      "in": "query",
      "name": "limit",
      "required": false,
      "schema": {
        "default": 15,
        "maximum": 50,
        "minimum": 1,
        "title": "Limit",
        "type": "integer"
      }
    },
    {
      "in": "query",
      "name": "suite_name",
      "required": false,
      "schema": {
        "anyOf": [
          {
            "minLength": 1,
            "type": "string"
          },
          {
            "type": "null"
          }
        ],
        "title": "Suite Name"
      }
    },
    {
      "description": "Scope to one release",
      "in": "query",
      "name": "release_id",
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
        "description": "Scope to one release",
        "title": "Release Id"
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
await analytics_service.top_failing_tests(db, str(scoped) if scoped else None, days, limit, suite_name=suite_name, allowed_project_ids=allowed, release_id=release_id)
```

Contract limit: at least one response has an unstructured schema. Read the linked handler/serializer for emitted fields; the empty schema is not a promise of an empty JSON object.
