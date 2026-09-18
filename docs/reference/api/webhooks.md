# Webhooks API

[Documentation home](../../README.md) · [Regeneration](../../handoff/maintenance.md)

Generated from tracked source by `scripts/generate_handoff_reference.py`. Do not edit by hand. The baseline and validation limits are recorded in [verification](../../handoff/verification.md).

## POST `/webhooks/minio`

Minio Webhook

Receive MinIO ObjectCreated events.
Requires MinIO's auth_token (``Authorization: Bearer``) or X-Webhook-Secret,
matching WEBHOOK_SECRET.
Only processes uploads of upload_complete.json sentinel files.
Always returns 200 OK quickly to prevent MinIO retry loops.

Source: [backend/app/routers/webhooks.py:60](../../../backend/app/routers/webhooks.py#L60).

Dependency chain: `verify_webhook_secret`.

Declared Python handler arguments (includes exact role/guard options):

```python
request: Request, background_tasks: BackgroundTasks
```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "minio_webhook_webhooks_minio_post",
  "parameters": [
    {
      "in": "header",
      "name": "X-Webhook-Secret",
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
        "title": "X-Webhook-Secret"
      }
    },
    {
      "in": "header",
      "name": "Authorization",
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
        "title": "Authorization"
      }
    }
  ],
  "responses": {
    "200": {
      "content": {
        "application/json": {
          "schema": {
            "additionalProperties": true,
            "title": "Response Minio Webhook Webhooks Minio Post",
            "type": "object"
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
