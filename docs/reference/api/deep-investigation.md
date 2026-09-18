# Deep Investigation API

[Documentation home](../../README.md) · [Regeneration](../../handoff/maintenance.md)

Generated from tracked source by `scripts/generate_handoff_reference.py`. Do not edit by hand. The baseline and validation limits are recorded in [verification](../../handoff/verification.md).

## GET `/api/v1/deep-investigate/defects/pending-review`

List Pending Defects

List defects awaiting approval in the caller's projects (QA Lead+, paginated).

``require_role(QA_LEAD)`` gates by ROLE, not by project membership, and this
query previously had no project filter at all — so a QA lead of one project
received the titles, components and owner teams of pending defects belonging
to every OTHER project on the deployment.

``get_accessible_project_ids()`` returning ``None`` means ADMIN, who is
legitimately unscoped; the defect was that non-admins were unscoped too.
``defects.project_id`` is indexed (``ix_defects_project_id``), so the schema
already anticipated this filter.

Source: [backend/app/routers/deep_investigation.py:424](../../../backend/app/routers/deep_investigation.py#L424).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_role.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
page: int=Query(1, ge=1, description='Page number'), page_size: int=Query(50, ge=1, le=100, description='Items per page'), current_user: User=Depends(require_role(UserRole.QA_LEAD, allow_project_key=True)), db: AsyncSession=Depends(get_db)
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "list_pending_defects_api_v1_deep_investigate_defects_pending_review_get",
  "parameters": [
    {
      "description": "Page number",
      "in": "query",
      "name": "page",
      "required": false,
      "schema": {
        "default": 1,
        "description": "Page number",
        "minimum": 1,
        "title": "Page",
        "type": "integer"
      }
    },
    {
      "description": "Items per page",
      "in": "query",
      "name": "page_size",
      "required": false,
      "schema": {
        "default": 50,
        "description": "Items per page",
        "maximum": 100,
        "minimum": 1,
        "title": "Page Size",
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
[{'defect_id': str(d.id), 'title': d.title, 'severity': d.severity, 'component': d.component, 'owner_team': d.owner_team, 'created_at': d.created_at.isoformat() if d.created_at else None, 'policy_reasons': (d.policy_evaluation or {}).get('policy_reasons', [])} for d in defects]
```

Contract limit: at least one response has an unstructured schema. Read the linked handler/serializer for emitted fields; the empty schema is not a promise of an empty JSON object.

## POST `/api/v1/deep-investigate/defects/{defect_id}/review`

Review Defect

Approve or reject a defect that is pending review (QA Lead+ only).

When approved with a Jira project key, the Jira ticket is created.

Source: [backend/app/routers/deep_investigation.py:326](../../../backend/app/routers/deep_investigation.py#L326).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_role.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
defect_id: uuid.UUID, body: DefectApprovalRequest, current_user: User=Depends(require_role(UserRole.QA_LEAD, allow_project_key=True)), db: AsyncSession=Depends(get_db)
```

Direct handler error branches (dependency/service errors can add others):

```python
HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Defect not found or not in pending_review status.')
HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="action must be 'approve' or 'reject'")
HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail='Rejection reason is required.')
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "review_defect_api_v1_deep_investigate_defects__defect_id__review_post",
  "parameters": [
    {
      "in": "path",
      "name": "defect_id",
      "required": true,
      "schema": {
        "format": "uuid",
        "title": "Defect Id",
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
          "$ref": "#/components/schemas/DefectApprovalRequest"
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
            "$ref": "#/components/schemas/DefectApprovalResponse"
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

## POST `/api/v1/deep-investigate/{run_id}`

Trigger Deep Investigation

Trigger the deep investigation pipeline for a completed test run.
Uses workflow_type="deep" which adds failure clustering, flaky sentinel,
test health analysis, and release risk on top of the standard 5-stage pipeline.

Source: [backend/app/routers/deep_investigation.py:89](../../../backend/app/routers/deep_investigation.py#L89).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_run_access.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
run_id: uuid.UUID, body: TriggerDeepRequest, current_user: User=Depends(get_current_active_user), _: User=Depends(require_run_access())
```

Direct handler error branches (dependency/service errors can add others):

```python
HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Test run not found')
HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail='Deep investigation is disabled. Enable it in Settings > AI Configuration.')
HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail='Deep investigation requires LLM or Auto mode. Current mode: rules.')
HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc))
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "trigger_deep_investigation_api_v1_deep_investigate__run_id__post",
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
  "requestBody": {
    "content": {
      "application/json": {
        "schema": {
          "$ref": "#/components/schemas/TriggerDeepRequest"
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
            "$ref": "#/components/schemas/TriggerDeepResponse"
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

## GET `/api/v1/deep-investigate/{run_id}/clusters`

Get Failure Clusters

Return semantic failure clusters for a test run.

Source: [backend/app/routers/deep_investigation.py:143](../../../backend/app/routers/deep_investigation.py#L143).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_run_access.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
run_id: uuid.UUID, current_user: User=Depends(require_run_access())
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "get_failure_clusters_api_v1_deep_investigate__run_id__clusters_get",
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
          "schema": {
            "items": {
              "$ref": "#/components/schemas/ClusterResponse"
            },
            "title": "Response Get Failure Clusters Api V1 Deep Investigate  Run Id  Clusters Get",
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

## GET `/api/v1/deep-investigate/{run_id}/clusters/ranked`

Get Ranked Clusters

Return failure clusters ranked by impact score for triage prioritization.

Source: [backend/app/routers/deep_investigation.py:268](../../../backend/app/routers/deep_investigation.py#L268).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_run_access.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
run_id: uuid.UUID, current_user: User=Depends(require_run_access()), db: AsyncSession=Depends(get_db)
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "get_ranked_clusters_api_v1_deep_investigate__run_id__clusters_ranked_get",
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
{'items': [], 'total': 0}
{'items': ranked, 'total': len(ranked)}
```

Contract limit: at least one response has an unstructured schema. Read the linked handler/serializer for emitted fields; the empty schema is not a promise of an empty JSON object.

## GET `/api/v1/deep-investigate/{run_id}/clusters/{cluster_id}/defect-candidate`

Get Cluster Defect Candidate

Get a pre-assembled defect candidate for a failure cluster.

Source: [backend/app/routers/deep_investigation.py:207](../../../backend/app/routers/deep_investigation.py#L207).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_run_access.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
run_id: uuid.UUID, cluster_id: str, current_user: User=Depends(require_run_access()), db: AsyncSession=Depends(get_db)
```

Direct handler error branches (dependency/service errors can add others):

```python
HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "get_cluster_defect_candidate_api_v1_deep_investigate__run_id__clusters__cluster_id__defect_candidate_get",
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
      "in": "path",
      "name": "cluster_id",
      "required": true,
      "schema": {
        "title": "Cluster Id",
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
            "$ref": "#/components/schemas/DefectCandidateResponse"
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

## GET `/api/v1/deep-investigate/{run_id}/clusters/{cluster_id}/duplicate-check`

Check Cluster Duplicate

Check if a cluster likely duplicates an existing open defect.
Returns duplicate info without creating anything.
P3-9: Business logic extracted to cluster_service.

Source: [backend/app/routers/deep_investigation.py:303](../../../backend/app/routers/deep_investigation.py#L303).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_run_access.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
run_id: uuid.UUID, cluster_id: str, current_user: User=Depends(require_run_access()), db: AsyncSession=Depends(get_db)
```

Direct handler error branches (dependency/service errors can add others):

```python
HTTPException(status_code=404, detail=str(exc))
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "check_cluster_duplicate_api_v1_deep_investigate__run_id__clusters__cluster_id__duplicate_check_get",
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
      "in": "path",
      "name": "cluster_id",
      "required": true,
      "schema": {
        "title": "Cluster Id",
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
await check_duplicate(str(run_id), cluster_id, db)
```

Contract limit: at least one response has an unstructured schema. Read the linked handler/serializer for emitted fields; the empty schema is not a promise of an empty JSON object.

## POST `/api/v1/deep-investigate/{run_id}/clusters/{cluster_id}/promote`

Promote Cluster To Defect

Promote a failure cluster to a defect record (optionally with Jira ticket).

Source: [backend/app/routers/deep_investigation.py:225](../../../backend/app/routers/deep_investigation.py#L225).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_run_access.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
run_id: uuid.UUID, cluster_id: str, body: DefectPromotionRequest, current_user: User=Depends(get_current_active_user), db: AsyncSession=Depends(get_db), _: User=Depends(require_run_access())
```

Direct handler error branches (dependency/service errors can add others):

```python
HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Test run not found')
HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "promote_cluster_to_defect_api_v1_deep_investigate__run_id__clusters__cluster_id__promote_post",
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
      "in": "path",
      "name": "cluster_id",
      "required": true,
      "schema": {
        "title": "Cluster Id",
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
          "$ref": "#/components/schemas/DefectPromotionRequest"
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
            "$ref": "#/components/schemas/DefectPromotionResponse"
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

## GET `/api/v1/deep-investigate/{run_id}/findings`

Get Deep Findings

Return deep investigation findings per failure cluster for a test run.

Source: [backend/app/routers/deep_investigation.py:171](../../../backend/app/routers/deep_investigation.py#L171).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_run_access.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
run_id: uuid.UUID, current_user: User=Depends(require_run_access())
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "get_deep_findings_api_v1_deep_investigate__run_id__findings_get",
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
          "schema": {
            "items": {
              "$ref": "#/components/schemas/DeepFindingResponse"
            },
            "title": "Response Get Deep Findings Api V1 Deep Investigate  Run Id  Findings Get",
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
