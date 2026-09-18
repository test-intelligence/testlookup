# Integration and client map

[Documentation home](../README.md)

| Integration | Direction/protocol | Configuration and authority | Main code |
|---|---|---|---|
| React UI | REST, WebSocket and SSE | JWT session; backend still enforces access; same-origin default API URL | [API client](../../frontend/src/services/api.ts), [routes](../../frontend/src/App.tsx) |
| CLI | HTTP to API | Named profiles, JWT/API key, project context | [CLI app](../../cli/testlookup_cli/app.py), [CLI guide](../../cli/README.md) |
| Python SDK/reporter | Batch/live result production | CI metadata and project credential | [client guide](../../client/README.md), [reporter](../../client/testlookup_reporter.py) |
| JS/Java/Go clients | Framework/runner integration and telemetry | Language-specific setup and CI secret storage | [client tree](../../client) |
| MCP | stdio or authenticated SSE → REST | stdio configured credentials; SSE per-caller bearer authority | [server](../../mcp/server.py), [client](../../mcp/client.py), [token verifier](../../mcp/token_verifier.py) |
| MinIO sentinel | Storage webhook → async ingestion | Webhook credential and validated object/project context | [webhooks](../../backend/app/routers/webhooks.py), [sentinel](../../backend/app/services/minio_sentinel.py) |
| Jira | Issue creation/status, feedback webhook, knowledge/release data | Project integration config, credentials, egress and action policy | [Jira client](../../backend/app/services/jira_client.py), [defect service](../../backend/app/services/defect_jira_service.py) |
| GitHub/GitLab | Commit/release context, checks/status/comments | Configured token/repository/project flags and review policy | [GitHub integration](../../backend/app/routers/github_integration.py), [GitLab integration](../../backend/app/services/gitlab_integration_service.py) |
| Confluence/URLs/documents | Knowledge ingestion → chunk/retrieve/generate | Source registry, URL safety, project scope, freshness and RAG gates | [knowledge sync](../../backend/app/services/knowledge_sync_service.py), [RAG](../../backend/app/services/rag_generation_service.py) |
| Splunk/OpenShift | Log/pod/environment evidence | Optional connector settings and network access | [connectors](../../backend/app/services/connectors), [OCP client](../../backend/app/services/ocp_client.py) |
| SMTP/Slack/Teams | Notifications and scheduled digests | Channel/transition settings, offline destination policy | [notification package](../../backend/app/services/notification), [routing](../../backend/app/services/notification_routing.py) |
| Outbound webhooks | Signed event deliveries, retries/DLQ/replay | Subscription, destination validation, project flags and current distribution policy | [webhook service](../../backend/app/services/webhook_service.py) |
| Model providers | Inference and embeddings | Environment ceiling + project agent configuration + budgets/allowlists | [factory](../../backend/app/services/llm_factory.py), [model router](../../backend/app/services/model_router.py) |

## Client contract details

The shared browser Axios client uses `VITE_API_BASE_URL` or same-origin, sends credentials only to the backend origin, serializes list queries as repeated bare keys, and coordinates refresh behavior. MFA/auth endpoints are excluded from normal refresh retry because their 401 can mean a rejected challenge rather than expired access. Prefer service wrappers to page-local HTTP code.

The CLI registers 13 command groups plus `ci-verdict`. The generated [client surface inventory](../reference/client-surfaces.md) lists commands, arguments, MCP functions and UI routes. For this source snapshot it finds 67 MCP tools, 11 resources and 7 prompts; counts are generated from decorators, not the stale marketing count. MCP review tools expose review information; inspect which actions the server actually registers before assuming UI write parity.

```bash
# From a development environment with Python 3.11+
python -m pip install -e ./cli
testlookup --help
testlookup auth --help
testlookup upload --help
testlookup ci-verdict --help
```

Use the selected command's help for credentials/profile/file options rather than copying credentials into examples. SDK format support differs by language/framework; backend support for a report format does not imply every SDK can generate it.

## Failure and retry guidance

Preserve source identity on producer retries and honor rate-limit/backpressure headers. Do not retry arbitrary mutations solely because a response was lost: inspect operation-specific idempotency semantics. Distinguish enqueue, durable storage, analysis completion, human approval and remote delivery. An accepted internal action proposal is not proof of a successful remote API call.

External delivery can lag or fail after a successful ingest. Inspect delivery attempts, integration health, worker/beat availability and policy refusal. Review authority must be checked at delivery/replay time as well as original report creation. Never use external connector content as instructions to bypass project policy.
