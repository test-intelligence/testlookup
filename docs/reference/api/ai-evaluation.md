# Ai Evaluation API

[Documentation home](../../README.md) · [Regeneration](../../maintainers/maintenance.md)

Generated from tracked source by `scripts/generate_reference.py`. Do not edit by hand. What the checks cover is recorded in [verification](../../maintainers/verification.md).

## POST `/api/v1/ai-eval/agent-stack-release-gate`

Run Agent Stack Release Gate

Run the release gate for prompt/model/routing changes across the agent stack.

The returned manifest checksum makes the gated change set auditable.

Source: [backend/app/routers/ai_evaluation.py:803](../../../backend/app/routers/ai_evaluation.py#L803).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_role.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
body: AgentStackReleaseGateRequest, current_user: User=Depends(require_role(UserRole.ADMIN)), db: AsyncSession=Depends(get_db)
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "run_agent_stack_release_gate_api_v1_ai_eval_agent_stack_release_gate_post",
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
          "$ref": "#/components/schemas/AgentStackReleaseGateRequest"
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
await evaluate_agent_stack_release_gate(db, change_id=body.change_id, prompt_versions=body.prompt_versions, model_versions=body.model_versions, routing_versions=body.routing_versions, required_gates=body.required_gates, evaluated_by=current_user.id, persist=body.persist)
```

Contract limit: at least one response has an unstructured schema. Read the linked handler/serializer for emitted fields; the empty schema is not a promise of an empty JSON object.

## GET `/api/v1/ai-eval/agent-stack-release-gate/runs`

List Agent Stack Gate Runs

List historical agent-stack release gate decisions, newest first, with ``total``.

Agent-stack rows only (``gate_type IS NULL``): the project-scoped G2 tier
comparisons share the table and are read per project through
``GET /tier-comparison/runs`` -- listing them here showed every QA lead
other projects' tier verdicts (W16 contract §0.4).

Source: [backend/app/routers/ai_evaluation.py:982](../../../backend/app/routers/ai_evaluation.py#L982).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_role.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
change_id: str | None=None, status_filter: str | None=Query(default=None, alias='status'), limit: int=Query(default=20, ge=1, le=100), current_user: User=Depends(require_role(UserRole.QA_LEAD)), db: AsyncSession=Depends(get_db)
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "list_agent_stack_gate_runs_api_v1_ai_eval_agent_stack_release_gate_runs_get",
  "parameters": [
    {
      "in": "query",
      "name": "change_id",
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
        "title": "Change Id"
      }
    },
    {
      "in": "query",
      "name": "status",
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
        "title": "Status"
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
            "$ref": "#/components/schemas/AIEvalGateRunListResponse"
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

## GET `/api/v1/ai-eval/baselines`

List Baselines

List active evaluation baselines.

Source: [backend/app/routers/ai_evaluation.py:1134](../../../backend/app/routers/ai_evaluation.py#L1134).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_role.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
task_type: str | None=None, current_user: User=Depends(require_role(UserRole.QA_LEAD)), db: AsyncSession=Depends(get_db)
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "list_baselines_api_v1_ai_eval_baselines_get",
  "parameters": [
    {
      "in": "query",
      "name": "task_type",
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
        "title": "Task Type"
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
[{'id': str(b.id), 'task_type': b.task_type, 'agent_name': b.agent_name, 'prompt_version': b.prompt_version, 'model_name': b.model_name, 'baseline_accuracy': b.baseline_accuracy, 'baseline_precision': b.baseline_precision, 'baseline_recall': b.baseline_recall, 'baseline_f1': b.baseline_f1, 'min_accuracy': b.min_accuracy, 'min_f1': b.min_f1, 'max_regression_pct': b.max_regression_pct, 'created_at': b.created_at.isoformat() if b.created_at else None, 'metric_kind': b.metric_kind or 'label_agreement', 'eval_run_id': str(b.eval_run_id) if b.eval_run_id else None, 'dataset_id': str(b.dataset_id) if b.dataset_id else None} for b in baselines]
```

Contract limit: at least one response has an unstructured schema. Read the linked handler/serializer for emitted fields; the empty schema is not a promise of an empty JSON object.

## POST `/api/v1/ai-eval/baselines`

Set Baseline

Set a baseline from a dataset evaluation (ADMIN only).

Computes metrics and stores them as the active baseline for the
specified agent/task_type. Deactivates any prior baseline.

Source: [backend/app/routers/ai_evaluation.py:1100](../../../backend/app/routers/ai_evaluation.py#L1100).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_role.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
body: SetBaselineRequest, current_user: User=Depends(require_role(UserRole.ADMIN)), db: AsyncSession=Depends(get_db)
```

Direct handler error branches (dependency/service errors can add others):

```python
HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "set_baseline_api_v1_ai_eval_baselines_post",
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
          "$ref": "#/components/schemas/SetBaselineRequest"
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
await set_baseline_from_eval(db, task_type=body.task_type, agent_name=body.agent_name, prompt_version=body.prompt_version, model_name=body.model_name, dataset_id=body.dataset_id, min_accuracy=body.min_accuracy, min_f1=body.min_f1, max_regression_pct=body.max_regression_pct, created_by=current_user.id, eval_run_id=body.eval_run_id)
```

Contract limit: at least one response has an unstructured schema. Read the linked handler/serializer for emitted fields; the empty schema is not a promise of an empty JSON object.

## GET `/api/v1/ai-eval/candidate-options`

Get Candidate Options

The tiers a candidate evaluation may replay, with the reason a tier is unavailable.

ADMIN (the Evaluate form is admin-only). Reads the effective AI config on
this request's session and the ML model's metadata; writes nothing.

Source: [backend/app/routers/ai_evaluation.py:415](../../../backend/app/routers/ai_evaluation.py#L415).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_role.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
current_user: User=Depends(require_role(UserRole.ADMIN)), db: AsyncSession=Depends(get_db)
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "get_candidate_options_api_v1_ai_eval_candidate_options_get",
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
          "schema": {
            "$ref": "#/components/schemas/CandidateOptionsResponse"
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

## GET `/api/v1/ai-eval/dashboard`

Get Quality Dashboard

Get the AI quality dashboard: agreement, drift, recent evals, model history.

Source: [backend/app/routers/ai_evaluation.py:64](../../../backend/app/routers/ai_evaluation.py#L64).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_role.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
days: int=Query(default=30, ge=1, le=365), current_user: User=Depends(require_role(UserRole.QA_LEAD)), db: AsyncSession=Depends(get_db)
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "get_quality_dashboard_api_v1_ai_eval_dashboard_get",
  "parameters": [
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
          "schema": {
            "$ref": "#/components/schemas/AIQualityDashboardResponse"
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

## GET `/api/v1/ai-eval/datasets`

List Datasets

List evaluation datasets.

Source: [backend/app/routers/ai_evaluation.py:127](../../../backend/app/routers/ai_evaluation.py#L127).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_role.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
task_type: str | None=None, current_user: User=Depends(require_role(UserRole.QA_LEAD)), db: AsyncSession=Depends(get_db)
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "list_datasets_api_v1_ai_eval_datasets_get",
  "parameters": [
    {
      "in": "query",
      "name": "task_type",
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
        "title": "Task Type"
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
              "$ref": "#/components/schemas/AIEvalDatasetResponse"
            },
            "title": "Response List Datasets Api V1 Ai Eval Datasets Get",
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

## POST `/api/v1/ai-eval/datasets`

Create Dataset

Create a new evaluation dataset (ADMIN only).

Source: [backend/app/routers/ai_evaluation.py:141](../../../backend/app/routers/ai_evaluation.py#L141).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_role.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
payload: AIEvalDatasetCreate, current_user: User=Depends(require_role(UserRole.ADMIN)), db: AsyncSession=Depends(get_db)
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "create_dataset_api_v1_ai_eval_datasets_post",
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
          "$ref": "#/components/schemas/AIEvalDatasetCreate"
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
            "$ref": "#/components/schemas/AIEvalDatasetResponse"
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

## POST `/api/v1/ai-eval/datasets/from-feedback`

Create Dataset From Feedback

Auto-generate a labeled dataset from existing human feedback (ADMIN only).

Source: [backend/app/routers/ai_evaluation.py:162](../../../backend/app/routers/ai_evaluation.py#L162).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_role.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
name: str='Auto-generated from feedback', task_type: str='classification', current_user: User=Depends(require_role(UserRole.ADMIN)), db: AsyncSession=Depends(get_db)
```

Direct handler error branches (dependency/service errors can add others):

```python
HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='No held-out feedback with eval-manifest provenance is available')
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "create_dataset_from_feedback_api_v1_ai_eval_datasets_from_feedback_post",
  "parameters": [
    {
      "in": "query",
      "name": "name",
      "required": false,
      "schema": {
        "default": "Auto-generated from feedback",
        "title": "Name",
        "type": "string"
      }
    },
    {
      "in": "query",
      "name": "task_type",
      "required": false,
      "schema": {
        "default": "classification",
        "title": "Task Type",
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
    "201": {
      "content": {
        "application/json": {
          "schema": {
            "$ref": "#/components/schemas/AIEvalDatasetResponse"
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

## DELETE `/api/v1/ai-eval/datasets/{dataset_id}`

Delete Dataset

Delete an evaluation dataset (ADMIN only).

Source: [backend/app/routers/ai_evaluation.py:193](../../../backend/app/routers/ai_evaluation.py#L193).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_role.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
dataset_id: uuid.UUID, current_user: User=Depends(require_role(UserRole.ADMIN)), db: AsyncSession=Depends(get_db)
```

Direct handler error branches (dependency/service errors can add others):

```python
HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Dataset not found')
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "delete_dataset_api_v1_ai_eval_datasets__dataset_id__delete",
  "parameters": [
    {
      "in": "path",
      "name": "dataset_id",
      "required": true,
      "schema": {
        "format": "uuid",
        "title": "Dataset Id",
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

## GET `/api/v1/ai-eval/drift`

Get Drift

Detect AI quality drift by comparing current vs previous eval windows.

Source: [backend/app/routers/ai_evaluation.py:552](../../../backend/app/routers/ai_evaluation.py#L552).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_role.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
task_type: str='classification', window_days: int=Query(default=7, ge=1, le=30), current_user: User=Depends(require_role(UserRole.QA_LEAD)), db: AsyncSession=Depends(get_db)
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "get_drift_api_v1_ai_eval_drift_get",
  "parameters": [
    {
      "in": "query",
      "name": "task_type",
      "required": false,
      "schema": {
        "default": "classification",
        "title": "Task Type",
        "type": "string"
      }
    },
    {
      "in": "query",
      "name": "window_days",
      "required": false,
      "schema": {
        "default": 7,
        "maximum": 30,
        "minimum": 1,
        "title": "Window Days",
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
await detect_quality_drift(db, task_type=task_type, window_days=window_days)
```

Contract limit: at least one response has an unstructured schema. Read the linked handler/serializer for emitted fields; the empty schema is not a promise of an empty JSON object.

## GET `/api/v1/ai-eval/gates/{checksum}`

Get Eval Manifest

Resolve the immutable eval manifest stamped on an agent pipeline run.

Source: [backend/app/routers/ai_evaluation.py:1085](../../../backend/app/routers/ai_evaluation.py#L1085).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_role.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
checksum: str, current_user: User=Depends(require_role(UserRole.QA_LEAD)), db: AsyncSession=Depends(get_db)
```

Direct handler error branches (dependency/service errors can add others):

```python
HTTPException(status_code=404, detail='Evaluation manifest not found')
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "get_eval_manifest_api_v1_ai_eval_gates__checksum__get",
  "parameters": [
    {
      "in": "path",
      "name": "checksum",
      "required": true,
      "schema": {
        "title": "Checksum",
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
            "$ref": "#/components/schemas/AIEvalManifestResponse"
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

## POST `/api/v1/ai-eval/golden-datasets/seed`

Seed Golden Datasets

Seed the golden reference datasets for all evaluation categories (ADMIN only).

Creates 4 golden datasets (classification, root_cause, duplicate_detection,
release_decision) if they don't already exist.

Source: [backend/app/routers/ai_evaluation.py:1169](../../../backend/app/routers/ai_evaluation.py#L1169).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_role.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
current_user: User=Depends(require_role(UserRole.ADMIN)), db: AsyncSession=Depends(get_db)
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "seed_golden_datasets_api_v1_ai_eval_golden_datasets_seed_post",
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
{'seeded': created, 'total': len(created)}
```

Contract limit: at least one response has an unstructured schema. Read the linked handler/serializer for emitted fields; the empty schema is not a promise of an empty JSON object.

## GET `/api/v1/ai-eval/label-health`

Get Training Label Health

Human-label coverage of the ML training pool (AI-F1).

Reports live counts per label-provenance bucket (human_direct /
human_indirect / llm_pseudo), the human-label floor, and the provenance
composition the deployed model was actually trained on — so the "learning
loop" claim is verifiable instead of implied.

Source: [backend/app/routers/ai_evaluation.py:107](../../../backend/app/routers/ai_evaluation.py#L107).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_role.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
current_user: User=Depends(require_role(UserRole.QA_LEAD)), db: AsyncSession=Depends(get_db)
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "get_training_label_health_api_v1_ai_eval_label_health_get",
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
await get_label_health(db)
```

Contract limit: at least one response has an unstructured schema. Read the linked handler/serializer for emitted fields; the empty schema is not a promise of an empty JSON object.

## POST `/api/v1/ai-eval/pre-release-gate`

Run Pre Release Gate

Run the pre-release evaluation gate for an agent.

Compares current metrics against baseline thresholds. Returns pass, fail,
or insufficient_samples
with per-rule results. Prompt, model, or routing changes should not ship
if the gate returns FAIL.

Source: [backend/app/routers/ai_evaluation.py:775](../../../backend/app/routers/ai_evaluation.py#L775).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_role.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
body: PreReleaseGateRequest, current_user: User=Depends(require_role(UserRole.ADMIN)), db: AsyncSession=Depends(get_db)
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "run_pre_release_gate_api_v1_ai_eval_pre_release_gate_post",
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
          "$ref": "#/components/schemas/PreReleaseGateRequest"
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
await evaluate_pre_release_gate(db, task_type=body.task_type, agent_name=body.agent_name, dataset_id=body.dataset_id, candidate_run_id=body.candidate_run_id)
```

Contract limit: at least one response has an unstructured schema. Read the linked handler/serializer for emitted fields; the empty schema is not a promise of an empty JSON object.

## GET `/api/v1/ai-eval/report-cycles`

List Report Eval Cycles

List durable report-level evaluation evidence.

Source: [backend/app/routers/ai_evaluation.py:602](../../../backend/app/routers/ai_evaluation.py#L602).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_role.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
corpus_version: str | None=Query(default=None, max_length=120), limit: int=Query(default=20, ge=1, le=100), current_user: User=Depends(require_role(UserRole.QA_LEAD)), db: AsyncSession=Depends(get_db)
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "list_report_eval_cycles_api_v1_ai_eval_report_cycles_get",
  "parameters": [
    {
      "in": "query",
      "name": "corpus_version",
      "required": false,
      "schema": {
        "anyOf": [
          {
            "maxLength": 120,
            "type": "string"
          },
          {
            "type": "null"
          }
        ],
        "title": "Corpus Version"
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
[_report_cycle_response(row) for row in rows]
```

Contract limit: at least one response has an unstructured schema. Read the linked handler/serializer for emitted fields; the empty schema is not a promise of an empty JSON object.

## POST `/api/v1/ai-eval/report-cycles`

Create Report Eval Cycle

Evaluate and persist one bounded report corpus cycle (ADMIN only).

Source: [backend/app/routers/ai_evaluation.py:641](../../../backend/app/routers/ai_evaluation.py#L641).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_role.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
payload: ReportEvalCycleRequest, current_user: User=Depends(require_role(UserRole.ADMIN)), db: AsyncSession=Depends(get_db)
```

Direct handler error branches (dependency/service errors can add others):

```python
HTTPException(status_code=422, detail=str(exc))
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "create_report_eval_cycle_api_v1_ai_eval_report_cycles_post",
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
          "$ref": "#/components/schemas/ReportEvalCycleRequest"
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
_report_cycle_response(row)
```

Contract limit: at least one response has an unstructured schema. Read the linked handler/serializer for emitted fields; the empty schema is not a promise of an empty JSON object.

## GET `/api/v1/ai-eval/report-cycles/readiness`

Get Report Eval Readiness

Return the fail-closed representative-corpus pilot readiness gate.

Source: [backend/app/routers/ai_evaluation.py:621](../../../backend/app/routers/ai_evaluation.py#L621).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_role.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
corpus_version: str=Query(..., min_length=1, max_length=120), current_user: User=Depends(require_role(UserRole.QA_LEAD)), db: AsyncSession=Depends(get_db)
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "get_report_eval_readiness_api_v1_ai_eval_report_cycles_readiness_get",
  "parameters": [
    {
      "in": "query",
      "name": "corpus_version",
      "required": true,
      "schema": {
        "maxLength": 120,
        "minLength": 1,
        "title": "Corpus Version",
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
assess_report_eval_readiness(rows)
```

Contract limit: at least one response has an unstructured schema. Read the linked handler/serializer for emitted fields; the empty schema is not a promise of an empty JSON object.

## POST `/api/v1/ai-eval/reviewer-quality`

Run Reviewer Quality

Run G3 and apply its guarded second-model retirement decision.

Source: [backend/app/routers/ai_evaluation.py:913](../../../backend/app/routers/ai_evaluation.py#L913).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_role.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
body: ReviewerQualityRequest, current_user: User=Depends(require_role(UserRole.QA_LEAD)), db: AsyncSession=Depends(get_db)
```

Direct handler error branches (dependency/service errors can add others):

```python
HTTPException(status_code=422, detail='G3 evaluates agent.reviewer.v1 only')
HTTPException(status_code=422, detail='auto_disable requires persist=true so the config change has durable evidence')
HTTPException(status_code=422, detail=str(exc))
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "run_reviewer_quality_api_v1_ai_eval_reviewer_quality_post",
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
          "$ref": "#/components/schemas/ReviewerQualityRequest"
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
result
```

Contract limit: at least one response has an unstructured schema. Read the linked handler/serializer for emitted fields; the empty schema is not a promise of an empty JSON object.

## GET `/api/v1/ai-eval/reviewer-quality/runs`

List Reviewer Quality Runs

The project's G3 reviewer-quality history, newest first, with ``total`` (W16).

READ-ONLY (the page never posts ``/reviewer-quality``, whose default
``auto_disable`` can retire the second model). The observation arrays are
never selected. Writes nothing.

Source: [backend/app/routers/ai_evaluation.py:1049](../../../backend/app/routers/ai_evaluation.py#L1049).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_role.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
project_id: uuid.UUID=Query(...), limit: int=Query(default=20, ge=1, le=100), current_user: User=Depends(require_role(UserRole.QA_LEAD)), db: AsyncSession=Depends(get_db)
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "list_reviewer_quality_runs_api_v1_ai_eval_reviewer_quality_runs_get",
  "parameters": [
    {
      "in": "query",
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
        "default": 20,
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
          "schema": {
            "$ref": "#/components/schemas/ReviewerQualityRunListResponse"
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

## GET `/api/v1/ai-eval/runs`

List Eval Runs

List evaluation runs, newest first, with the ``total`` of the filtered set.

W16: ``{items, total}`` (was a bare list no client read); ``metric_kind``
(legacy label agreement / replay) and ``status`` filter. Writes nothing.

Source: [backend/app/routers/ai_evaluation.py:212](../../../backend/app/routers/ai_evaluation.py#L212).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_role.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
dataset_id: uuid.UUID | None=None, task_type: str | None=None, metric_kind: str | None=Query(default=None, max_length=20), status_filter: str | None=Query(default=None, alias='status', max_length=20), limit: int=Query(default=20, ge=1, le=100), current_user: User=Depends(require_role(UserRole.QA_LEAD)), db: AsyncSession=Depends(get_db)
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "list_eval_runs_api_v1_ai_eval_runs_get",
  "parameters": [
    {
      "in": "query",
      "name": "dataset_id",
      "required": false,
      "schema": {
        "anyOf": [
          {
            "format": "uuid",
            "type": "string"
          },
          {
            "type": "null"
          }
        ],
        "title": "Dataset Id"
      }
    },
    {
      "in": "query",
      "name": "task_type",
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
        "title": "Task Type"
      }
    },
    {
      "in": "query",
      "name": "metric_kind",
      "required": false,
      "schema": {
        "anyOf": [
          {
            "maxLength": 20,
            "type": "string"
          },
          {
            "type": "null"
          }
        ],
        "title": "Metric Kind"
      }
    },
    {
      "in": "query",
      "name": "status",
      "required": false,
      "schema": {
        "anyOf": [
          {
            "maxLength": 20,
            "type": "string"
          },
          {
            "type": "null"
          }
        ],
        "title": "Status"
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
            "$ref": "#/components/schemas/AIEvalRunListResponse"
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

## POST `/api/v1/ai-eval/runs/evaluate/{dataset_id}`

Run Evaluation

Run evaluation against a dataset (ADMIN only).

No body: the LEGACY label-agreement evaluation, synchronous, 201 with the
run (unchanged). A ``candidate`` body (W16 AGT-09): a queued REPLAY of the
candidate through the real engine, 202 ``{run_id, status: "queued"}``.

Source: [backend/app/routers/ai_evaluation.py:258](../../../backend/app/routers/ai_evaluation.py#L258).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_role.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
dataset_id: uuid.UUID, body: Optional[CandidateEvaluationRequest]=None, current_user: User=Depends(require_role(UserRole.ADMIN)), db: AsyncSession=Depends(get_db)
```

Direct handler error branches (dependency/service errors can add others):

```python
HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Dataset not found')
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "run_evaluation_api_v1_ai_eval_runs_evaluate__dataset_id__post",
  "parameters": [
    {
      "in": "path",
      "name": "dataset_id",
      "required": true,
      "schema": {
        "format": "uuid",
        "title": "Dataset Id",
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
          "anyOf": [
            {
              "$ref": "#/components/schemas/CandidateEvaluationRequest"
            },
            {
              "type": "null"
            }
          ],
          "title": "Body"
        }
      }
    }
  },
  "responses": {
    "201": {
      "content": {
        "application/json": {
          "schema": {
            "$ref": "#/components/schemas/AIEvalRunResponse"
          }
        }
      },
      "description": "Successful Response"
    },
    "202": {
      "content": {
        "application/json": {
          "schema": {
            "$ref": "#/components/schemas/CandidateEvaluationAccepted"
          }
        }
      },
      "description": "Accepted"
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

## GET `/api/v1/ai-eval/runs/{eval_run_id}`

Get Eval Run

One evaluation run (legacy or replay) with its aggregate scores. Writes nothing.

Source: [backend/app/routers/ai_evaluation.py:437](../../../backend/app/routers/ai_evaluation.py#L437).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_role.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
eval_run_id: uuid.UUID, current_user: User=Depends(require_role(UserRole.QA_LEAD)), db: AsyncSession=Depends(get_db)
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "get_eval_run_api_v1_ai_eval_runs__eval_run_id__get",
  "parameters": [
    {
      "in": "path",
      "name": "eval_run_id",
      "required": true,
      "schema": {
        "format": "uuid",
        "title": "Eval Run Id",
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
            "$ref": "#/components/schemas/AIEvalRunResponse"
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

## GET `/api/v1/ai-eval/runs/{eval_run_id}/items`

List Eval Run Items

A replay run's per-item outputs in stored order (ADMIN only, contract D-4).

Items carry dataset text that a feedback-built dataset draws from every
project, so QA leads see aggregates only. Inputs, expected values and
outputs are re-sanitized here. A legacy run stored no items. Writes nothing.

Source: [backend/app/routers/ai_evaluation.py:498](../../../backend/app/routers/ai_evaluation.py#L498).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_role.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
eval_run_id: uuid.UUID, status_filter: str | None=Query(default=None, alias='status', max_length=20), limit: int=Query(default=25, ge=1, le=100), offset: int=Query(default=0, ge=0), current_user: User=Depends(require_role(UserRole.ADMIN)), db: AsyncSession=Depends(get_db)
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "list_eval_run_items_api_v1_ai_eval_runs__eval_run_id__items_get",
  "parameters": [
    {
      "in": "path",
      "name": "eval_run_id",
      "required": true,
      "schema": {
        "format": "uuid",
        "title": "Eval Run Id",
        "type": "string"
      }
    },
    {
      "in": "query",
      "name": "status",
      "required": false,
      "schema": {
        "anyOf": [
          {
            "maxLength": 20,
            "type": "string"
          },
          {
            "type": "null"
          }
        ],
        "title": "Status"
      }
    },
    {
      "in": "query",
      "name": "limit",
      "required": false,
      "schema": {
        "default": 25,
        "maximum": 100,
        "minimum": 1,
        "title": "Limit",
        "type": "integer"
      }
    },
    {
      "in": "query",
      "name": "offset",
      "required": false,
      "schema": {
        "default": 0,
        "minimum": 0,
        "title": "Offset",
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
            "$ref": "#/components/schemas/AIEvalItemListResponse"
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

## GET `/api/v1/ai-eval/shadow-pairs`

List Shadow Pairs

The project's live shadow pairs, newest first (QA lead+, interactive login only).

The QUERY project is checked before anything is read; ``agent_id`` and
``label_status`` (``all`` = every status) filter INSIDE it. Outputs are
re-sanitized; each pair's source is resolved inside its project. Writes nothing.

Source: [backend/app/routers/ai_evaluation.py:1236](../../../backend/app/routers/ai_evaluation.py#L1236).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_role.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
project_id: uuid.UUID=Query(...), agent_id: str | None=Query(default=None, max_length=80), label_status: str=Query(default='pending', max_length=20), limit: int=Query(default=25, ge=1, le=100), offset: int=Query(default=0, ge=0), current_user: User=Depends(require_role(UserRole.QA_LEAD)), db: AsyncSession=Depends(get_db)
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "list_shadow_pairs_api_v1_ai_eval_shadow_pairs_get",
  "parameters": [
    {
      "in": "query",
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
      "name": "agent_id",
      "required": false,
      "schema": {
        "anyOf": [
          {
            "maxLength": 80,
            "type": "string"
          },
          {
            "type": "null"
          }
        ],
        "title": "Agent Id"
      }
    },
    {
      "in": "query",
      "name": "label_status",
      "required": false,
      "schema": {
        "default": "pending",
        "maxLength": 20,
        "title": "Label Status",
        "type": "string"
      }
    },
    {
      "in": "query",
      "name": "limit",
      "required": false,
      "schema": {
        "default": 25,
        "maximum": 100,
        "minimum": 1,
        "title": "Limit",
        "type": "integer"
      }
    },
    {
      "in": "query",
      "name": "offset",
      "required": false,
      "schema": {
        "default": 0,
        "minimum": 0,
        "title": "Offset",
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
            "$ref": "#/components/schemas/ShadowPairListResponse"
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

## POST `/api/v1/ai-eval/shadow-pairs/{shadow_pair_id}/label`

Label Shadow Pair

Label one live shadow pair with a person's verdict (QA lead+, interactive login only).

Every check runs before anything is written; the label, ``labelled_by``
and the activity row share ONE commit. A label is final (no relabel).

Source: [backend/app/routers/ai_evaluation.py:1285](../../../backend/app/routers/ai_evaluation.py#L1285).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_role.<locals>._check`, `require_shadow_pair_access.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
body: ShadowPairLabelRequest, current_user: User=Depends(require_role(UserRole.QA_LEAD)), pair: Any=Depends(require_shadow_pair_access()), db: AsyncSession=Depends(get_db)
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "label_shadow_pair_api_v1_ai_eval_shadow_pairs__shadow_pair_id__label_post",
  "parameters": [
    {
      "in": "path",
      "name": "shadow_pair_id",
      "required": true,
      "schema": {
        "format": "uuid",
        "title": "Shadow Pair Id",
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
          "$ref": "#/components/schemas/ShadowPairLabelRequest"
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
            "$ref": "#/components/schemas/ShadowPairRead"
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

## POST `/api/v1/ai-eval/tier-comparison`

Run Tier Comparison

Run G2 against paired golden outputs and optionally retain the decision.

Source: [backend/app/routers/ai_evaluation.py:842](../../../backend/app/routers/ai_evaluation.py#L842).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_role.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
body: TierComparisonRequest, current_user: User=Depends(require_role(UserRole.QA_LEAD)), db: AsyncSession=Depends(get_db)
```

Direct handler error branches (dependency/service errors can add others):

```python
HTTPException(status_code=422, detail=str(exc))
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "run_tier_comparison_api_v1_ai_eval_tier_comparison_post",
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
          "$ref": "#/components/schemas/TierComparisonRequest"
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
result
```

Contract limit: at least one response has an unstructured schema. Read the linked handler/serializer for emitted fields; the empty schema is not a promise of an empty JSON object.

## GET `/api/v1/ai-eval/tier-comparison/runs`

List Tier Comparison Runs

The project's recorded G2 tier comparisons, newest first, with ``total`` (W16).

The QUERY project is checked like a body project before anything is read;
``agent_id`` filters INSIDE that project. Writes nothing.

Source: [backend/app/routers/ai_evaluation.py:1019](../../../backend/app/routers/ai_evaluation.py#L1019).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_role.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
project_id: uuid.UUID=Query(...), agent_id: str | None=Query(default=None, max_length=80), limit: int=Query(default=20, ge=1, le=100), current_user: User=Depends(require_role(UserRole.QA_LEAD)), db: AsyncSession=Depends(get_db)
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "list_tier_comparison_runs_api_v1_ai_eval_tier_comparison_runs_get",
  "parameters": [
    {
      "in": "query",
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
      "name": "agent_id",
      "required": false,
      "schema": {
        "anyOf": [
          {
            "maxLength": 80,
            "type": "string"
          },
          {
            "type": "null"
          }
        ],
        "title": "Agent Id"
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
            "$ref": "#/components/schemas/AIEvalGateRunListResponse"
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
