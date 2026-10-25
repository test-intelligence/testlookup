# Agent Actions API

[Documentation home](../../README.md) · [Regeneration](../../maintainers/maintenance.md)

Generated from tracked source by `scripts/generate_reference.py`. Do not edit by hand. What the checks cover is recorded in [verification](../../maintainers/verification.md).

## GET `/api/v1/projects/{project_id}/agent-actions`

List Agent Actions

The project's agent actions. ``view=pending`` is the queue (oldest first);
``view=decided`` the recent decisions (newest decision first). ``pipeline_run_id``
/ ``test_run_id`` filter INSIDE the path project: a foreign id matches nothing.

Source: [backend/app/routers/agent_actions.py:73](../../../backend/app/routers/agent_actions.py#L73).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_project_access.<locals>._check`, `require_role.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
project_id: uuid.UUID, view: Optional[str]=Query(default=None, max_length=20), status: Optional[str]=Query(default=None, max_length=20), action_type: Optional[str]=Query(default=None, max_length=60), pipeline_run_id: Optional[uuid.UUID]=Query(default=None), test_run_id: Optional[uuid.UUID]=Query(default=None), limit: int=Query(default=50, ge=1, le=200), db: AsyncSession=Depends(get_db), current_user: User=Depends(require_role(UserRole.QA_LEAD, allow_project_key=True)), __: User=Depends(require_project_access())
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "list_agent_actions_api_v1_projects__project_id__agent_actions_get",
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
      "name": "view",
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
        "title": "View"
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
      "name": "action_type",
      "required": false,
      "schema": {
        "anyOf": [
          {
            "maxLength": 60,
            "type": "string"
          },
          {
            "type": "null"
          }
        ],
        "title": "Action Type"
      }
    },
    {
      "in": "query",
      "name": "pipeline_run_id",
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
        "title": "Pipeline Run Id"
      }
    },
    {
      "in": "query",
      "name": "test_run_id",
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
        "title": "Test Run Id"
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
            "$ref": "#/components/schemas/AgentActionListResponse"
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

## PATCH `/api/v1/projects/{project_id}/agent-actions/{action_id}`

Transition Agent Action

Approve, reject or undo one agent action. Nothing is written before every check passes.

Source: [backend/app/routers/agent_actions.py:229](../../../backend/app/routers/agent_actions.py#L229).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_project_access.<locals>._check`, `require_role.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
project_id: uuid.UUID, action_id: uuid.UUID, body: ActionTransitionRequest, db: AsyncSession=Depends(get_db), current_user: User=Depends(require_role(UserRole.QA_LEAD)), _: User=Depends(require_project_access())
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "transition_agent_action_api_v1_projects__project_id__agent_actions__action_id__patch",
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
      "name": "action_id",
      "required": true,
      "schema": {
        "format": "uuid",
        "title": "Action Id",
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
          "$ref": "#/components/schemas/ActionTransitionRequest"
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
            "$ref": "#/components/schemas/AgentActionRead"
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
