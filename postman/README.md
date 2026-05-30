# TestLookup Postman Collection

Ready-to-use Postman collection covering all TestLookup API endpoints.

## Files

| File | Purpose |
|------|---------|
| `TestLookup_API_Collection.postman_collection.json` | Full API collection (100+ requests, 16 folders) |
| `TestLookup_Local.postman_environment.json` | Local dev environment with pre-configured variables |

## Quick Start

1. **Import both files** into Postman (File > Import)
2. **Select the environment** "TestLookup - Local Dev" from the top-right dropdown
3. **Start the backend** (`make dev` from the project root)
4. **Authenticate**: Run **Auth > Dev Login (Admin)** - the JWT token is auto-saved
5. **Load a project**: Run **Projects > List Projects** - the first `project_id` is auto-saved
6. **Explore**: All subsequent requests use saved variables automatically

## Auto-Saved Variables

Several requests have test scripts that auto-save IDs to environment variables:

| Request | Saves |
|---------|-------|
| Dev Login / Login | `access_token`, `refresh_token` |
| List Projects | `project_id` (first project) |
| List Test Runs | `run_id` (first run) |
| List Test Cases | `test_case_id` (first case) |
| Create Knowledge Source | `knowledge_source_id` |
| Generate Test Cases | `batch_id` |
| Create Chat Session | `chat_session_id` |

## Recommended Exploration Order

```
1.  Auth > Dev Login (Admin)
2.  Health > Details
3.  Projects > List Projects
4.  Test Runs > List Test Runs
5.  Test Runs > List Test Cases in Run
6.  Dashboard & Metrics > Dashboard Summary
7.  Search > Search Test Cases
8.  AI Analysis > Trigger Analysis
9.  Settings > Get AI Config
10. Settings > Update AI Config (enable RAG)
11. Knowledge RAG > RAG Status
12. Knowledge RAG > Generate Test Cases
13. Chat > Create Session
14. Chat > Send Message
```

## Authentication Methods

| Method | Header | Use Case |
|--------|--------|----------|
| JWT Bearer | `Authorization: Bearer {token}` | All protected endpoints (auto-attached from collection auth) |
| API Key | `X-API-Key: qai_{key}` | CI/CD and SDK integration |
| Dev Login | No credentials needed | Local development only |

## Folder Guide

| Folder | Endpoints | Description |
|--------|-----------|-------------|
| Auth | 9 | Login, register, profile, password, token refresh |
| Health | 3 | Liveness, readiness, full service status |
| Projects | 5 | CRUD + member listing |
| Test Runs | 6 | Run listing, test cases, regression diff |
| Dashboard & Metrics | 2 | KPI summary + trend data |
| Search | 4 | Keyword/semantic/global search |
| AI Analysis | 2 | Root-cause analysis via LangChain agent |
| AI Pipeline | 4 | Multi-agent pipeline execution |
| Chat | 5 | Conversational AI sessions |
| Knowledge RAG | 16 | Knowledge sources, retrieval, generation, coverage |
| Integrations | 1 | Jira defect creation |
| Releases | 2 | Release listing + creation |
| User Management | 4 | User CRUD, role updates, invitations |
| API Keys | 2 | Personal access token management |
| Notifications | 4 | Preferences + notification history |
| Reports | 4 | PDF export, evidence bundle, share links |
| Settings | 9 | AI, SMTP, integrations, storage, feature flags |
| SSO / SAML | 3 | SSO status + configuration |
| Observability | 2 | Frontend telemetry + SDK listing |

## Environment Variables Reference

| Variable | Description | Auto-set by |
|----------|-------------|-------------|
| `base_url` | Backend URL (default `http://localhost:8000`) | Manual |
| `access_token` | JWT access token | Login requests |
| `refresh_token` | JWT refresh token | Login requests |
| `project_id` | Active project UUID | List Projects |
| `run_id` | Test run UUID | List Test Runs |
| `test_case_id` | Test case UUID | List Test Cases |
| `batch_id` | RAG generation batch UUID | Generate Test Cases |
| `knowledge_source_id` | Knowledge source UUID | Create Knowledge Source |
| `pipeline_id` | Agent pipeline UUID | Manual |
| `chat_session_id` | Chat session UUID | Create Session |
| `user_id` | Target user UUID | Manual |
| `api_key` | API key for X-API-Key auth | Manual |
