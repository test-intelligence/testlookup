# TestLookup Threat Model

This document describes where data flows, what guarantees the offline mode provides, and what boundaries the system enforces. It was last reviewed against the code on 2026-11-05 (commit 1ff1e78b); report a gap through the process in [SECURITY.md](SECURITY.md).

## Data flow summary

```
Test Runner --> Ingest API (REST) --> Backend
                                       |
                        +--------------+--------------+
                        |              |              |
                   PostgreSQL       MongoDB        Valkey
                  (relational)     (event log)    (cache/broker)
                        |
                   Celery Worker
                        |
            +-----------+-----------+
            |                       |
         Ollama (local)     External LLMs (cloud)
         [offline-safe]     [requires AI_OFFLINE_MODE=false]
            |
         versitygw (S3-compatible object store)
```

## Trust boundaries

| Boundary | What crosses it | Protection |
|----------|----------------|------------|
| **User to Backend** | HTTP requests (JWT / API key) | TLS in production, JWT validation, role-based access control, project-scope tenant isolation |
| **Backend to PostgreSQL** | SQL queries | Async connection pool with timeout, parameterised queries (no string interpolation), row-level tenant isolation via `project_id` WHERE clauses |
| **Backend to MongoDB** | Pipeline event writes | Authenticated connection, write-only from backend (no user-facing reads bypass the backend) |
| **Backend to Valkey** | Cache reads/writes, Celery task dispatch, token denylist | Password-authenticated, private network only |
| **Backend to object storage** (versitygw, or any S3-compatible store) | Reports, attachments, exports, compliance packs, RAG documents | Authenticated via access key, bucket policy, private network |
| **Backend to Ollama** | LLM inference prompts | Private network only, no auth (Ollama doesn't support it), PII-redacted input |
| **Backend to External LLMs** | LLM inference prompts | API key auth, HTTPS, PII-redacted input, gated by `AI_OFFLINE_MODE`; a provider key from the environment is sent only to the provider's own endpoint, and a custom base URL needs a key stored in Settings > AI Configuration |
| **Backend to GitHub** | Check run posts | PAT auth, HTTPS, gated by `AI_OFFLINE_MODE` + `github_checks` flag |
| **Backend to Webhook receivers** | Event payloads | HMAC-SHA256 signed, HTTPS recommended, gated by `AI_OFFLINE_MODE` + `outbound_webhooks` flag |
| **Backend to Slack / Teams** | Notification title, body and failure metadata | Webhook URL held per deployment, per user or per team, gated by `AI_OFFLINE_MODE` (destination residency; `OFFLINE_NOTIFICATION_ALLOWED_HOSTS` names exceptions for the deployment's own webhooks only) |
| **Backend to SMTP relay** | Notification, digest and report emails; the SMTP test email; the health probe's login | SMTP credentials, TLS/STARTTLS, gated by `AI_OFFLINE_MODE` (relay residency; `OFFLINE_NOTIFICATION_ALLOWED_HOSTS` names exceptions; recipients judged by `OFFLINE_EMAIL_ALLOWED_RECIPIENT_DOMAINS`) |
| **MCP Server to Backend** | REST API calls | Stdio uses its configured JWT; network MCP validates and forwards each caller's JWT without a shared service identity |
| **CLI to Backend** | REST API calls | Same JWT/API key auth |
| **SAML identity provider to Backend** (SSO) | Sign-in responses and logout requests | Signatures verified against the configured IdP certificate (signxml over lxml with entity resolution and network access off; defusedxml for the other SAML parsing); a response must answer a request TestLookup issued (`InResponseTo`), and replayed or unsolicited messages are refused; the relay state is burned before use |
| **SCIM client to Backend** (provisioning) | User and group create/update/deactivate | Bearer tokens issued by an ADMIN at `/api/v1/scim-tokens`, stored as hashes, checked on every `/scim` call |
| **Anyone holding a share link** | One shared run report (HTML), no JWT | 64-character random token, only its hash stored; default expiry 7 days; every view audited with an 8-character token hint. Creating a link needs the QA_LEAD role in the run's project. Treat the link as a bearer credential |
| **Backend to knowledge sources** (RAG connectors) | Fetches of external documents | Only while the `knowledge_rag` flag is on; URL sources of both kinds (`external_url` and `internal_url`) must pass a scheme check and the deployment-wide domain allow-list an ADMIN maintains, on create, on every sync and for each redirect target, and neither is fetched in offline mode; the allow-list is the server-side request forgery (SSRF) control. Jira and Confluence ids must match the exact id shape before they go into a request path |
| **Backend to Jira, GitLab, Splunk, OpenShift** | Issue creation, comments, log searches, pod lookups | Admin-configured credentials over HTTPS; Splunk and OpenShift lookups are no-ops under `AI_OFFLINE_MODE`, and integration-health probes never dial these services while it is on. Jira uses one service account for the whole deployment: a project reaches only the Jira project in its Jira key (set by an administrator); a project without one uses the default Jira project, and only a QA lead or administrator may name another. Jira knowledge sources follow the same rule. Restricting the service account in Jira remains the outer limit |
| **Test runners and users to the upload endpoints** | Result files and ZIP archives | A request body is counted while it is received and refused with 413, before the credential is checked, once it passes the route's limit: 100 MiB on the result and archive upload routes (`REQUEST_BODY_UPLOAD_MAX_BYTES`) and 10 MiB on every other route (`REQUEST_BODY_MAX_BYTES`). One result file may be at most 50 MB; that check runs in the handler, after the body has been received, and bounds what is held in memory (the file is read in 1 MB chunks). One report may carry at most `INGEST_MAX_RESULTS_PER_UPLOAD` results. Report ZIP archives are capped at 200 MB uncompressed, 5,000 entries, 50 MB per entry and a 100:1 compression ratio (`MAX_ARCHIVE_*` settings); a project-data import archive is a different format with its own, larger caps. XML formats (JUnit, TestNG, NUnit, TRX, xUnit, Robot) are parsed with defusedxml. There are two separate ingest rate limits, each per project per minute: 200 batches, shared by the JSON, file and live-batch ingest routes (`INGEST_RATE_LIMIT_PER_MINUTE`), and 20,000 single events on the live-event route (`INGEST_EVENT_RATE_LIMIT_PER_MINUTE`). A build written straight to the object store (`<project>/runs/<build>/`) is held to the same result cap and to 50 MiB per object (`INGEST_DROP_MAX_OBJECT_BYTES`) |

## Offline mode guarantees

When `AI_OFFLINE_MODE=true`:

| Category | Behaviour |
|----------|-----------|
| **LLM inference** | Only local Ollama is used. No cloud LLM calls (OpenAI, Gemini, etc.) are made regardless of `LLM_PROVIDER` config. |
| **GitHub Checks** | Silent no-op. `github_checks_service` returns early before any network call. |
| **Outbound webhooks** | Silent no-op. `webhook_service.emit_event` returns 0 without touching the DB. |
| **RAG faithfulness (Ragas)** | Falls back to Ollama evaluator regardless of `rag_faithfulness_evaluator` config. |
| **Weekly retro digest narrative** | Falls back to the deterministic template path without calling the LLM. |
| **Feature flags** | Still functional (flags resolve via Valkey/Postgres, both local). The offline gate is checked BEFORE the feature flag in every outbound service. |
| **Slack / Teams notifications** | Delivered only to a destination that resolves to a loopback or private address — a self-hosted webhook on the LAN still works, a public one is refused. The deployment's own webhooks may also go to a host the operator names in `OFFLINE_NOTIFICATION_ALLOWED_HOSTS`; a webhook set per user or per team never uses that list. |
| **Email (SMTP)** | Same rule: an internal relay is allowed, a public relay is refused unless the operator names it. Applies to every SMTP connection the application makes: notifications, digests, report emails, the settings page's test email and the health probe. The **recipients** are judged too: with `OFFLINE_EMAIL_ALLOWED_RECIPIENT_DOMAINS` set, every recipient must be in it; without it, an on-box relay may deliver and a named off-box relay is refused, because it would deliver to any mailbox. |
| **Integration-health probes** | A probe never dials where its integration may not: Slack's API and the SMTP relay only when on-box; Jira, GitHub, Splunk and OpenShift not at all. It reports `skipped`, with the reason. |
| **Splunk log search, OpenShift pod lookups** | Silent no-op. The triage agent's Splunk search and the OpenShift pod lookups (ingestion and triage) return without a network call, whatever `SPLUNK_ENABLED` and `OCP_ENABLED` say. |
| **Ingestion** | Fully functional. No network calls. |
| **Analysis (rules/ML)** | Fully functional. No network calls. |
| **Dashboard / API / CLI / MCP** | Fully functional. All traffic is local. |

**The switch in Settings > AI covers LLM calls only.** Every guarantee in this table is tied to `AI_OFFLINE_MODE` in the deployment's environment. The "Offline Mode" switch on the Settings > AI page can turn offline mode on for LLM calls (model inference and the Ollama availability probes) when the environment leaves it off, but it does not stop Slack, Teams, email, webhooks, GitHub, GitLab, Jira, Splunk, OpenShift or knowledge-source requests: those read the environment variable only. For a deployment that must make no outbound requests, set `AI_OFFLINE_MODE=true` in the environment.

**What CAN still egress when offline mode is on:**
- Docker image pulls (if the images aren't pre-cached)
- DNS lookups (if the host's DNS resolver isn't local)
- NTP time sync
- Ollama model pulls (if the model isn't pre-cached in the volume)

These are infrastructure-level, not application-level. An air-gapped deployment that pre-caches images and models, and names no host in `OFFLINE_NOTIFICATION_ALLOWED_HOSTS` will see zero application-level egress. (An on-box mail relay forwards wherever its own MTA policy sends mail; `OFFLINE_EMAIL_ALLOWED_RECIPIENT_DOMAINS` fences that at the application too.)

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
absent from the table above, which is how it went unnoticed.

## Authentication and authorization

| Mechanism | Scope | Storage |
|-----------|-------|---------|
| **JWT** | Per-user session, short-lived (configurable expiry) | Signed with `JWT_SECRET_KEY`, refresh tokens in Postgres (hashes only); in the browser, `localStorage` (see below) |
| **API keys** | Per-user or per-project, long-lived | SHA-256 hash stored in Postgres, raw key shown once at creation |
| **Role hierarchy** | VIEWER < TESTER < QA_ENGINEER < QA_LEAD < ADMIN | Stored as `String(20)` on `users` and `project_members` |
| **Project scope** | Every data query is filtered by project membership | `require_project_access()` dependency, `get_accessible_project_ids()` returns None for ADMIN (no filter) |
| **Dual auth** | Endpoints accept either JWT or API key | `get_current_user_or_api_key()` tries JWT first, then API key |
| **MFA** | Optional per user, enforceable by policy | TOTP (RFC 6238) seed per user with one-time recovery codes; enrolling, disabling and reissuing codes are audited |
| **SSO / SCIM** | Sign-in through a SAML IdP; provisioning from the IdP | See the SAML and SCIM rows under trust boundaries |

### Where the browser keeps the session

The web app keeps the access token, the refresh token and the signed-in user's profile in the browser's `localStorage`, so a reload or a new tab stays signed in. Any script that runs on the app's origin can read `localStorage`. A script-injection flaw in the web app, or a compromised script in its bundle, would therefore expose both tokens, which an `HttpOnly` cookie would not. This is an accepted trade-off. What limits the damage:

- **Short access tokens.** An access token lasts 30 minutes by default (`JWT_ACCESS_TOKEN_EXPIRE_MINUTES`). The cloud example files suggest the same value.
- **Refresh rotation with replay detection.** A refresh token works once and is replaced on use. Presenting one that was already replaced (after a leeway of `JWT_REFRESH_REUSE_LEEWAY_SECONDS`, 10 seconds by default) revokes every refresh token of that user and ends their access tokens, so a copied refresh token stops working as soon as both the real browser and the copy have used it. Signing out and changing the password revoke them as well.
- **Content Security Policy.** The page loads scripts only from its own origin (`script-src 'self'`, so no inline script and no `eval`), allows no plugins (`object-src 'none'`) and no foreign `<base>` or form target.

The stronger design, a refresh token in an `HttpOnly; Secure; SameSite=Strict` cookie with the access token held in memory only, is not implemented.

### Sign-in rate limits and the client address

The sign-in routes are limited per client address (10 sign-ins a minute), and `/api/v1/auth/login` also per typed account name (`LOGIN_ATTEMPTS_PER_USERNAME_PER_MINUTE`, 20 a minute by default), whatever address the attempts come from. In production and staging the counters are kept in Valkey, so a limit is one figure for every worker and pod; while Valkey cannot be reached each worker process counts on its own (`RATE_LIMIT_SHARED_STORAGE`). The account lockout after repeated wrong passwords is separate and stored in Postgres.

Behind a proxy, the client address used for these limits and written to audit rows comes from `X-Forwarded-For`, and only when the connection arrives from a peer listed in `TRUSTED_PROXY_IPS`: it is the right-most entry of the header that is not itself a trusted proxy. Every proxy placed in front of TestLookup must therefore overwrite `X-Forwarded-For` or append the address it saw to it; a trusted proxy that passes the header through unchanged lets a client pick its own address, and with it a fresh rate-limit budget.

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

Secrets (PATs, SMTP credentials, LLM API keys) are stored via `secret_service` using Fernet symmetric encryption. The key is derived from `APP_SECRET_KEY` with PBKDF2-HMAC-SHA256, so the raw value is never used as a key, and a key below the minimum length fails closed. During a rotation, `APP_SECRET_KEY_PREVIOUS` keeps existing ciphertext readable (MultiFernet) until it is re-encrypted. Rotating `JWT_SECRET_KEY` signs users out but does not re-key stored secrets. Secrets are:
- Never logged (structlog filters by key name)
- Never returned in API responses (only a `has_*` boolean hint)
- Never included in compliance pack exports
- Rotatable via the Settings UI (ADMIN only, audited)

## Known limitations

1. **Ollama has no auth.** Anyone with network access to the Ollama port (default 11434) can send inference requests. Deploy behind a firewall or use Docker network isolation.
2. **Valkey has password auth but no TLS by default.** Production deployments should enable Valkey TLS or keep it on a private network.
3. **The MCP SSE transport exposes privileged tools over the network.** It requires a caller bearer token and binds the session to that credential. The stdio transport is implicitly local. SSE still belongs behind a TLS-terminating reverse proxy in production.
4. **A share link is a bearer credential.** Anyone who has the URL can read that report until the link expires or is revoked. Share only with people who may see the run.
5. **PII redaction is heuristic-based.** It catches common patterns but cannot guarantee 100% coverage of arbitrary user data embedded in test output. Customers with strict PII requirements should pre-sanitise test results before ingestion.
6. **`AI_OFFLINE_MODE` is a runtime flag, not a build-time flag.** A misconfigured deployment could toggle it off. For air-gapped environments, consider network-level egress controls as a defence-in-depth layer.
