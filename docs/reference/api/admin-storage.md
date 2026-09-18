# Admin Storage API

[Documentation home](../../README.md) · [Regeneration](../../handoff/maintenance.md)

Generated from tracked source by `scripts/generate_handoff_reference.py`. Do not edit by hand. The baseline and validation limits are recorded in [verification](../../handoff/verification.md).

## GET `/api/v1/admin/storage/deleted-projects`

Get Deleted Project Storage

What deleted projects still cost, and what will never reclaim it.

Deleting a project sets ``is_active = False`` and revokes its credentials;
nothing reconciles the data. Because ``ProjectRetentionPolicy.enabled``
defaults to ``False``, a project deleted without retention turned on is
purged by **nothing, ever** — across all five stores, invisible on every
other screen.

``reachable_by_retention`` is the distinction that matters: the nightly
beat selects on ``enabled`` alone and does **not** filter ``is_active``, so
a project that opted in before deletion still gets swept. One that did not
is stranded, and ``unreachable_by_retention`` counts those.

Deployment-wide, so it is ADMIN-only and not project-scoped — there is no
``{project_id}`` here to guard, and a per-project footprint already exists
at ``GET /api/v1/projects/{project_id}/storage``.

Bounded by ``limit``: each footprint costs at least one paginated
object-store listing. ``truncated`` and ``projects_measured`` say when the
answer is partial, so a capped total cannot quietly understate the number
this endpoint exists to surface.

Source: [backend/app/routers/admin_storage.py:25](../../../backend/app/routers/admin_storage.py#L25).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_role.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
limit: int=Query(storage_accounting_service.MAX_DELETED_PROJECTS_SCANNED, ge=1, le=200), db: AsyncSession=Depends(get_db), current_user: User=Depends(get_current_active_user)
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "get_deleted_project_storage_api_v1_admin_storage_deleted_projects_get",
  "parameters": [
    {
      "in": "query",
      "name": "limit",
      "required": false,
      "schema": {
        "default": 25,
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
            "$ref": "#/components/schemas/DeletedProjectsStorageResponse"
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
