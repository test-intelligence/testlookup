# Releases API

[Documentation home](../../README.md) · [Regeneration](../../handoff/maintenance.md)

Generated from tracked source by `scripts/generate_handoff_reference.py`. Do not edit by hand. The baseline and validation limits are recorded in [verification](../../handoff/verification.md).

## GET `/api/v1/releases`

List Releases



Source: [backend/app/routers/releases.py:164](../../../backend/app/routers/releases.py#L164).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`.

Declared Python handler arguments (includes exact role/guard options):

```python
project_id: Optional[str]=None, status: Optional[str]=None, db: AsyncSession=Depends(get_db), current_user: User=Depends(get_current_active_user)
```

Direct handler error branches (dependency/service errors can add others):

```python
HTTPException(status_code=400, detail='Invalid project_id')
HTTPException(status_code=403, detail='You do not have access to this project')
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "list_releases_api_v1_releases_get",
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
[]
await release_service.list_releases(db, project_id, status, accessible_project_ids=accessible)
```

Contract limit: at least one response has an unstructured schema. Read the linked handler/serializer for emitted fields; the empty schema is not a promise of an empty JSON object.

## POST `/api/v1/releases`

Create Release



Source: [backend/app/routers/releases.py:246](../../../backend/app/routers/releases.py#L246).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_role.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
body: ReleaseIn, db: AsyncSession=Depends(get_db), current_user: User=Depends(require_role(UserRole.QA_LEAD, allow_project_key=True))
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "create_release_api_v1_releases_post",
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
          "$ref": "#/components/schemas/ReleaseIn"
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
await release_service.serialize_created_release(db, release)
```

Contract limit: at least one response has an unstructured schema. Read the linked handler/serializer for emitted fields; the empty schema is not a promise of an empty JSON object.

## POST `/api/v1/releases/sync`

Sync Releases From External

Pull releases in from GitHub milestones or Jira fix versions.

**Why this endpoint exists at all.** ``sync_milestones`` and
``sync_fix_versions`` shipped complete, gated and tested, and nothing
called either of them -- so rung 2 of the attribution ladder ("the run's
external match") could never fire in production, because nothing created a
release carrying a ``source_system`` for it to match against. Two finished
features were unreachable behind a missing route.

**Manual, not scheduled.** No beat entry: ``AI_OFFLINE_MODE`` defaults to
True and a periodic job that egresses on its own is a materially larger
change than making a finished feature reachable. A person asking for a sync
is also the point at which a 503 explaining WHY it cannot run is useful.

Only identity is written -- name, external id, external url. A synced
release's phases, criteria, gate policy and run attribution are never
touched, because those are TestLookup's and neither GitHub nor Jira knows
anything about them.

Source: [backend/app/routers/releases.py:280](../../../backend/app/routers/releases.py#L280).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_role.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
body: ReleaseSyncIn, db: AsyncSession=Depends(get_db), current_user: User=Depends(require_role(UserRole.QA_LEAD, allow_project_key=True))
```

Direct handler error branches (dependency/service errors can add others):

```python
HTTPException(status_code=400, detail='Invalid project ID')
HTTPException(status_code=503, detail=str(exc))
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "sync_releases_from_external_api_v1_releases_sync_post",
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
          "$ref": "#/components/schemas/ReleaseSyncIn"
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
{'source': body.source, **summary}
```

Contract limit: at least one response has an unstructured schema. Read the linked handler/serializer for emitted fields; the empty schema is not a promise of an empty JSON object.

## DELETE `/api/v1/releases/{release_id}`

Delete Release



Source: [backend/app/routers/releases.py:410](../../../backend/app/routers/releases.py#L410).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_release_access.<locals>._check`, `require_role.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
release_id: str, db: AsyncSession=Depends(get_db), current_user: User=Depends(require_role(UserRole.ADMIN, allow_project_key=True)), __: User=Depends(require_release_access())
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "delete_release_api_v1_releases__release_id__delete",
  "parameters": [
    {
      "in": "path",
      "name": "release_id",
      "required": true,
      "schema": {
        "title": "Release Id",
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

## GET `/api/v1/releases/{release_id}`

Get Release



Source: [backend/app/routers/releases.py:195](../../../backend/app/routers/releases.py#L195).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_release_access.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
release_id: str, db: AsyncSession=Depends(get_db), _: User=Depends(require_release_access())
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "get_release_api_v1_releases__release_id__get",
  "parameters": [
    {
      "in": "path",
      "name": "release_id",
      "required": true,
      "schema": {
        "title": "Release Id",
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
await release_service.get_release_details(db, release_id)
```

Contract limit: at least one response has an unstructured schema. Read the linked handler/serializer for emitted fields; the empty schema is not a promise of an empty JSON object.

## PUT `/api/v1/releases/{release_id}`

Update Release



Source: [backend/app/routers/releases.py:338](../../../backend/app/routers/releases.py#L338).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_release_access.<locals>._check`, `require_role.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
release_id: str, body: ReleaseUpdate, db: AsyncSession=Depends(get_db), current_user: User=Depends(require_role(UserRole.QA_LEAD, allow_project_key=True)), __: User=Depends(require_release_access())
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "update_release_api_v1_releases__release_id__put",
  "parameters": [
    {
      "in": "path",
      "name": "release_id",
      "required": true,
      "schema": {
        "title": "Release Id",
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
          "$ref": "#/components/schemas/ReleaseUpdate"
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
serialize_model(release)
```

Contract limit: at least one response has an unstructured schema. Read the linked handler/serializer for emitted fields; the empty schema is not a promise of an empty JSON object.

## POST `/api/v1/releases/{release_id}/activate`

Activate Release Endpoint

Make this the project's active release.

The active release is where a run lands when nothing else claims it — the
attribution ladder's terminal rung. It has been a real, enforced concept
since S0 (one per project, guarded by ``ix_releases_project_active``, with
activation history behind it), and until now it could only ever be chosen
FOR the user by the ingestion ladder and the rotation beat.
``activate_release`` was written complete, with its ``reason="manual"``
default, for a caller that never arrived.

Both guards, not one: ``require_role`` gates by ROLE and knows nothing about
which project this release belongs to, so a QA lead of one project could
otherwise redirect another project's attribution.

The swap itself stays in ``release_lifecycle_service`` — it contains a
load-bearing flush between the demote and the promote, because SQLAlchemy
orders persistent UPDATEs by primary key and ``Release.id`` is a random
uuid4, so without it half of all orderings present two active rows to a
partial unique index. Writing ``is_active`` here instead would route around
that.

Source: [backend/app/routers/releases.py:516](../../../backend/app/routers/releases.py#L516).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_release_access.<locals>._check`, `require_role.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
release_id: str, db: AsyncSession=Depends(get_db), current_user: User=Depends(require_role(UserRole.QA_LEAD, allow_project_key=True)), __: User=Depends(require_release_access())
```

Direct handler error branches (dependency/service errors can add others):

```python
HTTPException(status_code=409, detail=f"'{release.name}' is {release.status} and cannot be made active — a finished release must not collect new runs.")
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "activate_release_endpoint_api_v1_releases__release_id__activate_post",
  "parameters": [
    {
      "in": "path",
      "name": "release_id",
      "required": true,
      "schema": {
        "title": "Release Id",
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
{'release_id': str(release.id), 'is_active': True, 'deactivated': None, 'changed': False}
{'release_id': str(release.id), 'is_active': True, 'deactivated': str(previous.id) if previous is not None else None, 'changed': True}
```

Contract limit: at least one response has an unstructured schema. Read the linked handler/serializer for emitted fields; the empty schema is not a promise of an empty JSON object.

## GET `/api/v1/releases/{release_id}/gate`

Get Release Gate

The standing verdict for this release, read from the stored snapshot.

Deliberately does NOT recompute. Recomputing would restate a past verdict
under today's runs and today's policy, which is what the snapshot columns on
``ReleaseGateDecision`` exist to prevent.

404 when the release has never been evaluated — distinct from a verdict of
NOT_EVALUATED, which means it WAS evaluated and there was not enough
evidence to say. Collapsing those two into one response would lose the
difference between "we have not looked" and "we looked and cannot say".

Source: [backend/app/routers/releases.py:204](../../../backend/app/routers/releases.py#L204).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_release_access.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
release_id: str, db: AsyncSession=Depends(get_db), _: User=Depends(require_release_access())
```

Direct handler error branches (dependency/service errors can add others):

```python
HTTPException(status_code=404, detail='This release has not been evaluated yet')
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "get_release_gate_api_v1_releases__release_id__gate_get",
  "parameters": [
    {
      "in": "path",
      "name": "release_id",
      "required": true,
      "schema": {
        "title": "Release Id",
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
gate
```

Contract limit: at least one response has an unstructured schema. Read the linked handler/serializer for emitted fields; the empty schema is not a promise of an empty JSON object.

## GET `/api/v1/releases/{release_id}/gate/baseline`

Get Release Gate Baseline

Release-over-release comparison, or a stated reason it is not meaningful.

Returns ``comparable: false`` with a reason rather than numbers whenever a
delta would mislead — no baseline, or either side below the evidence floor.
A delta against a release nothing ran in is arithmetically fine and
completely meaningless, and once it is a number on a scorecard nobody
re-derives whether it was meaningful.

Source: [backend/app/routers/releases.py:227](../../../backend/app/routers/releases.py#L227).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_release_access.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
release_id: str, db: AsyncSession=Depends(get_db), _: User=Depends(require_release_access())
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "get_release_gate_baseline_api_v1_releases__release_id__gate_baseline_get",
  "parameters": [
    {
      "in": "path",
      "name": "release_id",
      "required": true,
      "schema": {
        "title": "Release Id",
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
await release_gate_service.compare_to_baseline(db, release_id)
```

Contract limit: at least one response has an unstructured schema. Read the linked handler/serializer for emitted fields; the empty schema is not a promise of an empty JSON object.

## POST `/api/v1/releases/{release_id}/gate/evaluate`

Evaluate Release Gate

Evaluate the gate and, by default, record the verdict.

QA_LEAD, not plain membership. Recording appends to an append-only audit
trail that a release decision is later justified by, so it is a privileged
write even though it computes rather than edits.

The router owns the commit, per the repo's transaction-boundary rule, so the
demote of the previous verdict and the insert of the new one land as one
unit of work — a failure cannot leave a release with two current verdicts or
none.

Source: [backend/app/routers/releases.py:442](../../../backend/app/routers/releases.py#L442).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_release_access.<locals>._check`, `require_role.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
release_id: str, record: bool=Query(True, description="Append the verdict to the release's audit history. Pass false to preview without recording."), db: AsyncSession=Depends(get_db), current_user: User=Depends(require_role(UserRole.QA_LEAD, allow_project_key=True)), _access: User=Depends(require_release_access())
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "evaluate_release_gate_api_v1_releases__release_id__gate_evaluate_post",
  "parameters": [
    {
      "in": "path",
      "name": "release_id",
      "required": true,
      "schema": {
        "title": "Release Id",
        "type": "string"
      }
    },
    {
      "description": "Append the verdict to the release's audit history. Pass false to preview without recording.",
      "in": "query",
      "name": "record",
      "required": false,
      "schema": {
        "default": true,
        "description": "Append the verdict to the release's audit history. Pass false to preview without recording.",
        "title": "Record",
        "type": "boolean"
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

## POST `/api/v1/releases/{release_id}/outcomes`

Mark Release Outcome

Append a human incident or rollback outcome for later G5 evaluation.

Source: [backend/app/routers/releases.py:379](../../../backend/app/routers/releases.py#L379).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_release_access.<locals>._check`, `require_role.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
release_id: str, body: ReleaseOutcomeIn, db: AsyncSession=Depends(get_db), current_user: User=Depends(require_role(UserRole.QA_LEAD, allow_project_key=True)), _access: User=Depends(require_release_access())
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "mark_release_outcome_api_v1_releases__release_id__outcomes_post",
  "parameters": [
    {
      "in": "path",
      "name": "release_id",
      "required": true,
      "schema": {
        "title": "Release Id",
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
          "$ref": "#/components/schemas/ReleaseOutcomeIn"
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
serialize_model(outcome)
```

Contract limit: at least one response has an unstructured schema. Read the linked handler/serializer for emitted fields; the empty schema is not a promise of an empty JSON object.

## POST `/api/v1/releases/{release_id}/phases`

Add Phase



Source: [backend/app/routers/releases.py:480](../../../backend/app/routers/releases.py#L480).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_release_access.<locals>._check`, `require_role.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
release_id: str, body: PhaseIn, db: AsyncSession=Depends(get_db), _: User=Depends(require_role(UserRole.QA_LEAD, allow_project_key=True)), __: User=Depends(require_release_access())
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "add_phase_api_v1_releases__release_id__phases_post",
  "parameters": [
    {
      "in": "path",
      "name": "release_id",
      "required": true,
      "schema": {
        "title": "Release Id",
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
          "$ref": "#/components/schemas/PhaseIn"
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
serialize_model(phase)
```

Contract limit: at least one response has an unstructured schema. Read the linked handler/serializer for emitted fields; the empty schema is not a promise of an empty JSON object.

## GET `/api/v1/releases/{release_id}/phases/gate`

Get Release Phase Gate

Every phase of this release, with a verdict each, and whether it may
advance.

Read-only: ``evaluate_all_phases`` defaults to ``record=False`` because
looking at the state of a release is routine, and appending a decision row
per phase per look would bury the real decisions in noise.

The summary is three-valued on purpose. A release blocked by a FAILING
phase and one blocked by an UNEVALUATED phase need different actions — fix
the tests, or go run some — and collapsing both into "cannot advance" sends
a release manager to do the wrong one half the time. ``NO_PHASES`` is a
fourth state and not a pass: phases are optional, so having none is not a
failure, but nothing was gated either.

Source: [backend/app/routers/releases.py:599](../../../backend/app/routers/releases.py#L599).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_release_access.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
release_id: str, db: AsyncSession=Depends(get_db), _: User=Depends(require_release_access())
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "get_release_phase_gate_api_v1_releases__release_id__phases_gate_get",
  "parameters": [
    {
      "in": "path",
      "name": "release_id",
      "required": true,
      "schema": {
        "title": "Release Id",
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
{'release_id': release_id, **release_phase_gate_service.summarise_gate(phases), 'phases': phases}
```

Contract limit: at least one response has an unstructured schema. Read the linked handler/serializer for emitted fields; the empty schema is not a promise of an empty JSON object.

## DELETE `/api/v1/releases/{release_id}/phases/{phase_id}`

Delete Phase



Source: [backend/app/routers/releases.py:650](../../../backend/app/routers/releases.py#L650).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_release_access.<locals>._check`, `require_role.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
release_id: str, phase_id: str, db: AsyncSession=Depends(get_db), _: User=Depends(require_role(UserRole.ADMIN, allow_project_key=True)), __: User=Depends(require_release_access())
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "delete_phase_api_v1_releases__release_id__phases__phase_id__delete",
  "parameters": [
    {
      "in": "path",
      "name": "release_id",
      "required": true,
      "schema": {
        "title": "Release Id",
        "type": "string"
      }
    },
    {
      "in": "path",
      "name": "phase_id",
      "required": true,
      "schema": {
        "title": "Phase Id",
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

## PUT `/api/v1/releases/{release_id}/phases/{phase_id}`

Update Phase



Source: [backend/app/routers/releases.py:494](../../../backend/app/routers/releases.py#L494).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_release_access.<locals>._check`, `require_role.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
release_id: str, phase_id: str, body: PhaseUpdate, db: AsyncSession=Depends(get_db), current_user: User=Depends(require_role(UserRole.QA_LEAD, allow_project_key=True)), __: User=Depends(require_release_access())
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "update_phase_api_v1_releases__release_id__phases__phase_id__put",
  "parameters": [
    {
      "in": "path",
      "name": "release_id",
      "required": true,
      "schema": {
        "title": "Release Id",
        "type": "string"
      }
    },
    {
      "in": "path",
      "name": "phase_id",
      "required": true,
      "schema": {
        "title": "Phase Id",
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
          "$ref": "#/components/schemas/PhaseUpdate"
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

## POST `/api/v1/releases/{release_id}/phases/{phase_id}/gate/evaluate`

Evaluate Release Phase Gate

Evaluate ONE phase and, by default, record the verdict.

``record=false`` previews without appending. The history is the point of
that table, so writing to it is a deliberate act rather than a side effect
of looking — which is also why the read endpoint above never records.

Source: [backend/app/routers/releases.py:627](../../../backend/app/routers/releases.py#L627).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_release_access.<locals>._check`, `require_role.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
release_id: str, phase_id: str, record: bool=Query(True, description='Append the verdict to the audit history'), db: AsyncSession=Depends(get_db), current_user: User=Depends(require_role(UserRole.QA_LEAD, allow_project_key=True)), __: User=Depends(require_release_access())
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "evaluate_release_phase_gate_api_v1_releases__release_id__phases__phase_id__gate_evaluate_post",
  "parameters": [
    {
      "in": "path",
      "name": "release_id",
      "required": true,
      "schema": {
        "title": "Release Id",
        "type": "string"
      }
    },
    {
      "in": "path",
      "name": "phase_id",
      "required": true,
      "schema": {
        "title": "Phase Id",
        "type": "string"
      }
    },
    {
      "description": "Append the verdict to the audit history",
      "in": "query",
      "name": "record",
      "required": false,
      "schema": {
        "default": true,
        "description": "Append the verdict to the audit history",
        "title": "Record",
        "type": "boolean"
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

## POST `/api/v1/releases/{release_id}/test-runs`

Link Test Run



Source: [backend/app/routers/releases.py:664](../../../backend/app/routers/releases.py#L664).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_release_access.<locals>._check`, `require_role.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
release_id: str, body: LinkRunRequest, db: AsyncSession=Depends(get_db), current_user: User=Depends(require_role(UserRole.QA_LEAD, allow_project_key=True)), __: User=Depends(require_release_access())
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "link_test_run_api_v1_releases__release_id__test_runs_post",
  "parameters": [
    {
      "in": "path",
      "name": "release_id",
      "required": true,
      "schema": {
        "title": "Release Id",
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
          "$ref": "#/components/schemas/LinkRunRequest"
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
serialize_model(link)
{'message': 'Already linked', 'id': str(link.id)}
```

Contract limit: at least one response has an unstructured schema. Read the linked handler/serializer for emitted fields; the empty schema is not a promise of an empty JSON object.

## DELETE `/api/v1/releases/{release_id}/test-runs/{run_id}`

Unlink Test Run



Source: [backend/app/routers/releases.py:683](../../../backend/app/routers/releases.py#L683).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_release_access.<locals>._check`, `require_role.<locals>._check`, `require_run_access.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
release_id: str, run_id: str, db: AsyncSession=Depends(get_db), _: User=Depends(require_role(UserRole.ADMIN, allow_project_key=True)), __: User=Depends(require_run_access()), ___: User=Depends(require_release_access())
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "unlink_test_run_api_v1_releases__release_id__test_runs__run_id__delete",
  "parameters": [
    {
      "in": "path",
      "name": "release_id",
      "required": true,
      "schema": {
        "title": "Release Id",
        "type": "string"
      }
    },
    {
      "in": "path",
      "name": "run_id",
      "required": true,
      "schema": {
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
