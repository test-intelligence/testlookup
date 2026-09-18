# Shared Reports API

[Documentation home](../../README.md) · [Regeneration](../../handoff/maintenance.md)

Generated from tracked source by `scripts/generate_handoff_reference.py`. Do not edit by hand. The baseline and validation limits are recorded in [verification](../../handoff/verification.md).

## GET `/api/v1/shared/reports/{token}`

View Shared Report

Render the shared HTML report view (no auth required — token-based).

Source: [backend/app/routers/shared_reports.py:18](../../../backend/app/routers/shared_reports.py#L18).

Dependency chain: `get_db`.

Declared Python handler arguments (includes exact role/guard options):

```python
token: str, db: AsyncSession=Depends(get_db)
```

Direct handler error branches (dependency/service errors can add others):

```python
HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))
HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
HTTPException(status_code=status.HTTP_409_CONFLICT, detail=refusal_detail(decision))
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "view_shared_report_api_v1_shared_reports__token__get",
  "parameters": [
    {
      "in": "path",
      "name": "token",
      "required": true,
      "schema": {
        "title": "Token",
        "type": "string"
      }
    }
  ],
  "responses": {
    "200": {
      "content": {
        "text/html": {
          "schema": {
            "type": "string"
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
  }
}
```

## GET `/api/v1/shared/reports/{token}/pdf`

Download Shared Report Pdf

Download the shared PDF report (no auth required — token-based).

Source: [backend/app/routers/shared_reports.py:77](../../../backend/app/routers/shared_reports.py#L77).

Dependency chain: `get_db`.

Declared Python handler arguments (includes exact role/guard options):

```python
token: str, db: AsyncSession=Depends(get_db)
```

Direct handler error branches (dependency/service errors can add others):

```python
HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))
HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
HTTPException(status_code=status.HTTP_409_CONFLICT, detail=refusal_detail(decision))
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "download_shared_report_pdf_api_v1_shared_reports__token__pdf_get",
  "parameters": [
    {
      "in": "path",
      "name": "token",
      "required": true,
      "schema": {
        "title": "Token",
        "type": "string"
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
  }
}
```

Handler return expressions (source excerpts, not an inferred wire schema):

```python
StreamingResponse(io.BytesIO(pdf_bytes), media_type='application/pdf', headers={'Content-Disposition': f'attachment; filename="shared-report-{str(link.run_id)[:8]}.pdf"'})
```

Contract limit: at least one response has an unstructured schema. Read the linked handler/serializer for emitted fields; the empty schema is not a promise of an empty JSON object.
