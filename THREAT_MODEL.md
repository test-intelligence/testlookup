# TestLookup Threat Model

This document describes where data flows, what guarantees the offline mode provides, and what boundaries the system enforces.

## Data flow summary

```
Test Runner --> Ingest API (REST) --> Backend
                                       |
                        +--------------+--------------+
                        |              |              |
                   PostgreSQL       MongoDB        Redis
                  (relational)     (event log)    (cache/broker)
                        |
                   Celery Worker
                        |
            +-----------+-----------+
            |                       |
         Ollama (local)     External LLMs (cloud)
         [offline-safe]     [requires AI_OFFLINE_MODE=false]
            |
         MinIO (local object store)
```

## Trust boundaries

| Boundary | What crosses it | Protection |
|----------|----------------|------------|
| **User to Backend** | HTTP requests (JWT / API key) | TLS in production, JWT validation, role-based access control, project-scope tenant isolation |
| **Backend to PostgreSQL** | SQL queries | Async connection pool with timeout, parameterised queries (no string interpolation), row-level tenant isolation via `project_id` WHERE clauses |
| **Backend to MongoDB** | Pipeline event writes | Authenticated connection, write-only from backend (no user-facing reads bypass the backend) |
| **Backend to Redis** | Cache reads/writes, Celery task dispatch | Password-authenticated, private network only |
| **Backend to MinIO** | Object store reads/writes (compliance packs, RAG documents) | Authenticated via access key, bucket-level ACL, private network |
| **Backend to Ollama** | LLM inference prompts | Private network only, no auth (Ollama doesn't support it), PII-redacted input |
| **Backend to External LLMs** | LLM inference prompts | API key auth, HTTPS, PII-redacted input, gated by `AI_OFFLINE_MODE` |
| **Backend to GitHub** | Check run posts | PAT auth, HTTPS, gated by `AI_OFFLINE_MODE` + `github_checks` flag |
| **Backend to Webhook receivers** | Event payloads | HMAC-SHA256 signed, HTTPS recommended, gated by `AI_OFFLINE_MODE` + `outbound_webhooks` flag |
| **MCP Server to Backend** | REST API calls | Same JWT/API key auth as any other client |
| **CLI to Backend** | REST API calls | Same JWT/API key auth |

## Offline mode guarantees

When `AI_OFFLINE_MODE=true`:

| Category | Behaviour |
|----------|-----------|
| **LLM inference** | Only local Ollama is used. No cloud LLM calls (OpenAI, Gemini, etc.) are made regardless of `LLM_PROVIDER` config. |
| **GitHub Checks** | Silent no-op. `github_checks_service` returns early before any network call. |
| **Outbound webhooks** | Silent no-op. `webhook_service.emit_event` returns 0 without touching the DB. |
| **RAG faithfulness (Ragas)** | Falls back to Ollama evaluator regardless of `rag_faithfulness_evaluator` config. |
| **Weekly retro digest narrative** | Falls back to the deterministic template path without calling the LLM. |
| **Feature flags** | Still functional (flags resolve via Redis/Postgres, both local). The offline gate is checked BEFORE the feature flag in every outbound service. |
| **Ingestion** | Fully functional. No network calls. |
| **Analysis (rules/ML)** | Fully functional. No network calls. |
| **Dashboard / API / CLI / MCP** | Fully functional. All traffic is local. |

**What CAN still egress when offline mode is on:**
- Docker image pulls (if the images aren't pre-cached)
- DNS lookups (if the host's DNS resolver isn't local)
- NTP time sync
- Ollama model pulls (if the model isn't pre-cached in the volume)

These are infrastructure-level, not application-level. An air-gapped deployment that pre-caches images and models will see zero application-level egress.

## Authentication and authorization

| Mechanism | Scope | Storage |
|-----------|-------|---------|
| **JWT** | Per-user session, short-lived (configurable expiry) | Signed with `JWT_SECRET_KEY`, refresh tokens in Postgres |
| **API keys** | Per-user or per-project, long-lived | SHA-256 hash stored in Postgres, raw key shown once at creation |
| **Role hierarchy** | VIEWER < TESTER < QA_ENGINEER < QA_LEAD < ADMIN | Stored as `String(20)` on `users` and `project_members` |
| **Project scope** | Every data query is filtered by project membership | `require_project_access()` dependency, `get_accessible_project_ids()` returns None for ADMIN (no filter) |
| **Dual auth** | Endpoints accept either JWT or API key | `get_current_user_or_api_key()` tries JWT first, then API key |

## PII redaction

All data passes through `privacy_service` before:
- **Persistence**: `sanitize_for_persistence()` before any DB write
- **Logging**: `sanitize_for_logging()` before structlog emission
- **LLM prompts**: `sanitize_for_llm()` before any LLM call
- **Reports**: `sanitize_for_report()` before PDF/HTML rendering

Placeholder is always `[REDACTED]`. Max depth: 10 levels. 37 key patterns + 13 regex patterns.

**What is NOT redacted:**
- Test names (considered non-PII)
- Error messages (may contain user data embedded by the test runner -- the redactor catches common patterns like emails, IPs, tokens, but cannot guarantee coverage of arbitrary application data)
- Commit hashes and branch names
- Project and run metadata

## Secret storage

Secrets (PATs, SMTP credentials, LLM API keys) are stored via `secret_service` using Fernet symmetric encryption. The encryption key is derived from `JWT_SECRET_KEY`. Secrets are:
- Never logged (structlog filters by key name)
- Never returned in API responses (only a `has_*` boolean hint)
- Never included in compliance pack exports
- Rotatable via the Settings UI (ADMIN only, audited)

## Known limitations

1. **Ollama has no auth.** Anyone with network access to the Ollama port (default 11434) can send inference requests. Deploy behind a firewall or use Docker network isolation.
2. **Redis has password auth but no TLS by default.** Production deployments should enable Redis TLS or use a private network.
3. **The MCP SSE transport exposes the API over HTTP.** The stdio transport is implicitly local. SSE should be placed behind a reverse proxy with TLS and auth in production.
4. **PII redaction is heuristic-based.** It catches common patterns but cannot guarantee 100% coverage of arbitrary user data embedded in test output. Customers with strict PII requirements should pre-sanitise test results before ingestion.
5. **`AI_OFFLINE_MODE` is a runtime flag, not a build-time flag.** A misconfigured deployment could toggle it off. For air-gapped environments, consider network-level egress controls as a defence-in-depth layer.
