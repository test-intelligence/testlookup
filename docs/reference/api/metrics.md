# Metrics API

[Documentation home](../../README.md) · [Regeneration](../../handoff/maintenance.md)

Generated from tracked source by `scripts/generate_handoff_reference.py`. Do not edit by hand. The baseline and validation limits are recorded in [verification](../../handoff/verification.md).

## GET `/api/v1/metrics/detection-timing`

Detection Timing

How long does this project wait to learn a test is flaky? (Roadmap Phase 6.)

Two-tier detection: a short-cadence screen of new and directly-modified
fingerprints, plus a nightly pass over the whole corpus for the
environment- and dependency-induced flakiness a diff cannot reach.

The cadence is expressed in **time**, not commits. A commit-count cadence
assumes a commit range on most runs and enough of them to count; the Phase 0
census measured zero of four genuine projects on the reference deployment
clearing that, so such a cadence would simply never fire here.

The number worth reading is ``bottleneck``. On a thin corpus, detection is
limited by how often tests *run*, not by how often they are screened — a
score needs observations before it is defensible — and this says which term
dominates from measured numbers rather than assuming the flattering one.

Latency is reported only over fingerprints whose first appearance was
actually observed; the rest are counted and excluded, not backfilled to a
zero that would read as instant detection.

``project_id`` is REQUIRED: detection latency is a claim about one
project's own history, so a fleet average would be meaningless.

Source: [backend/app/routers/metrics.py:189](../../../backend/app/routers/metrics.py#L189).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`.

Declared Python handler arguments (includes exact role/guard options):

```python
project_id: str=Query(..., description='Project to measure — detection latency is never a fleet average'), days: int=Query(30, ge=7, le=365), db: AsyncSession=Depends(get_db), current_user: User=Depends(get_current_active_user)
```

Direct handler error branches (dependency/service errors can add others):

```python
HTTPException(status_code=422, detail='project_id must be a UUID')
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "detection_timing_api_v1_metrics_detection_timing_get",
  "parameters": [
    {
      "description": "Project to measure — detection latency is never a fleet average",
      "in": "query",
      "name": "project_id",
      "required": true,
      "schema": {
        "description": "Project to measure — detection latency is never a fleet average",
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
await measure_detection_timing(db, pid, window_days=days)
{'project_id': project_id, 'available': False, 'insufficient_data_reason': 'no test results are visible for this project'}
```

Contract limit: at least one response has an unstructured schema. Read the linked handler/serializer for emitted fields; the empty schema is not a promise of an empty JSON object.

## GET `/api/v1/metrics/flaky-readiness`

Flaky Readiness

Does this project have enough history to score flakiness at all?
(Roadmap Phase 0 gate on the continuous-score work.)

The published evidence for probabilistic flakiness scoring comes from
hyperscale monorepos; academic open-source corpora report per-test flake
rates about an order of magnitude lower. A moving-window posterior needs
runs *per fingerprint* to update a prior with — a test seen three times can
only produce a number that is mostly prior, which is fabricated confidence
wearing a decimal point.

So this reports how many fingerprints clear the per-fingerprint run
threshold, the median runs per test, and a plain ``available`` verdict with
``insufficient_data_reason`` when the corpus is too thin — the same honesty
contract as ``/metrics/tia-readiness``.

``project_id`` is REQUIRED: corpus depth is a claim about one project's own
history, so a fleet average would be meaningless.

Source: [backend/app/routers/metrics.py:146](../../../backend/app/routers/metrics.py#L146).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`.

Declared Python handler arguments (includes exact role/guard options):

```python
project_id: str=Query(..., description='Project to measure — readiness is never a fleet average'), days: int=Query(90, ge=7, le=365), db: AsyncSession=Depends(get_db), current_user: User=Depends(get_current_active_user)
```

Direct handler error branches (dependency/service errors can add others):

```python
HTTPException(status_code=422, detail='project_id must be a UUID')
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "flaky_readiness_api_v1_metrics_flaky_readiness_get",
  "parameters": [
    {
      "description": "Project to measure — readiness is never a fleet average",
      "in": "query",
      "name": "project_id",
      "required": true,
      "schema": {
        "description": "Project to measure — readiness is never a fleet average",
        "title": "Project Id",
        "type": "string"
      }
    },
    {
      "in": "query",
      "name": "days",
      "required": false,
      "schema": {
        "default": 90,
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
await get_flaky_readiness(db, pid, window_days=days)
{'project_id': project_id, 'available': False, 'insufficient_data_reason': 'no test results are visible for this project'}
```

Contract limit: at least one response has an unstructured schema. Read the linked handler/serializer for emitted fields; the empty schema is not a promise of an empty JSON object.

## GET `/api/v1/metrics/summary`

Dashboard Summary

Return aggregated KPI metrics for the Executive Dashboard.

Source: [backend/app/routers/metrics.py:55](../../../backend/app/routers/metrics.py#L55).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`.

Declared Python handler arguments (includes exact role/guard options):

```python
project_id: str | None=None, days: int=Query(7, ge=1, le=90), suite_name: str | None=Query(None, min_length=1), release_id: str | None=Query(None, description='Scope to one release'), db: AsyncSession=Depends(get_db), current_user: User=Depends(get_current_active_user)
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "dashboard_summary_api_v1_metrics_summary_get",
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
        "default": 7,
        "maximum": 90,
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
await get_dashboard_summary(db, project_id, days, suite_name=suite_name, release_id=release_id)
{}
```

Contract limit: at least one response has an unstructured schema. Read the linked handler/serializer for emitted fields; the empty schema is not a promise of an empty JSON object.

## GET `/api/v1/metrics/tia-readiness`

Tia Readiness

Is this project's commit-range corpus big enough to train test-impact
analysis on yet? (Epic 10 go/no-go.)

Counts the runs whose commit range actually resolved AND carries
per-commit changed files, the span they cover, and the distinct paths
seen. Returns ``available: false`` with a concrete
``insufficient_data_reason`` until every published threshold is met —
the same honesty contract as the value-metrics headline gate.

``project_id`` is REQUIRED: readiness is a claim about one project's own
change/failure history, so there is no meaningful all-projects rollup.

Source: [backend/app/routers/metrics.py:109](../../../backend/app/routers/metrics.py#L109).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`.

Declared Python handler arguments (includes exact role/guard options):

```python
project_id: str=Query(..., description='Project to measure — readiness is never a fleet average'), days: int=Query(90, ge=7, le=365), db: AsyncSession=Depends(get_db), current_user: User=Depends(get_current_active_user)
```

Direct handler error branches (dependency/service errors can add others):

```python
HTTPException(status_code=422, detail='project_id must be a UUID')
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "tia_readiness_api_v1_metrics_tia_readiness_get",
  "parameters": [
    {
      "description": "Project to measure — readiness is never a fleet average",
      "in": "query",
      "name": "project_id",
      "required": true,
      "schema": {
        "description": "Project to measure — readiness is never a fleet average",
        "title": "Project Id",
        "type": "string"
      }
    },
    {
      "in": "query",
      "name": "days",
      "required": false,
      "schema": {
        "default": 90,
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
await get_tia_readiness(db, pid, days=days)
{'project_id': project_id, 'available': False, 'insufficient_data_reason': 'no commit ranges have been resolved for this project yet'}
```

Contract limit: at least one response has an unstructured schema. Read the linked handler/serializer for emitted fields; the empty schema is not a promise of an empty JSON object.

## GET `/api/v1/metrics/trends`

Trend Data

Return daily pass/fail/skip breakdown for trend charts.

Source: [backend/app/routers/metrics.py:86](../../../backend/app/routers/metrics.py#L86).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`.

Declared Python handler arguments (includes exact role/guard options):

```python
project_id: str | None=None, days: int=Query(7, ge=1, le=90), suite_name: str | None=Query(None, min_length=1), release_id: str | None=Query(None, description='Scope to one release'), db: AsyncSession=Depends(get_db), current_user: User=Depends(get_current_active_user)
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "trend_data_api_v1_metrics_trends_get",
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
        "default": 7,
        "maximum": 90,
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
{'data': [], 'period_days': days}
{'data': data, 'period_days': days}
```

Contract limit: at least one response has an unstructured schema. Read the linked handler/serializer for emitted fields; the empty schema is not a promise of an empty JSON object.
