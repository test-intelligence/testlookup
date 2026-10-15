# Execution Metrics API

[Documentation home](../../README.md) · [Regeneration](../../maintainers/maintenance.md)

Generated from tracked source by `scripts/generate_reference.py`. Do not edit by hand. What the checks cover is recorded in [verification](../../maintainers/verification.md).

## GET `/api/v1/analytics/execution-metrics`

Get Execution Metrics

EXEC-03: the project's execution time per UTC day (wall clock and summed), p50/p95 per suite against the
previous window, the biggest suite regressions (median of the last 5 runs >= 1.5x the 20 before, same branch)
and test regressions (window p50 vs the duration baseline, >= 10 samples), the slowest tests (by p95) and the
duration histogram. ``suite_name`` (repeatable) narrows every block and switches ``per_day`` to the suite
grain.

Source: [backend/app/routers/execution_metrics.py:114](../../../backend/app/routers/execution_metrics.py#L114).

Dependency chain: `OAuth2PasswordBearer`, `_gate_dependency.<locals>.analytics_gate`, `analytics_scope.<locals>.dependency`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`.

Declared Python handler arguments (includes exact role/guard options):

```python
branch: Optional[str]=Query(None, description=_BRANCH_DESCRIPTION), top: int=Query(svc.METRICS_DEFAULT_TOP, description=f'Size of slowest_tests and of each regressions list, 1-{svc.METRICS_MAX_TOP}.'), suite_limit: int=Query(svc.METRICS_DEFAULT_SUITE_LIMIT, description=f'Size of the per-suite table, 1-{svc.METRICS_MAX_SUITE_LIMIT}.'), scope: AnalyticsScope=Depends(analytics_scope(_EXECUTION_METRICS)), db: AsyncSession=Depends(get_db)
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "get_execution_metrics_api_v1_analytics_execution_metrics_get",
  "parameters": [
    {
      "description": "Only runs of this branch (exact, case-sensitive, 1-255 characters). Absent or empty: every branch.",
      "in": "query",
      "name": "branch",
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
        "description": "Only runs of this branch (exact, case-sensitive, 1-255 characters). Absent or empty: every branch.",
        "title": "Branch"
      }
    },
    {
      "description": "Size of slowest_tests and of each regressions list, 1-100.",
      "in": "query",
      "name": "top",
      "required": false,
      "schema": {
        "default": 20,
        "description": "Size of slowest_tests and of each regressions list, 1-100.",
        "title": "Top",
        "type": "integer"
      }
    },
    {
      "description": "Size of the per-suite table, 1-200.",
      "in": "query",
      "name": "suite_limit",
      "required": false,
      "schema": {
        "default": 50,
        "description": "Size of the per-suite table, 1-200.",
        "title": "Suite Limit",
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
          "schema": {
            "$ref": "#/components/schemas/ExecutionMetricsResponse"
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

## GET `/api/v1/analytics/execution/suite-history`

Get Suite Execution History

EXEC-03: one suite's wall clock and summed test time per run over the window (oldest first), with window
p50/p95 over every execution in it. ``suite_name`` is required and single-valued here (matched trimmed and
case-insensitively); ``release_id`` filters the runs.

Source: [backend/app/routers/execution_metrics.py:83](../../../backend/app/routers/execution_metrics.py#L83).

Dependency chain: `OAuth2PasswordBearer`, `_gate_dependency.<locals>.analytics_gate`, `analytics_scope.<locals>.dependency`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`.

Declared Python handler arguments (includes exact role/guard options):

```python
branch: Optional[str]=Query(None, description=_BRANCH_DESCRIPTION), limit: int=Query(svc.SUITE_HISTORY_DEFAULT_LIMIT, description=f'Max points, 1-{svc.SUITE_HISTORY_MAX_LIMIT} (the newest are kept).'), scope: AnalyticsScope=Depends(analytics_scope(_SUITE_HISTORY)), db: AsyncSession=Depends(get_db)
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "get_suite_execution_history_api_v1_analytics_execution_suite_history_get",
  "parameters": [
    {
      "description": "Only runs of this branch (exact, case-sensitive, 1-255 characters). Absent or empty: every branch.",
      "in": "query",
      "name": "branch",
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
        "description": "Only runs of this branch (exact, case-sensitive, 1-255 characters). Absent or empty: every branch.",
        "title": "Branch"
      }
    },
    {
      "description": "Max points, 1-500 (the newest are kept).",
      "in": "query",
      "name": "limit",
      "required": false,
      "schema": {
        "default": 100,
        "description": "Max points, 1-500 (the newest are kept).",
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
      "description": "Window in days, 1-365.",
      "in": "query",
      "name": "days",
      "required": false,
      "schema": {
        "default": 90,
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
          "schema": {
            "$ref": "#/components/schemas/SuiteExecutionHistoryResponse"
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

## GET `/api/v1/runs/{run_id}/timing`

Get Run Timing

EXEC-03: how long the run took and where the time went.

Wall clock vs summed test time and parallelism, phase totals, the run's suite executions, the slowest tests
with their duration baselines, worker lanes, a timeline of at most ``timeline_limit`` tests for the waterfall,
and the run's duration regressions (the ``/duration-regressions`` body). A figure the report did not carry is
``null`` with ``measured: false`` and a ``reason`` on its block -- e.g. ``timeline.reason`` "report carried no
timestamps" for a JUnit run -- never 0. Read-only.

Source: [backend/app/routers/execution_metrics.py:56](../../../backend/app/routers/execution_metrics.py#L56).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_run_access.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
run_id: uuid.UUID, slowest: int=Query(svc.DEFAULT_SLOWEST, ge=1, le=svc.MAX_SLOWEST, description='How many slowest tests to return.'), timeline_limit: int=Query(svc.DEFAULT_TIMELINE_LIMIT, ge=1, le=svc.MAX_TIMELINE_LIMIT, description='Max timeline bars; the LONGEST tests are kept when more qualify (timeline.truncated).'), db: AsyncSession=Depends(get_db), _: User=Depends(require_run_access())
```

Direct handler error branches (dependency/service errors can add others):

```python
HTTPException(status_code=404, detail='Test run not found')
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "get_run_timing_api_v1_runs__run_id__timing_get",
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
      "description": "How many slowest tests to return.",
      "in": "query",
      "name": "slowest",
      "required": false,
      "schema": {
        "default": 20,
        "description": "How many slowest tests to return.",
        "maximum": 100,
        "minimum": 1,
        "title": "Slowest",
        "type": "integer"
      }
    },
    {
      "description": "Max timeline bars; the LONGEST tests are kept when more qualify (timeline.truncated).",
      "in": "query",
      "name": "timeline_limit",
      "required": false,
      "schema": {
        "default": 2000,
        "description": "Max timeline bars; the LONGEST tests are kept when more qualify (timeline.truncated).",
        "maximum": 2000,
        "minimum": 1,
        "title": "Timeline Limit",
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
          "schema": {
            "$ref": "#/components/schemas/RunTimingResponse"
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
