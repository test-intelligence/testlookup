# Reports API

[Documentation home](../../README.md) · [Regeneration](../../maintainers/maintenance.md)

Generated from tracked source by `scripts/generate_reference.py`. Do not edit by hand. What the checks cover is recorded in [verification](../../maintainers/verification.md).

## POST `/api/v1/reports/email-trends`

Email Trends Report

Email the Trends report of one project (the page's window, releases and
suites) to project members and up to 5 typed addresses, through the relay
saved in Settings › Notifications. One message for every recipient.

Source: [backend/app/routers/reports.py:143](../../../backend/app/routers/reports.py#L143).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_role.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
body: EmailTrendsRequest=Body(...), db: AsyncSession=Depends(get_db), current_user: User=Depends(require_role(UserRole.QA_LEAD))
```

Direct handler error branches (dependency/service errors can add others):

```python
HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f'The mail relay refused the report ({type(exc).__name__}).')
HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=reason)
HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc))
HTTPException(status_code=status.HTTP_504_GATEWAY_TIMEOUT, detail=RELAY_TIMEOUT)
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "email_trends_report_api_v1_reports_email_trends_post",
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
          "$ref": "#/components/schemas/EmailTrendsRequest"
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
            "$ref": "#/components/schemas/EmailTrendsResponse"
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

## GET `/api/v1/reports/email-trends/availability`

Email Trends Availability

Can the Trends report be emailed right now? The relay saved in Settings ›
Notifications must be enabled, have a host, and pass the offline egress
gate (the recipients are judged when sending). Reads nothing of a project.

Source: [backend/app/routers/reports.py:220](../../../backend/app/routers/reports.py#L220).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_role.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
db: AsyncSession=Depends(get_db), current_user: User=Depends(require_role(UserRole.QA_LEAD))
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "email_trends_availability_api_v1_reports_email_trends_availability_get",
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
            "$ref": "#/components/schemas/EmailAvailability"
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

## GET `/api/v1/reports/runs/{run_id}/evidence-bundle`

Export Evidence Bundle

Generate and download a ZIP evidence bundle for the given run.

Source: [backend/app/routers/reports.py:351](../../../backend/app/routers/reports.py#L351).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_run_access.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
run_id: uuid.UUID, db: AsyncSession=Depends(get_db), current_user: User=Depends(require_run_access())
```

Direct handler error branches (dependency/service errors can add others):

```python
HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Test run not found')
HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail='Bundle generation failed')
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "export_evidence_bundle_api_v1_reports_runs__run_id__evidence_bundle_get",
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
StreamingResponse(io.BytesIO(zip_bytes), media_type='application/zip', headers={'Content-Disposition': f'attachment; filename="evidence-{str(run_id)[:8]}.zip"'})
```

Contract limit: at least one response has an unstructured schema. Read the linked handler/serializer for emitted fields; the empty schema is not a promise of an empty JSON object.

## GET `/api/v1/reports/runs/{run_id}/pdf`

Export Run Report Pdf

Generate and download a PDF intelligence report for the given run.

E8.4: an AI report nobody has accepted is refused (409) once the review gate
is enforced, unless the project allows drafts or a QA lead passes
``include_unreviewed``; either way it is watermarked and audited.

Source: [backend/app/routers/reports.py:269](../../../backend/app/routers/reports.py#L269).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_run_access.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
run_id: uuid.UUID, layout: str=Query(default='executive', pattern='^(executive|engineering)$'), include_unreviewed: bool=Query(default=False, description='Export an AI report still awaiting human review, watermarked DRAFT. QA_LEAD or ADMIN only; audited.'), db: AsyncSession=Depends(get_db), current_user: User=Depends(require_run_access())
```

Direct handler error branches (dependency/service errors can add others):

```python
HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail='include_unreviewed requires QA_LEAD or ADMIN')
HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
HTTPException(status_code=status.HTTP_409_CONFLICT, detail=refusal_detail(decision))
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "export_run_report_pdf_api_v1_reports_runs__run_id__pdf_get",
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
      "name": "layout",
      "required": false,
      "schema": {
        "default": "executive",
        "pattern": "^(executive|engineering)$",
        "title": "Layout",
        "type": "string"
      }
    },
    {
      "description": "Export an AI report still awaiting human review, watermarked DRAFT. QA_LEAD or ADMIN only; audited.",
      "in": "query",
      "name": "include_unreviewed",
      "required": false,
      "schema": {
        "default": false,
        "description": "Export an AI report still awaiting human review, watermarked DRAFT. QA_LEAD or ADMIN only; audited.",
        "title": "Include Unreviewed",
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
StreamingResponse(io.BytesIO(pdf_bytes), media_type='application/pdf', headers={'Content-Disposition': f'attachment; filename="report-{str(run_id)[:8]}-{layout}.pdf"'})
```

Contract limit: at least one response has an unstructured schema. Read the linked handler/serializer for emitted fields; the empty schema is not a promise of an empty JSON object.

## POST `/api/v1/reports/runs/{run_id}/share`

Create Share Link Endpoint

Create a time-limited share link for the run report.

Source: [backend/app/routers/reports.py:423](../../../backend/app/routers/reports.py#L423).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_run_access.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
run_id: uuid.UUID, body: CreateShareLinkRequest, db: AsyncSession=Depends(get_db), current_user: User=Depends(require_run_access())
```

Direct handler error branches (dependency/service errors can add others):

```python
HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Run not found')
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "create_share_link_endpoint_api_v1_reports_runs__run_id__share_post",
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
          "$ref": "#/components/schemas/CreateShareLinkRequest"
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
            "$ref": "#/components/schemas/ShareLinkResponse"
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

## GET `/api/v1/reports/runs/{run_id}/share-links`

List Share Links Endpoint

List all share links for a run.

Source: [backend/app/routers/reports.py:512](../../../backend/app/routers/reports.py#L512).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_run_access.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
run_id: uuid.UUID, db: AsyncSession=Depends(get_db), current_user: User=Depends(require_run_access())
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "list_share_links_endpoint_api_v1_reports_runs__run_id__share_links_get",
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
              "$ref": "#/components/schemas/ShareLinkResponse"
            },
            "title": "Response List Share Links Endpoint Api V1 Reports Runs  Run Id  Share Links Get",
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

## DELETE `/api/v1/reports/share-links/{link_id}`

Revoke Share Link Endpoint

Revoke a share link. Creator, project members, and admins can revoke.

Source: [backend/app/routers/reports.py:539](../../../backend/app/routers/reports.py#L539).

Dependency chain: `OAuth2PasswordBearer`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`, `require_link_access.<locals>._check`.

Declared Python handler arguments (includes exact role/guard options):

```python
link: ReportShareLink=Depends(require_link_access()), db: AsyncSession=Depends(get_db), current_user: User=Depends(get_current_active_user)
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "revoke_share_link_endpoint_api_v1_reports_share_links__link_id__delete",
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

## GET `/api/v1/reports/trends/pdf`

Export Trends Report Pdf

The Trends report as a PDF (any member): the page's KPIs, verdict, daily
series and suite table for its window, releases and suites. Audited.

Source: [backend/app/routers/reports.py:239](../../../backend/app/routers/reports.py#L239).

Dependency chain: `OAuth2PasswordBearer`, `analytics_scope.<locals>.dependency`, `get_current_active_user`, `get_current_user_or_api_key`, `get_db`.

Declared Python handler arguments (includes exact role/guard options):

```python
scope: AnalyticsScope=Depends(analytics_scope(_TRENDS_PDF_SCOPE)), db: AsyncSession=Depends(get_db), current_user: User=Depends(get_current_active_user)
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "export_trends_report_pdf_api_v1_reports_trends_pdf_get",
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
      "description": "Window in days, 1-90.",
      "in": "query",
      "name": "days",
      "required": false,
      "schema": {
        "default": 14,
        "description": "Window in days, 1-90.",
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
StreamingResponse(io.BytesIO(content), media_type='application/pdf', headers={'Content-Disposition': f'attachment; filename="{filename}"'})
```

Contract limit: at least one response has an unstructured schema. Read the linked handler/serializer for emitted fields; the empty schema is not a promise of an empty JSON object.
