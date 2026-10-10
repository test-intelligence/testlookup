# Analytics API

[Documentation home](../../README.md) · [Regeneration](../../handoff/maintenance.md)

Generated from tracked source by `scripts/generate_handoff_reference.py`. Do not edit by hand. The baseline and validation limits are recorded in [verification](../../handoff/verification.md).

## GET `/api/v1/analytics/ai-summary`

Ai Analysis Summary

Return summary of AI analysis results for the project.

VIZ-202: ``release_id`` / ``suite_name`` (repeatable) count the analyses
of executions in those releases (run's primary release) and suites
(effective suite).

Source: [backend/app/routers/analytics.py:686](../../../backend/app/routers/analytics.py#L686).

Dependency chain: `OAuth2PasswordBearer`, `analytics_scope.<locals>.dependency`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`.

Declared Python handler arguments (includes exact role/guard options):

```python
scope: AnalyticsScope=Depends(analytics_scope(_AI_SUMMARY)), db: AsyncSession=Depends(get_db)
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "ai_analysis_summary_api_v1_analytics_ai_summary_get",
  "parameters": [
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
with_meta(payload, await build_meta(db, scope))
```

Contract limit: at least one response has an unstructured schema. Read the linked handler/serializer for emitted fields; the empty schema is not a promise of an empty JSON object.

## GET `/api/v1/analytics/chart-data`

Chart Data

A metric over up to two dimensions, as contract C3 ``chart_series``.

Series are keyed by the second ``group_by`` and zero-filled over the first,
so the table view and CSV export need no transformation. Each point carries
``y`` and ``n`` (the sample behind it); a rate with nothing evaluated is
``y: null`` with ``measured: false`` and a reason, never 0.

Source: [backend/app/routers/analytics.py:934](../../../backend/app/routers/analytics.py#L934).

Dependency chain: `OAuth2PasswordBearer`, `_gate_dependency.<locals>.analytics_gate`, `analytics_scope.<locals>.dependency`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`.

Declared Python handler arguments (includes exact role/guard options):

```python
metric: str=Query('executions', description='What to measure. One of: ' + ', '.join(sorted(chart_data_service.METRICS))), group_by: Optional[list[str]]=Query(None, description='The axis, and optionally a second dimension to key the series by (at most two, the time dimension first). One of: ' + ', '.join(sorted(chart_data_service.DIMENSIONS))), top_n: Optional[int]=Query(None, description="Keep the largest N keys and merge the rest into an 'other' bucket. Required for group_by=test outside a single suite."), scope: AnalyticsScope=Depends(analytics_scope(_CHART_DATA)), db: AsyncSession=Depends(get_db)
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "chart_data_api_v1_analytics_chart_data_get",
  "parameters": [
    {
      "description": "What to measure. One of: broken, duration_p50, duration_p95, duration_total, executions, failed, failure_rate, failures, flaky_tests, pass_rate, passed, retried_tests, run_count, skipped, unique_tests, unknown",
      "in": "query",
      "name": "metric",
      "required": false,
      "schema": {
        "default": "executions",
        "description": "What to measure. One of: broken, duration_p50, duration_p95, duration_total, executions, failed, failure_rate, failures, flaky_tests, pass_rate, passed, retried_tests, run_count, skipped, unique_tests, unknown",
        "title": "Metric",
        "type": "string"
      }
    },
    {
      "description": "The axis, and optionally a second dimension to key the series by (at most two, the time dimension first). One of: branch, day, environment, failure_category, ingestion_source, project, release, status, suite, test, week",
      "in": "query",
      "name": "group_by",
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
        "description": "The axis, and optionally a second dimension to key the series by (at most two, the time dimension first). One of: branch, day, environment, failure_category, ingestion_source, project, release, status, suite, test, week",
        "title": "Group By"
      }
    },
    {
      "description": "Keep the largest N keys and merge the rest into an 'other' bucket. Required for group_by=test outside a single suite.",
      "in": "query",
      "name": "top_n",
      "required": false,
      "schema": {
        "anyOf": [
          {
            "type": "integer"
          },
          {
            "type": "null"
          }
        ],
        "description": "Keep the largest N keys and merge the rest into an 'other' bucket. Required for group_by=test outside a single suite.",
        "title": "Top N"
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
```

Contract limit: at least one response has an unstructured schema. Read the linked handler/serializer for emitted fields; the empty schema is not a promise of an empty JSON object.

## GET `/api/v1/analytics/chart-data/rows`

Chart Data Rows

One page of the test executions behind a chart mark, newest first.

``reconciliation`` names the mark field these rows add up to (``y`` for a
count, ``n`` for a rate or a duration) and the number to compare with it;
``meta.definitions.chart_grain`` says when the chart counted run
aggregates, which can hold runs with no per-test rows.

Source: [backend/app/routers/analytics_chart_rows.py:45](../../../backend/app/routers/analytics_chart_rows.py#L45).

Dependency chain: `OAuth2PasswordBearer`, `_gate_dependency.<locals>.analytics_gate`, `analytics_scope.<locals>.dependency`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`.

Declared Python handler arguments (includes exact role/guard options):

```python
metric: str=Query('executions', description="The chart's metric; it decides which executions count. One of: " + ', '.join(sorted(chart_data_service.METRICS))), group_by: Optional[list[str]]=Query(None, description="The chart's dimensions (at most two). One of: " + ', '.join(rows_service.ROWS_DIMENSIONS)), top_n: Optional[int]=Query(None, description="The chart's top_n, when it had one."), bucket_day: Optional[str]=_bucket('day'), bucket_week: Optional[str]=_bucket('week'), bucket_project: Optional[str]=_bucket('project'), bucket_release: Optional[str]=_bucket('release'), bucket_suite: Optional[str]=_bucket('suite'), bucket_status: Optional[str]=_bucket('status'), bucket_failure_category: Optional[str]=_bucket('failure_category'), bucket_branch: Optional[str]=_bucket('branch'), bucket_environment: Optional[str]=_bucket('environment'), bucket_ingestion_source: Optional[str]=_bucket('ingestion_source'), bucket_test: Optional[str]=_bucket('test'), bucket_error_signature: Optional[str]=_bucket('error_signature'), page: int=Query(1, description='From 1.'), size: int=Query(rows_service.DEFAULT_PAGE_SIZE, description=f'1-{rows_service.MAX_PAGE_SIZE}; (page - 1) x size <= {rows_service.MAX_OFFSET}.'), scope: AnalyticsScope=Depends(analytics_scope(_ROWS)), db: AsyncSession=Depends(get_db)
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "chart_data_rows_api_v1_analytics_chart_data_rows_get",
  "parameters": [
    {
      "description": "The chart's metric; it decides which executions count. One of: broken, duration_p50, duration_p95, duration_total, executions, failed, failure_rate, failures, flaky_tests, pass_rate, passed, retried_tests, run_count, skipped, unique_tests, unknown",
      "in": "query",
      "name": "metric",
      "required": false,
      "schema": {
        "default": "executions",
        "description": "The chart's metric; it decides which executions count. One of: broken, duration_p50, duration_p95, duration_total, executions, failed, failure_rate, failures, flaky_tests, pass_rate, passed, retried_tests, run_count, skipped, unique_tests, unknown",
        "title": "Metric",
        "type": "string"
      }
    },
    {
      "description": "The chart's dimensions (at most two). One of: day, week, project, release, suite, status, failure_category, branch, environment, ingestion_source, test, error_signature",
      "in": "query",
      "name": "group_by",
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
        "description": "The chart's dimensions (at most two). One of: day, week, project, release, suite, status, failure_category, branch, environment, ingestion_source, test, error_signature",
        "title": "Group By"
      }
    },
    {
      "description": "The chart's top_n, when it had one.",
      "in": "query",
      "name": "top_n",
      "required": false,
      "schema": {
        "anyOf": [
          {
            "type": "integer"
          },
          {
            "type": "null"
          }
        ],
        "description": "The chart's top_n, when it had one.",
        "title": "Top N"
      }
    },
    {
      "description": "The chart's key for the selected day bucket. Allowed only when day is in group_by.",
      "in": "query",
      "name": "bucket_day",
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
        "description": "The chart's key for the selected day bucket. Allowed only when day is in group_by.",
        "title": "Bucket Day"
      }
    },
    {
      "description": "The chart's key for the selected week bucket. Allowed only when week is in group_by.",
      "in": "query",
      "name": "bucket_week",
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
        "description": "The chart's key for the selected week bucket. Allowed only when week is in group_by.",
        "title": "Bucket Week"
      }
    },
    {
      "description": "The chart's key for the selected project bucket. Allowed only when project is in group_by.",
      "in": "query",
      "name": "bucket_project",
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
        "description": "The chart's key for the selected project bucket. Allowed only when project is in group_by.",
        "title": "Bucket Project"
      }
    },
    {
      "description": "The chart's key for the selected release bucket. Allowed only when release is in group_by.",
      "in": "query",
      "name": "bucket_release",
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
        "description": "The chart's key for the selected release bucket. Allowed only when release is in group_by.",
        "title": "Bucket Release"
      }
    },
    {
      "description": "The chart's key for the selected suite bucket. Allowed only when suite is in group_by.",
      "in": "query",
      "name": "bucket_suite",
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
        "description": "The chart's key for the selected suite bucket. Allowed only when suite is in group_by.",
        "title": "Bucket Suite"
      }
    },
    {
      "description": "The chart's key for the selected status bucket. Allowed only when status is in group_by.",
      "in": "query",
      "name": "bucket_status",
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
        "description": "The chart's key for the selected status bucket. Allowed only when status is in group_by.",
        "title": "Bucket Status"
      }
    },
    {
      "description": "The chart's key for the selected failure_category bucket. Allowed only when failure_category is in group_by.",
      "in": "query",
      "name": "bucket_failure_category",
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
        "description": "The chart's key for the selected failure_category bucket. Allowed only when failure_category is in group_by.",
        "title": "Bucket Failure Category"
      }
    },
    {
      "description": "The chart's key for the selected branch bucket. Allowed only when branch is in group_by.",
      "in": "query",
      "name": "bucket_branch",
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
        "description": "The chart's key for the selected branch bucket. Allowed only when branch is in group_by.",
        "title": "Bucket Branch"
      }
    },
    {
      "description": "The chart's key for the selected environment bucket. Allowed only when environment is in group_by.",
      "in": "query",
      "name": "bucket_environment",
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
        "description": "The chart's key for the selected environment bucket. Allowed only when environment is in group_by.",
        "title": "Bucket Environment"
      }
    },
    {
      "description": "The chart's key for the selected ingestion_source bucket. Allowed only when ingestion_source is in group_by.",
      "in": "query",
      "name": "bucket_ingestion_source",
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
        "description": "The chart's key for the selected ingestion_source bucket. Allowed only when ingestion_source is in group_by.",
        "title": "Bucket Ingestion Source"
      }
    },
    {
      "description": "The chart's key for the selected test bucket. Allowed only when test is in group_by.",
      "in": "query",
      "name": "bucket_test",
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
        "description": "The chart's key for the selected test bucket. Allowed only when test is in group_by.",
        "title": "Bucket Test"
      }
    },
    {
      "description": "The chart's key for the selected error_signature bucket. Allowed only when error_signature is in group_by.",
      "in": "query",
      "name": "bucket_error_signature",
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
        "description": "The chart's key for the selected error_signature bucket. Allowed only when error_signature is in group_by.",
        "title": "Bucket Error Signature"
      }
    },
    {
      "description": "From 1.",
      "in": "query",
      "name": "page",
      "required": false,
      "schema": {
        "default": 1,
        "description": "From 1.",
        "title": "Page",
        "type": "integer"
      }
    },
    {
      "description": "1-200; (page - 1) x size <= 10000.",
      "in": "query",
      "name": "size",
      "required": false,
      "schema": {
        "default": 50,
        "description": "1-200; (page - 1) x size <= 10000.",
        "title": "Size",
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

Source: [backend/app/routers/analytics.py:800](../../../backend/app/routers/analytics.py#L800).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_role.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
payload: ClassifyUncategorizedRequest, db: AsyncSession=Depends(get_db), current_user: User=Depends(require_role(UserRole.QA_ENGINEER))
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

Source: [backend/app/routers/analytics.py:543](../../../backend/app/routers/analytics.py#L543).

Dependency chain: `OAuth2PasswordBearer`, `_gate_dependency.<locals>.analytics_gate`, `analytics_scope.<locals>.dependency`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`.

Declared Python handler arguments (includes exact role/guard options):

```python
scope: AnalyticsScope=Depends(analytics_scope(_WINDOWED)), db: AsyncSession=Depends(get_db)
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "coverage_stats_api_v1_analytics_coverage_get",
  "parameters": [
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
with_meta(payload, await build_meta(db, scope, pass_rate_basis=PASS_RATE_BASIS_EXECUTIONS, truncated=suite_count > shown, truncated_total=suite_count))
```

Contract limit: at least one response has an unstructured schema. Read the linked handler/serializer for emitted fields; the empty schema is not a promise of an empty JSON object.

## GET `/api/v1/analytics/coverage-map`

Coverage Map

One level of the coverage map as contract C3 ``tree``: the parent as the
single root, its children (at most 499, the rest in one "Other (n)" node),
each with the ``stats`` block. Test-execution coverage, not code coverage.

Source: [backend/app/routers/analytics_coverage_map.py:37](../../../backend/app/routers/analytics_coverage_map.py#L37).

Dependency chain: `OAuth2PasswordBearer`, `_gate_dependency.<locals>.analytics_gate`, `analytics_scope.<locals>.dependency`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`.

Declared Python handler arguments (includes exact role/guard options):

```python
depth: int=Query(1, description='1: the suites. 2: the classes (or files) of one suite. 3: the tests of one class.'), suite: Optional[str]=Query(None, description="depth 2 and 3: the suite KEY, as the level-1 node id carries it after its 's:' prefix. Echoed, never parsed."), class_key: Optional[str]=Query(None, description=f"depth 3: the class KEY, as the level-2 node id carries it after 'c:<suite key>{coverage_map_service.KEY_SEPARATOR}' ('{coverage_map_service.NO_CLASS_KEY}' for tests with no class)."), scope: AnalyticsScope=Depends(analytics_scope(COVERAGE_MAP_POLICY)), db: AsyncSession=Depends(get_db)
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "coverage_map_api_v1_analytics_coverage_map_get",
  "parameters": [
    {
      "description": "1: the suites. 2: the classes (or files) of one suite. 3: the tests of one class.",
      "in": "query",
      "name": "depth",
      "required": false,
      "schema": {
        "default": 1,
        "description": "1: the suites. 2: the classes (or files) of one suite. 3: the tests of one class.",
        "title": "Depth",
        "type": "integer"
      }
    },
    {
      "description": "depth 2 and 3: the suite KEY, as the level-1 node id carries it after its 's:' prefix. Echoed, never parsed.",
      "in": "query",
      "name": "suite",
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
        "description": "depth 2 and 3: the suite KEY, as the level-1 node id carries it after its 's:' prefix. Echoed, never parsed.",
        "title": "Suite"
      }
    },
    {
      "description": "depth 3: the class KEY, as the level-2 node id carries it after 'c:<suite key>␟' ('__none__' for tests with no class).",
      "in": "query",
      "name": "class_key",
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
        "description": "depth 3: the class KEY, as the level-2 node id carries it after 'c:<suite key>␟' ('__none__' for tests with no class).",
        "title": "Class Key"
      }
    },
    {
      "description": "Project to read. Single-valued. Required: without it the request is refused with 422 'missing_parameter' (All Projects is not supported here).",
      "in": "query",
      "name": "project_id",
      "required": true,
      "schema": {
        "description": "Project to read. Single-valued. Required: without it the request is refused with 422 'missing_parameter' (All Projects is not supported here).",
        "title": "Project Id",
        "type": "string"
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
```

Contract limit: at least one response has an unstructured schema. Read the linked handler/serializer for emitted fields; the empty schema is not a promise of an empty JSON object.

## GET `/api/v1/analytics/defects`

List Defects

Return defects for a project with optional resolution status filter.

VIZ-202: ``release_id`` / ``suite_name`` (repeatable) keep the defects
whose originating execution ran in one of the releases (the run's primary
release) and suites (effective suite). A defect with no linked execution
cannot be placed and is left out while either filter is set. The list has
no time window (``meta.ignored_filters``): open defects of any age.

Source: [backend/app/routers/analytics.py:602](../../../backend/app/routers/analytics.py#L602).

Dependency chain: `OAuth2PasswordBearer`, `analytics_scope.<locals>.dependency`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`.

Declared Python handler arguments (includes exact role/guard options):

```python
resolution_status: str | None=None, page: int=Query(1, ge=1), size: int=Query(20, ge=1, le=100), scope: AnalyticsScope=Depends(analytics_scope(_DEFECTS)), db: AsyncSession=Depends(get_db)
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "list_defects_api_v1_analytics_defects_get",
  "parameters": [
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
with_meta(payload, await build_meta(db, scope, measured=True))
```

Contract limit: at least one response has an unstructured schema. Read the linked handler/serializer for emitted fields; the empty schema is not a promise of an empty JSON object.

## POST `/api/v1/analytics/defects`

Create Defect

Manual Defect Intake. Creates an OPEN defect for the project,
best-effort attaching to the most-recent matching TestCase when
``test_name`` (and optionally ``suite_name``) are supplied.

Source: [backend/app/routers/analytics.py:633](../../../backend/app/routers/analytics.py#L633).

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

Source: [backend/app/routers/analytics.py:405](../../../backend/app/routers/analytics.py#L405).

Dependency chain: `OAuth2PasswordBearer`, `_gate_dependency.<locals>.analytics_gate`, `analytics_scope.<locals>.dependency`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`.

Declared Python handler arguments (includes exact role/guard options):

```python
scope: AnalyticsScope=Depends(analytics_scope(_WINDOWED)), db: AsyncSession=Depends(get_db)
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "failure_categories_api_v1_analytics_failure_categories_get",
  "parameters": [
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
with_meta(payload, await build_meta(db, scope))
```

Contract limit: at least one response has an unstructured schema. Read the linked handler/serializer for emitted fields; the empty schema is not a promise of an empty JSON object.

## GET `/api/v1/analytics/failure-groups`

Failure Groups

Failing executions grouped by error signature, as contract C3 ``graph``.

``nodes`` are the largest groups (size = failures, ``group`` = dominant
failure category); ``groups`` carries their counts, share, categories,
trend and top tests in rank order; ``no_message`` and ``singletons`` are
roll-ups, not nodes. ``edges`` is empty unless ``include=edges``. Labels
are raw error text: untrusted, render as text.

Source: [backend/app/routers/analytics_failure_groups.py:34](../../../backend/app/routers/analytics_failure_groups.py#L34).

Dependency chain: `OAuth2PasswordBearer`, `_gate_dependency.<locals>.analytics_gate`, `analytics_scope.<locals>.dependency`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`.

Declared Python handler arguments (includes exact role/guard options):

```python
include: Optional[list[str]]=Query(None, description='Optional parts of the answer (repeatable). One of: ' + ', '.join(sorted(failure_groups_service.INCLUDE_TOKENS)) + ". 'edges' links groups that fail in the same tests."), scope: AnalyticsScope=Depends(analytics_scope(_FAILURE_GROUPS)), db: AsyncSession=Depends(get_db)
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "failure_groups_api_v1_analytics_failure_groups_get",
  "parameters": [
    {
      "description": "Optional parts of the answer (repeatable). One of: edges. 'edges' links groups that fail in the same tests.",
      "in": "query",
      "name": "include",
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
        "description": "Optional parts of the answer (repeatable). One of: edges. 'edges' links groups that fail in the same tests.",
        "title": "Include"
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

Source: [backend/app/routers/analytics.py:129](../../../backend/app/routers/analytics.py#L129).

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

Source: [backend/app/routers/analytics.py:161](../../../backend/app/routers/analytics.py#L161).

Dependency chain: `OAuth2PasswordBearer`, `_gate_dependency.<locals>.analytics_gate`, `analytics_scope.<locals>.dependency`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`.

Declared Python handler arguments (includes exact role/guard options):

```python
limit: int=Query(50, ge=1, le=200), scope: AnalyticsScope=Depends(analytics_scope(_FLAKY_SCORES)), db: AsyncSession=Depends(get_db)
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
      "description": "Project to read. Single-valued. Required: without it the request is refused with 422 'missing_parameter' (All Projects is not supported here).",
      "in": "query",
      "name": "project_id",
      "required": true,
      "schema": {
        "description": "Project to read. Single-valued. Required: without it the request is refused with 422 'missing_parameter' (All Projects is not supported here).",
        "title": "Project Id",
        "type": "string"
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
{'meta': meta, 'items': [{'test_fingerprint': row.test_fingerprint, 'test_name': row.test_name, 'score': row.score, 'components': row.components, 'weights': row.weights, 'observation_count': row.observation_count, 'confidence': row.confidence, 'computed_at': row.computed_at} for row in rows], 'total': len(rows), 'suppression': decision.to_dict(), 'scope': {'membership': _membership_note(scope, 'Scores')['membership'], 'score': 'project_window', 'release_id': list(release_id) if isinstance(release_id, tuple) else release_id, 'note': 'Scores are computed project-wide over the scoring window and are NOT recomputed per release: a single release rarely reaches the evidence floor a score needs. A release filter selects which already-scored tests ran in that release, not how flaky they were during it.' if release_id and (not scope.suite_names) else _membership_note(scope, 'Scores')['note']}}
```

Contract limit: at least one response has an unstructured schema. Read the linked handler/serializer for emitted fields; the empty schema is not a promise of an empty JSON object.

## GET `/api/v1/analytics/flaky-tests`

Flaky Tests

Return tests with highest flakiness rate (intermittent pass/fail pattern).

Source: [backend/app/routers/analytics.py:105](../../../backend/app/routers/analytics.py#L105).

Dependency chain: `OAuth2PasswordBearer`, `_gate_dependency.<locals>.analytics_gate`, `analytics_scope.<locals>.dependency`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`.

Declared Python handler arguments (includes exact role/guard options):

```python
limit: int=Query(20, ge=1, le=100), scope: AnalyticsScope=Depends(analytics_scope(_WINDOWED)), db: AsyncSession=Depends(get_db)
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "flaky_tests_api_v1_analytics_flaky_tests_get",
  "parameters": [
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
with_meta(payload, await build_meta(db, scope))
```

Contract limit: at least one response has an unstructured schema. Read the linked handler/serializer for emitted fields; the empty schema is not a promise of an empty JSON object.

## GET `/api/v1/analytics/heatmap`

Heatmap

A two-dimensional read as contract C3 ``matrix``.

Rate kinds carry the pass rate in percentage points (``unit: "percent"``),
with the five status counts behind each cell; a cell with nothing
evaluated is ``null``, never 0. ``test_run`` carries each execution's
status. Rows and columns beyond the caps are dropped and counted in
``meta.truncated_axes``.

Source: [backend/app/routers/analytics_heatmap.py:45](../../../backend/app/routers/analytics_heatmap.py#L45).

Dependency chain: `OAuth2PasswordBearer`, `_gate_dependency.<locals>.analytics_gate`, `analytics_scope.<locals>.dependency`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`.

Declared Python handler arguments (includes exact role/guard options):

```python
kind: Optional[str]=Query(None, description='Which matrix. One of: ' + ', '.join(heatmap_service.KINDS) + '. ' + ' and '.join((f'kind={kind}' for kind in sorted(heatmap_service.PROJECT_KINDS))) + " answer for one project: without project_id they are refused with 422 'project_required'. The other kinds take All Projects."), rows: Optional[int]=Query(None, description=f'Rows to keep, worst first (default {heatmap_service.DEFAULT_ROWS}, 1-{heatmap_service.MAX_ROWS}).'), runs: Optional[int]=Query(None, description=f'kind=test_run only: the last N runs as columns (default {heatmap_service.DEFAULT_RUNS}, 1-{heatmap_service.MAX_RUNS}).'), scope: AnalyticsScope=Depends(analytics_scope(heatmap_service.HEATMAP_SCOPE)), db: AsyncSession=Depends(get_db)
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "heatmap_api_v1_analytics_heatmap_get",
  "parameters": [
    {
      "description": "Which matrix. One of: suite_day, test_run, suite_environment, suite_release. kind=suite_release and kind=test_run answer for one project: without project_id they are refused with 422 'project_required'. The other kinds take All Projects.",
      "in": "query",
      "name": "kind",
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
        "description": "Which matrix. One of: suite_day, test_run, suite_environment, suite_release. kind=suite_release and kind=test_run answer for one project: without project_id they are refused with 422 'project_required'. The other kinds take All Projects.",
        "title": "Kind"
      }
    },
    {
      "description": "Rows to keep, worst first (default 40, 1-60).",
      "in": "query",
      "name": "rows",
      "required": false,
      "schema": {
        "anyOf": [
          {
            "type": "integer"
          },
          {
            "type": "null"
          }
        ],
        "description": "Rows to keep, worst first (default 40, 1-60).",
        "title": "Rows"
      }
    },
    {
      "description": "kind=test_run only: the last N runs as columns (default 30, 1-90).",
      "in": "query",
      "name": "runs",
      "required": false,
      "schema": {
        "anyOf": [
          {
            "type": "integer"
          },
          {
            "type": "null"
          }
        ],
        "description": "kind=test_run only: the last N runs as columns (default 30, 1-90).",
        "title": "Runs"
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

Source: [backend/app/routers/analytics.py:450](../../../backend/app/routers/analytics.py#L450).

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

Source: [backend/app/routers/analytics.py:712](../../../backend/app/routers/analytics.py#L712).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_role.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
payload: NotifyTestOwnerRequest, db: AsyncSession=Depends(get_db), current_user: User=Depends(require_role(UserRole.QA_ENGINEER))
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

Return detailed breakdown for a test suite (several ``suite_name`` values
return their union, and ``suite_name`` in the response is then the list):
  - Summary KPIs (unique tests, executions, pass rate, avg duration)
  - Per-test-case aggregates with flakiness flag
  - Last 10 test runs that included this suite
No ``suite_name`` matches nothing (``suite_name: ""``), as before.

Source: [backend/app/routers/analytics.py:571](../../../backend/app/routers/analytics.py#L571).

Dependency chain: `OAuth2PasswordBearer`, `_gate_dependency.<locals>.analytics_gate`, `analytics_scope.<locals>.dependency`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`.

Declared Python handler arguments (includes exact role/guard options):

```python
scope: AnalyticsScope=Depends(analytics_scope(_WINDOWED)), db: AsyncSession=Depends(get_db)
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "suite_detail_api_v1_analytics_suite_detail_get",
  "parameters": [
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
with_meta(payload, await build_meta(db, scope, pass_rate_basis=PASS_RATE_BASIS_EXECUTIONS))
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

VIZ-202: ``release_id`` / ``suite_name`` (repeatable) select the MEMBERS
that ran in scope -- a member is kept when it has an execution in one of
the releases and suites -- and a cluster with no member left is dropped.
The cluster's own statistics (size, cohesion, co-failure runs) are
project-level and are not recomputed; ``scope`` in the response says so.
Clusters have no time window (``meta.ignored_filters``).

Source: [backend/app/routers/analytics.py:282](../../../backend/app/routers/analytics.py#L282).

Dependency chain: `OAuth2PasswordBearer`, `_gate_dependency.<locals>.analytics_gate`, `analytics_scope.<locals>.dependency`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`.

Declared Python handler arguments (includes exact role/guard options):

```python
scope: AnalyticsScope=Depends(analytics_scope(_CLUSTERS)), db: AsyncSession=Depends(get_db)
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
      "description": "Project to read. Single-valued. Required: without it the request is refused with 422 'missing_parameter' (All Projects is not supported here).",
      "in": "query",
      "name": "project_id",
      "required": true,
      "schema": {
        "description": "Project to read. Single-valued. Required: without it the request is refused with 422 'missing_parameter' (All Projects is not supported here).",
        "title": "Project Id",
        "type": "string"
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
with_meta(payload, await build_meta(db, scope, measured=True))
```

Contract limit: at least one response has an unstructured schema. Read the linked handler/serializer for emitted fields; the empty schema is not a promise of an empty JSON object.

## GET `/api/v1/analytics/test-scatter`

Get Test Scatter

p95 duration (ms, log x) x failure rate (%, y) x executions (size), one
point per test; tests that cannot be placed are counted in ``excluded``.

Source: [backend/app/routers/analytics_test_scatter.py:39](../../../backend/app/routers/analytics_test_scatter.py#L39).

Dependency chain: `OAuth2PasswordBearer`, `_gate_dependency.<locals>.analytics_gate`, `analytics_scope.<locals>.dependency`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`.

Declared Python handler arguments (includes exact role/guard options):

```python
min_executions: int=Query(scatter_service.DEFAULT_MIN_EXECUTIONS, description=f'Tests with fewer executions in scope are left out and counted (1-{scatter_service.MAX_MIN_EXECUTIONS}).'), limit: int=Query(scatter_service.DEFAULT_LIMIT, description=f'At most this many points (1-{scatter_service.MAX_LIMIT}).'), order: str=Query(scatter_service.DEFAULT_ORDER, description='Which tests are kept when more qualify: ' + ', '.join(scatter_service.ORDERS)), scope: AnalyticsScope=Depends(analytics_scope(_SCATTER)), db: AsyncSession=Depends(get_db)
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "get_test_scatter_api_v1_analytics_test_scatter_get",
  "parameters": [
    {
      "description": "Tests with fewer executions in scope are left out and counted (1-1000).",
      "in": "query",
      "name": "min_executions",
      "required": false,
      "schema": {
        "default": 5,
        "description": "Tests with fewer executions in scope are left out and counted (1-1000).",
        "title": "Min Executions",
        "type": "integer"
      }
    },
    {
      "description": "At most this many points (1-5000).",
      "in": "query",
      "name": "limit",
      "required": false,
      "schema": {
        "default": 2000,
        "description": "At most this many points (1-5000).",
        "title": "Limit",
        "type": "integer"
      }
    },
    {
      "description": "Which tests are kept when more qualify: failures, volume",
      "in": "query",
      "name": "order",
      "required": false,
      "schema": {
        "default": "failures",
        "description": "Which tests are kept when more qualify: failures, volume",
        "title": "Order",
        "type": "string"
      }
    },
    {
      "description": "Project to read. Single-valued. Required: without it the request is refused with 422 'missing_parameter' (All Projects is not supported here).",
      "in": "query",
      "name": "project_id",
      "required": true,
      "schema": {
        "description": "Project to read. Single-valued. Required: without it the request is refused with 422 'missing_parameter' (All Projects is not supported here).",
        "title": "Project Id",
        "type": "string"
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
```

Contract limit: at least one response has an unstructured schema. Read the linked handler/serializer for emitted fields; the empty schema is not a promise of an empty JSON object.

## GET `/api/v1/analytics/top-failing`

Top Failing Tests

Return tests with the highest total failure count in the period.

Source: [backend/app/routers/analytics.py:426](../../../backend/app/routers/analytics.py#L426).

Dependency chain: `OAuth2PasswordBearer`, `_gate_dependency.<locals>.analytics_gate`, `analytics_scope.<locals>.dependency`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`.

Declared Python handler arguments (includes exact role/guard options):

```python
limit: int=Query(15, ge=1, le=50), scope: AnalyticsScope=Depends(analytics_scope(_WINDOWED)), db: AsyncSession=Depends(get_db)
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "top_failing_tests_api_v1_analytics_top_failing_get",
  "parameters": [
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
with_meta(payload, await build_meta(db, scope))
```

Contract limit: at least one response has an unstructured schema. Read the linked handler/serializer for emitted fields; the empty schema is not a promise of an empty JSON object.
