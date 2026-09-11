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
| **Backend to Slack / Teams** | Notification title, body and failure metadata | Webhook URL held per deployment, per user or per team, gated by `AI_OFFLINE_MODE` (destination residency; `OFFLINE_NOTIFICATION_ALLOWED_HOSTS` names exceptions for the deployment's own webhooks only) |
| **Backend to SMTP relay** | Notification, digest and report emails; the SMTP test email; the health probe's login | SMTP credentials, TLS/STARTTLS, gated by `AI_OFFLINE_MODE` (relay residency; `OFFLINE_NOTIFICATION_ALLOWED_HOSTS` names exceptions) |
| **MCP Server to Backend** | REST API calls | Stdio uses its configured JWT; network MCP validates and forwards each caller's JWT without a shared service identity |
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
| **Slack / Teams notifications** | Delivered only to a destination that resolves to a loopback or private address — a self-hosted webhook on the LAN still works, a public one is refused. The deployment's own webhooks may also go to a host the operator names in `OFFLINE_NOTIFICATION_ALLOWED_HOSTS`; a webhook set per user or per team never uses that list. |
| **Email (SMTP)** | Same rule: an internal relay is allowed, a public relay is refused unless the operator names it. Applies to every SMTP connection the application makes: notifications, digests, report emails, the settings page's test email and the health probe. |
| **Integration-health probes** | A probe never dials where its integration may not: Slack's API and the SMTP relay only when on-box; Jira, GitHub, Splunk and OpenShift not at all. It reports `skipped`, with the reason. |
| **Splunk log search, OpenShift pod lookups** | **Not gated yet (re-audit N19, open).** With `SPLUNK_ENABLED` or `OCP_ENABLED` set, the triage agent's Splunk search (`tools/query_splunk.py`) and the OpenShift pod lookups (`services/ocp_client.py`, called during ingestion and by the triage agent) still call those APIs. Both settings default to false. Their health probes are skipped (row above). |
| **Ingestion** | Fully functional. No network calls, except the OpenShift pod lookup when `OCP_ENABLED` is set (row above). |
| **Analysis (rules/ML)** | Fully functional. No network calls. |
| **Dashboard / API / CLI / MCP** | Fully functional. All traffic is local. |

**What CAN still egress when offline mode is on:**
- Docker image pulls (if the images aren't pre-cached)
- DNS lookups (if the host's DNS resolver isn't local)
- NTP time sync
- Ollama model pulls (if the model isn't pre-cached in the volume)

These are infrastructure-level, not application-level. An air-gapped deployment that pre-caches images and models, names no host in `OFFLINE_NOTIFICATION_ALLOWED_HOSTS`, and does not enable the Splunk or OpenShift integrations while N19 is open, will see zero application-level egress.

Note on how the notification channels are gated: the check is **where the
destination resolves**, not what the channel is called. That is deliberate in
both directions — an air-gapped site's own Slack-compatible webhook or mail
relay is on the LAN and keeps working, while `smtp.gmail.com` is egress however
ordinary "email" sounds. A destination that cannot be resolved is refused, so
"we could not prove this stays on-box" denies rather than allows.

An operator who wants one hosted destination while keeping the rest of the
ceiling -- a SaaS Slack workspace, say -- names it in
`OFFLINE_NOTIFICATION_ALLOWED_HOSTS` (comma-separated; `.example.com` matches
subdomains). That is an exception the operator writes down, and the API warns
at startup about every configured destination offline mode will refuse.

The list is per host, and a hosted service puts every customer on the same
hosts: allow-listing `hooks.slack.com` admits every Slack workspace, and
`.webhook.office.com` every Teams tenant, not only the operator's. So the list
covers only the deployment's own destinations: the global Slack and Teams
webhooks and the SMTP relay, which an admin configures. A webhook a user sets
in their notification preferences, or a QA lead sets for a team, is judged by
residency alone, whatever the list says. For mail the relay is judged, not the
recipient: an allow-listed hosted relay delivers wherever an address points,
including a user's own email override.

Until 2026-09-10 these three channels were gated by nothing at all and were
absent from the table above, which is how it went unnoticed (re-audit H10).

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
3. **The MCP SSE transport exposes privileged tools over the network.** It requires a caller bearer token and binds the session to that credential. The stdio transport is implicitly local. SSE still belongs behind a TLS-terminating reverse proxy in production.
4. **PII redaction is heuristic-based.** It catches common patterns but cannot guarantee 100% coverage of arbitrary user data embedded in test output. Customers with strict PII requirements should pre-sanitise test results before ingestion.
5. **`AI_OFFLINE_MODE` is a runtime flag, not a build-time flag.** A misconfigured deployment could toggle it off. For air-gapped environments, consider network-level egress controls as a defence-in-depth layer.
