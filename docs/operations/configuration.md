# Configuration reference

[Documentation home](../README.md) · [Feature flags](feature-flags.md) · [Integrations](integrations.md) · [Deployment](deployment.md)

This page explains every setting the TestLookup backend reads: what it does, its default, when to change it, and which ones are secrets or are checked in production. It is written by hand from `backend/app/core/config.py` (class `Settings`), the root `.env.example`, and the `os.getenv` reads elsewhere in the code. The [generated settings table](../reference/configuration.md) lists the same `Settings` fields with their exact declared types and constraints, and is regenerated from source in CI; use it when you need the literal declaration.

## How settings are loaded

- The backend, the Celery workers and the beat scheduler all read the same `Settings` class (pydantic-settings). Each value comes from, in order: a process environment variable, then a `.env` file in the process working directory, then the default in code.
- Names are **case sensitive** (`case_sensitive=True`): `LLM_MODEL` works, `llm_model` does not.
- An **empty value counts as unset** (`env_ignore_empty=True`): `PUBLIC_BASE_URL=` leaves the default in force. To clear a value whose default is not empty you must set something explicit.
- Unknown variables are ignored (`extra="ignore"`), so a typo fails silently. Check the effective value through the UI pages listed below or `GET /health/details`.
- Docker Compose passes the root `.env` to the backend, worker, beat, migration and seed containers (`env_file: .env`) and then overrides the connection settings (`DATABASE_URL`, `MONGO_URI`, `REDIS_URL`, `CELERY_*`, `POSTGRES_HOST`, `MINIO_ENDPOINT`, `OLLAMA_BASE_URL`, `CHROMA_HOST`/`CHROMA_PORT`, pool sizes, `APP_ENV`, `DEV_AUTO_LOGIN_ENABLED`) with the in-network values. Kubernetes reads `testlookup-config` (ConfigMap) and `testlookup-secrets` (Secret) through `envFrom`; see `k8s/base/configmap.yaml` and `k8s/base/secrets.yaml`.
- Some settings are only **defaults** that an administrator can override at runtime from the UI. The stored value wins over the environment:

  | UI page | Stored as | Settings it overrides |
  |---|---|---|
  | `/settings/ai` | `app_settings` row `ai_config` (keys in `secret_refs`) | `LLM_PROVIDER`, `LLM_MODEL`, `LLM_TEMPERATURE`, `LLM_MAX_TOKENS`, `EMBEDDING_PROVIDER`, `EMBEDDING_MODEL`, `AI_CONFIDENCE_THRESHOLD`, `AI_TIMEOUT_SECONDS`, `ANALYSIS_MODE`, `DEEP_INVESTIGATION_ENABLED`, `FINETUNE_ENABLED`, the four provider API keys. A stored value overrides the environment in both directions (a stored "off" beats an environment "on" and vice versa); a stored value that is not valid (for example the text `"false"` instead of a boolean, or a timeout outside 5-1800) is ignored and the environment value applies. `GET /api/v1/settings/ai` reports each of these with a `*_source` field: `ai_config` (stored, "Set here") or `env_default` (the environment value). After the first save every field is stored, so they all read `ai_config`. Changes apply within a minute (a 60 s cache, invalidated by every save). `AI_OFFLINE_MODE` can only be tightened there, never loosened (see [AI and LLM providers](#ai-and-llm-providers)). |
  | `/settings/integrations` | `app_settings` row `integrations_config` + `secret_refs` | `JIRA_ENABLED`, `JIRA_DOMAIN`, `JIRA_EMAIL`, `JIRA_API_TOKEN`, `JIRA_DEFAULT_PROJECT_KEY`; `SPLUNK_ENABLED`, `SPLUNK_BASE_URL`, `SPLUNK_API_TOKEN`; `OCP_ENABLED`, `OCP_API_URL`, `OCP_SA_TOKEN`, `OCP_DEFAULT_NAMESPACE`; `SLACK_ENABLED`, `SLACK_WEBHOOK_URL`, `TEAMS_ENABLED`, `TEAMS_WEBHOOK_URL`. Per key, on every runtime path (see the note below). `SPLUNK_INDEX` has no field here: a project's **Splunk Index** comes before it. The API still accepts `slack_default_channel` and ignores it, and still stores `github_token` / `github_repo`: the token feeds only the deployment-wide GitHub row of Integration health, the repository nothing. |
  | `/settings/notifications` (Email) | `app_settings` row `smtp_config` + `secret_refs` | `SMTP_*` |
  | `/settings/storage` | `app_settings` row `storage_config` | `STORAGE_BACKEND`, `MINIO_ENDPOINT`, `MINIO_BUCKET_NAME`, `MINIO_USE_SSL`, `CHROMA_HOST`, `CHROMA_PORT`, `CHROMA_COLLECTION` (not the access keys) |

  **The integrations rule** (one resolver for every path: the Splunk and OpenShift agent tools and server-side look-ups, every Jira path including the triage stage, defect promotion and `POST /api/v1/integrations/jira`, Slack, Teams and the health probes; first match wins): `AI_OFFLINE_MODE=true` refuses every outbound call (read live, never cached); a project's own value comes next (its Splunk Index; for OpenShift the run's own namespace, then the project's); then the value saved at `/settings/integrations`; then the environment variable, which applies only to a key never saved. A text saved empty (or only spaces) counts as never saved, so clearing a field hands it back to the environment; a saved boolean always counts (saved `false` beats an environment `true`); a cleared token falls back to the environment. `GET /api/v1/settings/integrations` names the environment-sourced keys in `environment_fields`. Each process caches a project's resolved configuration for 30 seconds (the saving process drops it at once). An install whose first save, before W10, copied the environment's values into the stored row keeps those copies: save a field empty to hand it back. The per-project GitHub and GitLab integrations have no environment layer; `GITHUB_TOKEN` / `GITHUB_REPO` no longer feed any commit lookup. `DEEP_INVESTIGATION_ENABLED`, `FINETUNE_ENABLED` and `ANALYSIS_MODE` follow the value stored at `/settings/ai` (they were read from the environment before W04). Details: [Integrations](integrations.md#which-value-applies).

### Marks used below

- **Secret**: a credential. Keep it out of version control; in Kubernetes put it in the Secret, not the ConfigMap.
- **Production check**: verified at API startup when `APP_ENV` is `production` or `staging` (see [Production startup checks](#production-startup-checks)).
- **Required in production**: the stack does not work, or is insecure, with the default.
- **Not read**: declared in `Settings` but no code reads it today. Setting it has no effect.

## Production startup checks

`Settings.validate_production_secrets()` runs in the API's startup hook (`backend/app/main.py`) whenever `APP_ENV` is `production` or `staging`. A **CRITICAL** finding stops the process with `Refusing to start in <env> with N critical security issue(s)`; a **WARNING** is logged and startup continues.

| Severity | Condition |
|---|---|
| CRITICAL | `JWT_SECRET_KEY` is empty or a placeholder |
| CRITICAL | `APP_SECRET_KEY` is empty or a placeholder |
| CRITICAL | `WEBHOOK_SECRET` is empty or a placeholder |
| CRITICAL | `MONGO_URI` carries a password that is a placeholder (a password-less URI passes) |
| CRITICAL | `DEV_AUTO_LOGIN_ENABLED` is true |
| WARNING | `JIRA_ENABLED` is true and `JIRA_WEBHOOK_SECRET` is empty or a placeholder (the Jira webhook then refuses every delivery) |
| WARNING | `SSO_ENABLED` is true and `SAML_BASE_URL` is still `http://localhost:8000` |
| WARNING | an entry of `CORS_ORIGINS` contains `*` |

A value counts as a **placeholder** when it is empty, when it contains `change-me`, `change_me`, `replace-with`, `replace_with`, `your-` or `set-a-` (case-insensitive), or when the whole value is one angle-bracketed token such as `<set-a-strong-password>`. Values generated with `openssl rand -hex 32` never match.

Two more production-only behaviours:

- With `APP_ENV=production` the API also queries PostgreSQL at startup (`SHOW max_connections`, reserved connections, the role's connection limit) and refuses to start when the server's `max_connections` is below `PG_FLEET_MAX_CONNECTIONS` or when `PG_FLEET_REQUIRED_CONNECTIONS` exceeds the usable capacity. See [PostgreSQL connection budget](database-connection-budget.md).
- `DEV_AUTO_LOGIN_ENABLED` defaults to **true in code**. The Compose files, the Kubernetes base (`k8s/base/configmap.yaml`) and the `homelab` and `openshift-artifactory` overlays set it to `false`. A deployment that writes its own ConfigMap or environment must set `DEV_AUTO_LOGIN_ENABLED=false` too, or the API refuses to start in production.

## Core application and HTTP edge

| Setting | Type | Default | Description |
|---|---|---|---|
| `APP_NAME` | str | `TestLookup` | Service name in logs and in the `/health` response. Rarely changed. |
| `APP_ENV` | `development` \| `staging` \| `production` | `development` | Deployment mode. `production` and `staging` enable the [startup checks](#production-startup-checks), disable the passwordless dev login and hide SQL parameters in database errors (`production` only); `development` turns on SQL echo and the OTLP stdout fallback. The production container image sets `APP_ENV=production` itself, and Compose defaults it to `production`; `make dev` / `scripts/gen-dev-env.sh` write `development` into `.env`. **Required in production.** |
| `APP_SECRET_KEY` | str | `change-me-in-production` | Encrypts secrets at rest (`secret_refs`: integration tokens, SMTP password, provider API keys, webhook signing secrets) and MFA secrets, and signs decision-evidence snapshots, evidence attestations and the cached membership data (HMAC). Generate with `openssl rand -hex 32`. Changing it makes stored secrets unreadable unless you rotate with `APP_SECRET_KEY_PREVIOUS`. **Secret. Production check.** |
| `APP_SECRET_KEY_PREVIOUS` | str (optional) | unset | Previous `APP_SECRET_KEY` during a key rotation. When set, decryption tries `[current, previous]` (MultiFernet) while new writes use the current key. Rotate, let values be re-encrypted on their next write, then remove it. **Secret.** |
| `APP_DEBUG` | bool | `false` | **Not read.** |
| `APP_VERSION` | str | `0.0.1` | Version reported by `/health`, in logs, in the `app_info` metric and as the OpenTelemetry service version. Set it to the release you deploy. |
| `BUILD_REVISION` | str | empty | Commit the image was built from; reported by `GET /health/details`. Set by the image build (`backend/Dockerfile` build argument), not by operators. Empty reads as `unknown`. |
| `BUILD_DATE` | str | empty | Build timestamp, same source and purpose as `BUILD_REVISION`. |
| `CORS_ORIGINS_RAW` (env name `CORS_ORIGINS` or `CORS_ORIGINS_RAW`) | str | `http://localhost:3000,http://localhost:5173` | Origins allowed to make credentialed cross-origin calls. Comma-separated, or a JSON list. Its first entry is also the fallback for `PUBLIC_BASE_URL`. Set it to the URL users open (for example `https://testlookup.example.com`). A `*` triggers a production warning. |
| `PUBLIC_BASE_URL` | str | empty | Externally reachable dashboard URL, used to build links in Jira issues, Slack/Teams messages, emails, GitHub check runs, GitLab notes and report links, and the page a browser returns to after SAML sign-in (`<PUBLIC_BASE_URL>/sso/callback`), so it must be the URL users open. Empty falls back to the first `CORS_ORIGINS` entry, then `http://localhost:3000`. Set it explicitly in production. |
| `DEV_AUTO_LOGIN_ENABLED` | bool | `true` | Enables `POST /api/v1/auth/dev-login`, which issues an admin token without credentials, and only when `APP_ENV=development`. **Production check** (CRITICAL when true). Set `false` everywhere except a local development stack. |
| `TRUSTED_PROXY_IPS` | str | empty | Peers (addresses or CIDR networks, comma-separated) allowed to supply `X-Forwarded-For` / `X-Forwarded-Proto`. Empty trusts nothing, so the client address is the immediate peer. Set it to your ingress or load-balancer range so rate limits, lockout and audit logs see the real client. Deliberately not named `FORWARDED_ALLOW_IPS`, which gunicorn would parse and reject when given a CIDR. `docker-compose.release.yml` sets `172.16.0.0/12`. |
| `FORM_URLENCODED_MAX_BYTES` | int | `65536` | Largest accepted `application/x-www-form-urlencoded` body (login and token exchanges are a few hundred bytes). |
| `FORM_URLENCODED_ACS_MAX_BYTES` | int | `2097152` | The same limit for the SAML ACS endpoint, whose `SAMLResponse` is larger. |
| `HTTP_VERIFY_TLS` | bool | `true` | Certificate verification for outbound HTTPS from the shared HTTP client (Jira, Slack, Teams, GitHub, GitLab, webhooks, hosted LLM endpoints, knowledge connectors). `false` disables verification and logs a warning; only for isolated labs. Prefer `HTTP_CA_BUNDLE`. |
| `HTTP_CA_BUNDLE` | str (optional) | unset | Path to a PEM bundle of extra trusted roots (corporate CA, TLS-intercepting proxy). Verification stays strict. Takes precedence over `HTTP_VERIFY_TLS`. See [Corporate CA and TLS interception](integrations.md#corporate-ca-and-tls-interception). |
| `WEBHOOK_SECRET` | str | `change-me-webhook-secret` | Shared secret for `POST /webhooks/minio` (`Authorization: Bearer <secret>` or `X-Webhook-Secret`) and, while `LIVE_EVENTS_REQUIRE_PROJECT_KEY=false`, for `POST /ws/events/{run_id}`. Generate with `openssl rand -hex 32`. **Secret. Production check.** |
| `LIVE_EVENTS_REQUIRE_PROJECT_KEY` | bool | `true` | When true, `POST /ws/events/{run_id}` accepts only a project-scoped API key and refuses the shared `WEBHOOK_SECRET` (which names no project). Leave true unless an old direct integration still sends the shared secret. |

## Authentication, MFA, SSO and SCIM

| Setting | Type | Default | Description |
|---|---|---|---|
| `JWT_SECRET_KEY` | str | `change-me-jwt-secret` | HMAC key that signs access and refresh tokens. Generate with `openssl rand -hex 32`. Changing it signs every user out. **Secret. Production check.** |
| `JWT_ALGORITHM` | str | `HS256` | JWT signing algorithm. Leave as is; the MCP server's token check also assumes HS256. |
| `JWT_ACCESS_TOKEN_EXPIRE_MINUTES` | int | `720` | Access-token lifetime (12 hours). Lower it for stricter environments; the SPA refreshes with the refresh token. |
| `JWT_REFRESH_TOKEN_EXPIRE_DAYS` | int | `7` | Refresh-token lifetime. |
| `AUTH_REVOCATION_FAIL_OPEN` | bool | `false` | Token revocation is checked in Valkey/Redis. Default: when that store is unreachable, authenticated requests get 503 instead of being accepted unchecked. `true` restores fail-open (a revoked token may keep working during an outage) and logs an ERROR on every use. Environment only; there is no UI toggle. |
| `MFA_ISSUER_NAME` | str | `TestLookup` | Issuer label shown in authenticator apps. |
| `MFA_CHALLENGE_TTL_SECONDS` | int | `300` | Lifetime of the short-lived token issued between "password accepted" and "second factor accepted". Keep it small. |
| `MFA_RECOVERY_CODE_COUNT` | int | `10` | Single-use recovery codes minted when a user enables MFA. |
| `MFA_BREAKGLASS_ENABLED` | bool | `false` | Read by `backend/scripts/mfa_breakglass.py`, which refuses to reset a user's MFA unless this is true in the backend environment. Every use writes an `MFA_BREAKGLASS_RESET` identity event. Turn it on only for the duration of a recovery. |
| `REGISTRATION_ENABLED` | bool | `false` | Self-service sign-up. While false, `POST /api/v1/auth/register` answers 403 (`registration_disabled`) and the sign-in page hides **Register** (it reads the public `GET /api/v1/auth/options`). When true, a new account is a `VIEWER` with no project membership and must change its password at first sign-in. Administrators create accounts under Settings, Users (`POST /api/v1/users`, which returns a one-time temporary password) either way. |
| `SSO_ENABLED` | bool | `false` | Master switch for SAML 2.0 and OIDC SSO (`/api/v1/sso/login-url`, `/acs`, `/slo`, `/oidc/login`, `/oidc/callback`, `/exchange`, `/status`) and for SSO enforcement at password login. See [SSO](integrations.md#single-sign-on-saml-20). |
| `SECRET_PROVIDER_VAULT_ADDR` | str | `""` | Vault (or OpenBao) address for `vault:<path>#<key>` secret references (KV v2). Empty = references cannot resolve (the field reads as unset). Deployment-only; never set from the UI. |
| `SECRET_PROVIDER_VAULT_AUTH` | str | `token` | How the backend logs in to Vault: `token`, `approle` or `kubernetes`. |
| `SECRET_PROVIDER_VAULT_TOKEN` | str | `""` | Vault token for `token` auth. |
| `SECRET_PROVIDER_VAULT_ROLE_ID` | str | `""` | AppRole role id for `approle` auth. |
| `SECRET_PROVIDER_VAULT_SECRET_ID` | str | `""` | AppRole secret id for `approle` auth. |
| `SECRET_PROVIDER_VAULT_K8S_ROLE` | str | `""` | Vault role for `kubernetes` auth (the pod's service-account token is sent). |
| `SECRET_PROVIDER_VAULT_MOUNT` | str | `secret` | The KV v2 mount the reference paths live under. |
| `SECRET_PROVIDER_VAULT_NAMESPACE` | str | `""` | Vault Enterprise namespace (`X-Vault-Namespace`), when used. |
| `SECRET_PROVIDER_VAULT_CACHE_SECONDS` | int | `60` | How long a resolved reference is cached in the process. |
| `SCIM_ENABLED` | bool | `false` | Master switch for SCIM 2.0 provisioning under `/api/v1/scim/v2`. While false every SCIM request answers 404. |
| `SAML_SP_ENTITY_ID` | str | `https://testlookup.io/saml/metadata` | Service-provider entity ID returned by `GET /api/v1/sso/metadata`. Set it to an identifier you control (for example `https://testlookup.example.com/saml/metadata`) and use the same value in the IdP and in the SSO configuration's `sp_entity_id`. |
| `SAML_BASE_URL` | str | `http://localhost:8000` | Base URL used to build the ACS URL (`<base>/api/v1/sso/acs`) shown in SP metadata. Set it to the public URL that reaches the backend. Production warning while it is the localhost default and SSO is on. |
| `SSO_ADMIN_FALLBACK_ENABLED` | bool | `true` | While SSO enforcement is `SSO_REQUIRED`, lets `ADMIN` users still sign in with a password (logged as `ADMIN_FALLBACK_LOGIN`). Keep it on until SSO is proven; it is your way back in if the IdP breaks. |
| `SSO_ALLOW_PRIVATE_IDP_ENDPOINTS` | bool | `false` | The SSO "test connection" call refuses private and link-local IdP addresses (SSRF guard). Set true only for an internal-only IdP. |
| `SAML_ALLOW_IDP_INITIATED` | bool | `false` | Accept assertions without `InResponseTo` (IdP-initiated login). Off by default: each response must answer a request minted by `/login-url`. Turning it on drops that replay-protection leg; the assertion-ID single-use cache, audience, recipient and signature checks still apply. |
| `SAML_CLOCK_SKEW_SECONDS` | int | `60` | Tolerated IdP/SP clock difference for `NotBefore` / `NotOnOrAfter`. |
| `SSO_MAX_PROVISIONED_ROLE` | `VIEWER` \| `TESTER` \| `QA_ENGINEER` \| `QA_LEAD` \| `ADMIN` | `ADMIN` | Ceiling on the role an IdP group mapping (SSO just-in-time provisioning) or a SCIM provision can grant. Higher resolved roles are clamped and logged at WARNING. Set `QA_LEAD` to refuse IdP-driven admin grants. An invalid value stops startup. |

## Databases

### PostgreSQL

| Setting | Type | Default | Description |
|---|---|---|---|
| `DATABASE_URL` | str | empty | The SQLAlchemy async URL the application connects with, for example `postgresql+asyncpg://testlookup_user:<password>@postgres:5432/testlookup`. **Required** (the app has no default connection). **Secret** (it contains the password). |
| `POSTGRES_HOST` | str | `localhost` | Used only by Alembic (`backend/migrations/env.py`) to build a URL when `DATABASE_URL` is unset, and by Compose for its own wiring. The application itself reads only `DATABASE_URL`. |
| `POSTGRES_PORT` | int | `5433` | As `POSTGRES_HOST`. `5433` is the host port the Compose stack publishes. |
| `POSTGRES_DB` | str | `testlookup` | As `POSTGRES_HOST`; Compose also creates the database with this name. |
| `POSTGRES_USER` | str | `testlookup_user` | As `POSTGRES_HOST`; Compose creates this role. |
| `POSTGRES_PASSWORD` | str | empty | As `POSTGRES_HOST`; Compose sets the database password from it. **Secret.** |
| `PG_POOL_SIZE` | int (optional) | unset | SQLAlchemy pool size **per process**. Unset uses 5 (development), 15 (staging) or 2 (production). Every gunicorn worker and every Celery child owns its own pool, so multiply before raising it. |
| `PG_MAX_OVERFLOW` | int (optional) | unset | Extra connections above the pool per process. Unset uses 10 / 30 / 1 for development / staging / production. |
| `PG_POOL_RECYCLE` | int | `1800` | Seconds before a pooled connection is recycled; the engine caps it at 900. |
| `PG_POOL_TIMEOUT` | int | `30` | Seconds to wait for a free pooled connection before failing. |
| `PG_PROCESS_ROLE` | `api` \| `worker` \| `operation` | `operation` | Labels the connection (`application_name=testlookup-<role>`). In production a `worker` also gets `pool_pre_ping`. Set by the manifests (`api` for the backend, `worker` for Celery). |
| `PG_PROCESSES_PER_POD` | int | `1` | Processes per pod sharing this configuration (gunicorn workers or Celery concurrency). Reported by the performance settings page for the connection-budget arithmetic. |
| `PG_FLEET_MAX_CONNECTIONS` | int | `400` | The server's `max_connections` the deployment was sized for. Production startup fails if the real server reports less. |
| `PG_FLEET_OPERATIONAL_RESERVE` | int | `50` | Connections kept free for operators, migrations and tools. |
| `PG_FLEET_SUPERUSER_RESERVED_CONNECTIONS` | int | `3` | Matches the server's `superuser_reserved_connections`; used by the Compose budget gate. |
| `PG_FLEET_RESERVED_CONNECTIONS` | int | `0` | Matches the server's `reserved_connections` (PostgreSQL 16+); used by the Compose budget gate. |
| `PG_FLEET_MIGRATION_CONNECTIONS` | int | `1` | Connections the migration job needs. |
| `PG_FLEET_REQUIRED_CONNECTIONS` | int | `273` | Total connections the whole fleet can open (sum of processes x (pool + overflow) + migrations). Production startup fails if it exceeds the usable capacity. The Compose budget gate (`app.db.compose_budget_gate`) recomputes it and fails when the declared value differs. See [PostgreSQL connection budget](database-connection-budget.md). |

### MongoDB

MongoDB holds raw report documents, execution logs, live execution events, run summaries and decision reports.

| Setting | Type | Default | Description |
|---|---|---|---|
| `MONGO_URI` | str | `mongodb://localhost:27017` | Connection string the application uses, for example `mongodb://testlookup:<password>@mongo:27017/testlookup_logs?authSource=admin`. A placeholder password is a **production check**. **Secret.** |
| `MONGO_DB` | str | `testlookup_logs` | Database name. |
| `MONGO_HOST` | str | `localhost` | **Not read** by the application (it uses `MONGO_URI`); Compose sets it for completeness. |
| `MONGO_PORT` | int | `27017` | **Not read** by the application. |
| `MONGO_MAX_POOL_SIZE` | int | `50` | Driver connection pool maximum. |
| `MONGO_MIN_POOL_SIZE` | int | `5` | Driver connection pool minimum. |
| `MONGO_SOCKET_TIMEOUT_MS` | int | `30000` | Socket timeout. |
| `MONGO_MAX_IDLE_TIME_MS` | int | `60000` | Idle time before a pooled connection is closed. |

## Valkey (Redis protocol) and Celery

TestLookup speaks the Redis protocol; the shipped stacks run Valkey. It is the Celery broker and result backend, the JWT revocation store, the feature-flag and config cache, the live-stream buffer and the rate-limit store. Never expose it unauthenticated.

| Setting | Type | Default | Description |
|---|---|---|---|
| `REDIS_URL` | str | `redis://localhost:6379/0` | Connection URL for application use (cache, revocation, live buffer). Embed the password: `redis://:<password>@redis:6379/0` (the Compose and Kubernetes service keeps the name `redis` while running Valkey). **Secret** when it carries a password. |
| `CELERY_BROKER_URL` | str | `redis://localhost:6379/0` | Celery broker URL. Same form as `REDIS_URL`. **Secret** when it carries a password. |
| `CELERY_RESULT_BACKEND` | str | `redis://localhost:6379/1` | Celery result backend (database 1). |
| `REDIS_PASSWORD` | str | empty | **Not read by the application.** Docker Compose uses it for the server's `--requirepass` and embeds it into the container `REDIS_URL` / `CELERY_*` URLs. When you run the backend outside Compose, put the password in the URLs. **Secret.** |
| `CELERY_WORKER_CONCURRENCY` | int | `4` | Reported on the performance settings page only. The real concurrency is the worker's `--concurrency` flag (Compose: `CELERY_CONCURRENCY`; Kubernetes: the worker Deployment args). |

## Object storage

The backend talks to any S3-compatible endpoint through `aioboto3` (signature v4, region `us-east-1`). The shipped stacks run an S3-compatible server under the service name `minio`; the `MINIO_*` names refer to "the S3 endpoint", whatever implements it.

| Setting | Type | Default | Description |
|---|---|---|---|
| `STORAGE_BACKEND` | `minio` \| `s3` \| `local` | `minio` | `minio` and `s3` both use the S3 client against `MINIO_ENDPOINT`; `local` writes under `LOCAL_STORAGE_PATH` (single-host only). Overridable at `/settings/storage`. |
| `LOCAL_STORAGE_PATH` | str | `/tmp/testlookup_data` | Root directory for `STORAGE_BACKEND=local`. Use a persistent volume. |
| `MINIO_ENDPOINT` | str | `localhost:9000` | `host:port` of the S3 endpoint, without scheme. For AWS S3 use the regional endpoint host with `MINIO_USE_SSL=true`. Overridable at `/settings/storage`. |
| `MINIO_USE_SSL` | bool | `false` | Use `https://` for `MINIO_ENDPOINT`. |
| `MINIO_ACCESS_KEY` | str | `testlookup_minio` | S3 access key. Environment only. **Secret.** |
| `MINIO_SECRET_KEY` | str | empty | S3 secret key. Environment only. **Secret. Required.** |
| `MINIO_BUCKET_NAME` | str | `test-telemetry` | Bucket for uploaded reports, run artifacts and sentinels. The app does not create buckets; the deployment's bucket job does. Other buckets the app writes: `KNOWLEDGE_DOCS_BUCKET`, `FINETUNE_EXPORT_BUCKET`, `pipeline-artifacts`, `compliance-packs`. |
| `S3_MAX_POOL_CONNECTIONS` | int | `50` | botocore connection pool size. |
| `INGESTION_S3_CONCURRENCY` | int | `10` | Parallel object fetches per ingestion run. |

## Ingestion and live streaming

| Setting | Type | Default | Description |
|---|---|---|---|
| `INGEST_RATE_LIMIT_PER_MINUTE` | int | `200` | Live-stream batches accepted per project per minute. `0` disables. |
| `INGEST_EVENT_RATE_LIMIT_PER_MINUTE` | int | `20000` | Single events (`POST /ws/events/{run_id}`) per project per minute. `0` disables. |
| `INGEST_MAX_RESULTS_PER_UPLOAD` | int | `50000` | Most results one uploaded report may carry (the same cap as one JSON batch). Checked before and after parsing. `0` disables. |
| `MAX_ARCHIVE_UNCOMPRESSED_BYTES` | int | `209715200` (200 MiB) | Total decompressed size allowed for an uploaded zip. |
| `MAX_ARCHIVE_ENTRIES` | int | `5000` | Files allowed in an uploaded zip. |
| `MAX_ARCHIVE_ENTRY_BYTES` | int | `52428800` (50 MiB) | Largest single file inside an uploaded zip. |
| `MAX_ARCHIVE_RATIO` | int | `100` | Largest decompressed/compressed ratio (zip-bomb guard). |
| `INGEST_REDIS_MEMORY_THRESHOLD_PCT` | float | `75.0` | Live-stream backpressure: refuse new batches when Valkey/Redis memory passes this percentage of `maxmemory`. Used when `maxmemory` is set. |
| `INGEST_REDIS_MEMORY_ABSOLUTE_BYTES` | int | `0` | The same gate as an absolute byte count, used when `maxmemory` is not set. `0` disables. |
| `LIVE_BUFFER_MAX_EVENTS_PER_RUN` | int | `50000` | Newest events kept in a run's live buffer list. `0` removes the cap. |
| `PERSIST_LIVE_BULK_INSERT_CHUNK` | int | `1000` | Rows per bulk insert when a live session is persisted. |
| `LIVE_INGEST_SHARD_COUNT` | int | `8` | Number of `ingestion.shard.<i>` Celery queues; projects route by hash. `0` = legacy single queue (Compose sets `0`). Changing it remaps projects: follow [Changing the shard count](ingestion-shard-count-change.md) and keep the worker queue list in step. |
| `LIVE_SESSION_DRAIN_ENABLED` | bool | `true` | Persist buffered live events every 30 s during a session, so the buffer cap only evicts events already stored. |
| `LIVE_SESSION_DRAIN_BATCH_SIZE` | int | `5000` | Events drained per run per tick. |
| `HIGH_VOLUME_AUTO_DETECT_ENABLED` | bool | `true` | Flag a project `high_volume` when it sustains the rate below; flagged projects store sampled test rows (aggregates stay exact). |
| `HIGH_VOLUME_TESTS_PER_MINUTE` | int | `1000` | Events per minute that count as high volume. |
| `HIGH_VOLUME_CONSECUTIVE_MINUTES` | int | `3` | Consecutive minutes above the rate before flagging. |
| `HIGH_VOLUME_SAMPLE_EVERY_N` | int | `2` | Store one test row in N for flagged projects. `1` disables sampling. |
| `INGESTION_DLQ_ENABLED` | bool | `true` | Write a dead-letter record when live-session persistence exhausts its retries. See the [DLQ runbook](integrations.md#operator-runbook-dead-letters-outbox-and-backfills). |
| `CANONICAL_DELETION_WINDOW_RUNS` | int | `5` | A canonical test case is marked deleted when it is absent from this many of the project's latest runs. `0` disables deletion detection. |
| `WS_MAX_CONNECTIONS_PER_PROJECT` | int | `500` | Live-view WebSocket connections per project per API process. |
| `WS_MAX_TOTAL_CONNECTIONS` | int | `5000` | Live-view WebSocket connections per API process. |
| `WS_BROADCAST_TIMEOUT` | float | `5.0` | Seconds before a slow WebSocket client is dropped. |

## AI and LLM providers

### Providers, models and offline mode

TestLookup works without any model (`ANALYSIS_MODE=rules`). With a model, it is local by default (Ollama) and `AI_OFFLINE_MODE=true` blocks every remote provider. The provider and model set here are the defaults; `/settings/ai` can override them at runtime.

| `LLM_PROVIDER` | Where it runs | Endpoint setting | Key setting | Allowed while `AI_OFFLINE_MODE=true` |
|---|---|---|---|---|
| `ollama` (default) | local | `OLLAMA_BASE_URL` | none | yes, if the endpoint resolves to a loopback or private address |
| `lmstudio` | local | `LMSTUDIO_BASE_URL` | none | same |
| `localai` | local | `LOCALAI_BASE_URL` | none | same |
| `vllm` | local | `VLLM_BASE_URL` | none | same |
| `openai` | remote | the `/settings/ai` base URL override, else `OPENAI_API_BASE` / `OPENAI_BASE_URL`, else OpenAI | `OPENAI_API_KEY` | no |
| `gemini` | remote | Google | `GOOGLE_API_KEY` | no |
| `anthropic` | remote | Anthropic | `ANTHROPIC_API_KEY` | no |
| `openrouter` | remote | `OPENROUTER_BASE_URL` | `OPENROUTER_API_KEY` | no |

| Setting | Type | Default | Description |
|---|---|---|---|
| `LLM_PROVIDER` | one of the eight above | `ollama` | Provider for analysis, the agent pipeline and Ask AI. |
| `LLM_MODEL` | str | `qwen2.5:7b-instruct` | Model name as the provider spells it. The default is an Apache-2.0 Ollama model (about 4.7 GB at Q4_K_M); pull it into Ollama before use (`ollama pull qwen2.5:7b-instruct`). For hosted providers use their model ID (for example `gpt-4o`). The AI settings page reports whether the model is installed by comparing tags exactly (a name without a tag means `:latest`), so `qwen2.5:7b` and `qwen2.5:7b-instruct` are different models. |
| `LLM_TEMPERATURE` | float | `0.1` | Sampling temperature. Keep it low; analysis wants repeatable answers. |
| `LLM_MAX_TOKENS` | int | `4096` | Output token cap per call. |
| `OLLAMA_BASE_URL` | str | `http://localhost:11434` | Ollama endpoint. Compose sets `http://ollama:11434`. |
| `OLLAMA_NUM_CTX` | int | `8192` | Ollama context window per request. Without it Ollama silently truncates long prompts. Raise for models with larger windows if you have the memory. |
| `LMSTUDIO_BASE_URL` | str | `http://localhost:1234/v1` | LM Studio OpenAI-compatible endpoint. |
| `LOCALAI_BASE_URL` | str | `http://localhost:8080/v1` | LocalAI endpoint. |
| `VLLM_BASE_URL` | str | `http://localhost:8000/v1` | vLLM OpenAI-compatible endpoint. Note the default port clashes with the TestLookup API on the same host. |
| `OPENAI_API_KEY` | str (optional) | unset | OpenAI key; also used by the OpenAI fine-tuning track. **Secret.** |
| `GOOGLE_API_KEY` | str (optional) | unset | Gemini key. **Secret.** |
| `ANTHROPIC_API_KEY` | str (optional) | unset | Anthropic key. **Secret.** |
| `OPENROUTER_API_KEY` | str (optional) | unset | OpenRouter key. **Secret.** |
| `OPENROUTER_BASE_URL` | str | `https://openrouter.ai/api/v1` | OpenRouter endpoint. |
| `OPENROUTER_SITE_URL` | str | `https://github.com/test-intelligence/testlookup` | Sent to OpenRouter as the attribution referer header (`HTTP-Referer`), so usage shows up under that URL on the OpenRouter dashboard. Set it to your own deployment's URL. |
| `OPENROUTER_APP_NAME` | str | `TestLookup` | Sent to OpenRouter as the attribution title header (`X-Title`). |
| `AI_OFFLINE_MODE` | bool | `true` | Egress ceiling. While true: remote LLM providers are refused; a local provider whose endpoint resolves to a public address is refused; ChromaDB's embedder may not download its model weights; outbound webhooks, the one-click Jira issue, defect-promotion tickets, GitHub checks and PR comments, GitLab, the Fixer, Splunk and OpenShift queries and external knowledge syncs are off; Slack/Teams/SMTP may reach only hosts in `OFFLINE_NOTIFICATION_ALLOWED_HOSTS`. `/settings/ai` can turn offline mode on but never off. A few paths do not check it yet; see [Offline mode](integrations.md#offline-mode-and-egress-allow-lists). |
| `AI_LLM_PROVIDER_ALLOWLIST` | str | empty | Optional comma-separated provider IDs (for example `ollama,vllm`). When set, any other provider is refused even if an admin selects it. |
| `AI_LLM_ALLOWED_BASE_URLS` | str | empty | Optional comma-separated origins (`scheme://host[:port]`) an endpoint override may use. When set, a base URL outside the list is refused. |
| `LLM_PRICE_OVERRIDES` | str (JSON, optional) | unset | Per-deployment token prices overriding the built-in table in `services/llm_pricing.py`, keyed `"provider:model-regex"`, USD per million tokens. Example: `{"anthropic:sonnet": {"input_per_mtok": 2.4, "output_per_mtok": 12.0}}`; optional keys `cached_input_per_mtok`, `cache_write_per_mtok`. Overrides win over the table; first match wins. Malformed JSON is logged and ignored. Feeds the cost meter and the [LLM cost budget](feature-flags.md#llm-cost-budget). |
| `EMBEDDING_PROVIDER` | str | `ollama` | The CONFIGURED embedding provider (`default` = ChromaDB's bundled MiniLM, `ollama`, `openai`; overridable on **Settings → AI**). It takes effect only through a re-embed into a new generation of collections — when an administrator changes the embedder there or presses **Re-embed now** — and until that build finishes the embedder in use keeps serving every read and write. A new install's embedder in use is the bundled MiniLM (ONNX, see `CHROMA_ONNX_MODEL_DIR`), whatever this says; the page then reads "… is not in use yet". See [user-guide/ai-features.md](../../user-guide/ai-features.md#changing-the-embedder). |
| `EMBEDDING_MODEL` | str | `nomic-embed-text:v1.5` | The CONFIGURED embedding model (the one `make dev-llm` pulls), applied the same way as `EMBEDDING_PROVIDER`. The model-status panel requires an Ollama embedding model only while it is the embedder in use or the target of a running re-embed. |
| `AI_MAX_RETRIES` | int | `3` | Retries after the first attempt for LLM clients and Jira issue creation. |
| `AI_TIMEOUT_SECONDS` | int | `300` | Per-call timeout for every LLM call (pipeline stages, Ask AI, RAG generation); also how long a call waits for a cluster LLM slot. Overridable at `/settings/ai` (5-1800 s; the floor was 30 s before W04). A pipeline freezes the value in force when it starts: a running or resumed pipeline keeps it, a manual retry after a change starts a new run. An agent may set a lower limit for itself; a stage may cap it lower (the summary stage at 120 s). |
| `LLM_MAX_CONCURRENT_ANALYSES` | int | `3` | Parallel root-cause LLM calls per process. |
| `LLM_CLUSTER_MAX_CONCURRENT` | int | `4` | Cluster-wide concurrent LLM calls per provider, held as Valkey/Redis leases. `0` disables the cluster bound. |
| `LLM_CLUSTER_SLOT_LEASE_SECONDS` | int | `60` | Lease length of a cluster LLM slot. Must be `0` (use 60) or at least 15; a smaller value stops startup. |
| `AI_CONFIDENCE_THRESHOLD` | int | `80` | Minimum confidence (0-100) for automated actions such as auto-ticketing, and the threshold every pipeline stage that routes on confidence uses (triage, gap detection, the per-analysis threshold check). Overridable at `/settings/ai`. A pipeline freezes the value in force when it starts; a manual retry after a change starts a new run. |
| `AI_ANALYSIS_CACHE_TTL` | int | `3600` | Seconds an analysis result stays in the cache. |
| `SEMANTIC_SIMILARITY_THRESHOLD` | float | `0.85` | Minimum cosine similarity for a semantic-cache hit. |
| `PROMPT_OVERHEAD_TOKENS` | int | `1500` | Tokens reserved for system prompt and reasoning when sizing context. |
| `SEMANTIC_CACHE_MAX_DOCUMENTS` | int | `10000` | Cap on documents in the semantic-cache collection. |

### Ask AI chat

| Setting | Type | Default | Description |
|---|---|---|---|
| `CHAT_LLM_MODEL` | str | empty | Optional chat-only model on the same provider as `LLM_PROVIDER` (for example a stronger tool user). Empty uses `LLM_MODEL`. |
| `CHAT_FIRST_TOKEN_TIMEOUT_SECONDS` | int | `45` | Wait for each provider call's first chunk before reporting the provider as hung. |
| `CHAT_TURN_TIMEOUT_SECONDS` | int | `120` | Limit for one whole answer, lookups included. |
| `CHAT_MAX_TOOL_ROUNDS` | int | `4` | Rounds of read-only lookups per answer. |
| `CHAT_HISTORY_MESSAGES` | int | `12` | Earlier messages replayed to the model each turn. |

### Analysis engine, ML classifier and fine-tuning

| Setting | Type | Default | Description |
|---|---|---|---|
| `ANALYSIS_MODE` | `auto` \| `llm` \| `ml` \| `rules` | `auto` | Which engine classifies failures. `auto`: the ML model if trained, else the LLM if the provider saved at `/settings/ai` (or this environment's `LLM_PROVIDER`) is configured and reachable, else rules. Overridable at `/settings/ai`; the stored mode is the truth and the API and every worker obey it. Redis key `config:analysis_mode` only accelerates it: it has no expiry and is re-seeded from the stored value after a Redis restart or flush. An invalid value here counts as `auto`. A direct SQL edit of the stored mode is not seen until the next save at `/settings/ai` (which invalidates the key) or a Redis flush. |
| `ML_MODEL_DIR` | str | `models` | Directory for trained `.joblib` models (a pod-local cache when `ML_MODEL_SYNC_ENABLED` is on). |
| `ML_MODEL_SYNC_ENABLED` | bool | `true` | Publish retrained models to object storage and have every pod pull new versions, so a model trained by the worker ("Train now" or the nightly run) reaches the API. Default `true` since W04 (was `false`; the Kubernetes base always set `true`). Publishing and syncing are best-effort: with the object store unreachable, each pod keeps the models it has and logs a warning. |
| `ML_MODEL_STORE_PREFIX` | str | `ml-models/` | Object-storage prefix for synced models. |
| `ML_MIN_TRAINING_SAMPLES` | int | `200` | Labelled samples needed before the ML engine trains and activates (the nightly run and "Train now" both refuse below it). `GET /api/v1/settings/ai` reports it as `ml_min_training_samples`, so the ML card shows `Samples N / <this>`. |
| `ML_RETRAIN_ENABLED` | bool | `true` | Gates the nightly ML classifier training (Celery beat, 03:10 UTC). A manual "Train now" (`POST /api/v1/settings/ai/ml/retrain`, ADMIN) ignores it. |
| `ML_ACCURACY_THRESHOLD` | float | `0.80` | Minimum accuracy for a newly trained model to be deployed. |
| `ML_PSEUDO_LABEL_CAP` | float | `0.30` | Largest share of the training set that may come from high-confidence LLM labels without human confirmation. |
| `ML_PSEUDO_LABEL_WEIGHT` | float | `0.3` | Sample weight of those pseudo-labels (human labels weigh 1.0). |
| `ML_HUMAN_LABEL_FLOOR` | int | `50` | Below this many human labels the ML engine reports bootstrap mode. |
| `CLASSIFIER_CONFIDENCE_THRESHOLD` | int | `85` | Minimum confidence to trust the fast fine-tuned classifier. |
| `CLASSIFIER_MODEL` | str (optional) | unset | Model for the fast classifier; unset uses `LLM_MODEL`. |
| `FINETUNE_ENABLED` | bool | `false` | Master switch for the fine-tuning tasks: the daily trigger check, the fine-tune queued after the weekly export, and the fine-tuning pipeline itself. Overridable at `/settings/ai` ("Fine-Tuning Pipeline"; the stored value wins in both directions and applies within a minute). The weekly export of verified examples is not gated by it. `GET /api/v1/training/status` reports the effective value and `finetune_enabled_source`. Training itself still needs a GPU or a hosted provider. |
| `FINETUNE_CLASSIFIER_MIN_EXAMPLES` | int | `500` | Verified examples before the classifier track (1) runs. |
| `FINETUNE_REASONING_MIN_EXAMPLES` | int | `2000` | Verified examples before the full reasoning track (2) runs. |
| `FINETUNE_EMBED_MIN_PAIRS` | int | `1000` | Pairs before the embedding track (3) runs. |
| `FINETUNE_INCREMENTAL_TRIGGER` | int | `200` | New verified examples that re-trigger a track. |
| `FINETUNE_EVAL_HOLDOUT` | float | `0.10` | Fraction held out for evaluation. |
| `FINETUNE_MIN_ACCURACY_GAIN` | float | `0.02` | A candidate must beat the current model by this much. |
| `FINETUNE_EXPORT_BUCKET` | str | `training-data` | Bucket for JSONL training exports. |
| `FINETUNE_OPENAI_SUFFIX` | str | `testlookup` | Suffix for OpenAI fine-tune job names. |

### Agent pipeline and investigations

| Setting | Type | Default | Description |
|---|---|---|---|
| `AI_PIPELINE_DEADLINE_SECONDS` | int | `1500` | Wall-clock budget of one agent pipeline run, checked between stages and before each per-test analysis; when it is spent the pipeline publishes a degraded report naming what it skipped. Keep it under the Celery soft limit (1740 s). `0` disables. |
| `AGENT_PIPELINE_ALERT_GRACE_SECONDS` | int | `300` | Grace after the deadline before the overdue-pipeline gauge rises. |
| `AGENT_PIPELINE_MAX_ATTEMPTS` | int | `5` | Default attempts for a failed pipeline run (exponential backoff, same run id). |
| `AGENT_MAX_ATTEMPTS_CEILING` | int | `10` | Ceiling no project or run may exceed. |
| `AGENT_MAX_TIMEOUT_CEILING` | int | `600` | Highest per-agent timeout a project may configure. |
| `AGENT_RETRY_BASE_SECONDS` | int | `30` | Base delay of the retry backoff. |
| `AGENT_RETRY_CAP_SECONDS` | int | `600` | Largest retry delay. |
| `AGENT_INVOKE_SYNC_CONCURRENCY` | int | `4` | `mode=sync` agent invocations waiting at once per API process; a full pool answers 503 with `Retry-After`. |
| `AGENT_INVOKE_SYNC_WAIT_SECONDS` | int | `25` | How long a `mode=sync` invocation waits before answering 202. |
| `AGENT_INVOKE_STREAM_TICKET_SECONDS` | int | `60` | Lifetime of a single-use invocation event-stream ticket. |
| `REVIEW_GATE_ENFORCED` | bool | `false` | Human review gate for AI report distribution. Off: nothing is refused; each would-be refusal is recorded in `access_audit_logs`. On: an unreviewed AI report is refused (409) for PDF export and share links unless the project allows unreviewed distribution (watermarked, audited), and the release gate and `release.decided` webhooks read `PENDING_REVIEW`. Existing runs have no accepted reviews, which is why it is off by default. |
| `AIQ_GAP_REFINEMENT_ENABLED` | bool | `false` | Environment fallback for the `aiq_gap_refinement` feature flag (the `gap_detection` and `report_refinement` deep-pipeline stages), used only while no flag row exists. Migration 0209 seeds the row ON for every install (or with this variable's value when it is set), so after the upgrade the flag at `/settings/feature-flags` decides, per project. See [Feature flags](feature-flags.md#environment-fallback). |
| `AIQ_CONTRACT_VALIDATION_ENABLED` | bool | `false` | Environment fallback for the `contract_validation` feature flag, used only while no flag row exists. See [Feature flags](feature-flags.md#environment-fallback). |
| `AIQ_CHANGE_OWNERSHIP_ENABLED` | bool | `false` | Environment fallback for the `change_ownership` flag (same rule). |
| `AIQ_ASYNC_DECISION_REPORT_SUPERSESSION_ENABLED` | bool | `false` | Environment fallback for the `async_decision_report_supersession` flag (same rule). |
| `AI_REPORT_EVAL_ALLOW_CALLER_CORPUS` | bool | `false` | Lets report-level AI evaluation accept a caller-supplied corpus instead of published, tenant-authorised reports. For test fixtures only. |
| `DEEP_INVESTIGATION_ENABLED` | bool | `true` | Allows deep investigations. Overridable at `/settings/ai` ("Deep Investigation"; the stored value wins in both directions and applies within a minute). Obeyed by `POST /api/v1/deep-investigate/{run_id}`, the bulk trigger with `workflow_type: "deep"`, `POST /api/v1/agents/pipelines/trigger` with a deep workflow, and a manual retry that starts a new deep run; each answers 422 `Deep investigation is disabled in Settings › AI. Ask an administrator to enable it.` when it is off (and `... requires LLM or Auto mode. Current mode: rules.` in Rules mode). Resuming a failed deep run is not refused. `GET /api/v1/settings/ai/mode` (any signed-in role) reports the effective value as `deep_investigation_enabled`. |
| `RELEASE_PASS_RATE_THRESHOLD` | float | `90.0` | Pass rate below which the release-risk agent, regression diff and policy evaluator treat a run as not GO-worthy. |
| `AGENT_MEMORY_RETENTION_DAYS` | int | `365` | Expiry of agent memory entries (clamped to 1-3650 days). |
| `COMMIT_RANGE_FILE_FETCH_LIMIT` | int | `25` | Commits in a resolved range whose changed files are fetched from GitHub (one API call each, capped at 100). Raise it when building a test-impact corpus. |
| `GITHUB_TOKEN` | str (optional) | unset | Instance-wide GitHub token, used only by the deployment-wide GitHub row of Integration health (`api.github.com/rate_limit`); a token saved at `/settings/integrations` comes first. Since W10 no commit lookup reads it: the run Changes tab, the regression diff and the build-changes agent tool list commits through each project's own GitHub integration (`/settings/github`), offline-gated. **Secret.** |
| `GITHUB_REPO` | str (optional) | unset | Accepted for compatibility, not used (it named the repository of the former instance-wide commit lookups). |
| `PROMETHEUS_URL` | str (optional) | unset | Prometheus base URL (for example `http://prometheus:9090`) for the agents' application-metrics tool and, when set, the observed latency on Settings › Performance. Unset disables the tool; Performance then measures in-app. |
| `SLOW_QUERY_MS` | int | `500` | Statements at least this slow (ms) are captured, as their shape only (no values), for Settings › Performance › Slow queries. |

#### Anomaly detection

| Setting | Type | Default | Description |
|---|---|---|---|
| `ANOMALY_REGRESSION_THRESHOLD` | float | `10.0` | Pass-rate drop (percentage points) against the median baseline that flags a regression. |
| `ANOMALY_MIN_HISTORY_RUNS` | int | `3` | Prior runs needed before the regression check runs. |
| `ANOMALY_FLAKY_WINDOW_RUNS` | int | `10` | History entries examined per test fingerprint for flakiness. |
| `ANOMALY_FLAKY_LOOKBACK_DAYS` | int | `30` | History window for flakiness. |
| `ANOMALY_PERF_HISTORY_DAYS` | int | `30` | History window for duration baselines. |
| `ANOMALY_MIN_PERF_SAMPLES` | int | `5` | Samples needed before a duration spike is flagged. |
| `ANOMALY_PERF_SPIKE_MULTIPLIER` | float | `2.0` | A duration must exceed this multiple of the median. |
| `ANOMALY_PERF_MIN_ABSOLUTE_DELTA_MS` | int | `1000` | ...and grow by at least this many milliseconds. |
| `ANOMALY_SUMMARY_MAX_ITEMS` | int | `8` | Anomalies included in the LLM summary prompt. |

#### Risk scoring weights

The release-risk composite is the sum of seven 0-100 dimension scores times these weights. Keep the sum at 1.0.

| Setting | Type | Default |
|---|---|---|
| `RISK_WEIGHT_USER_IMPACT` | float | `0.25` |
| `RISK_WEIGHT_ENV_SENSITIVITY` | float | `0.10` |
| `RISK_WEIGHT_REPRODUCIBILITY` | float | `0.15` |
| `RISK_WEIGHT_REGRESSION_LIKELY` | float | `0.20` |
| `RISK_WEIGHT_HIST_RECURRENCE` | float | `0.10` |
| `RISK_WEIGHT_BLAST_RADIUS` | float | `0.15` |
| `RISK_WEIGHT_DIAGNOSIS_CONF` | float | `0.05` |

## Knowledge, RAG and search

Knowledge-grounded test generation is gated by the `knowledge_rag` [feature flag](feature-flags.md); `KNOWLEDGE_RAG_ENABLED` only seeds that flag.

| Setting | Type | Default | Description |
|---|---|---|---|
| `CHROMA_HOST` | str | `localhost` | ChromaDB host (vector store for semantic search, duplicates, the semantic cache and knowledge chunks). Overridable at `/settings/storage`. |
| `CHROMA_PORT` | int | `8001` | ChromaDB port. Compose sets `8000` (the in-network port). |
| `CHROMA_COLLECTION` | str | `testlookup_embeddings` | The **Collection prefix** (**Settings → Data & storage**): up to 8 lower-case letters, digits or `-`, starting with a letter or digit, put in front of every vector collection's name (`demo_test_case_search`, …) so two deployments can share one ChromaDB. `""` means no prefix, and so do the old default `testlookup_embeddings` and any stored value that is not a valid prefix (the page says so). A change re-embeds every index into a new generation of collections; reads keep the current ones until the build finishes. |
| `CHROMA_ONNX_MODEL_DIR` | str | empty | Where a side-loaded copy of ChromaDB's ONNX embedding model lives. ChromaDB otherwise downloads about 79 MB on first use, which `AI_OFFLINE_MODE` forbids; point this at a pre-loaded directory in sealed installs. The Kubernetes base mounts `/var/cache/testlookup/chroma-onnx`. |
| `KNOWLEDGE_RAG_ENABLED` | bool | `false` | Seeds the `knowledge_rag` flag row when migration 0062 runs (the value of this variable at migration time), and is the flag's environment fallback while no row exists. After the migration, use `/settings/ai` or the feature-flag API. |
| `KNOWLEDGE_SYNC_TIMEOUT_SECONDS` | int | `60` | HTTP timeout for the Jira, Confluence and URL knowledge connectors. |
| `KNOWLEDGE_MAX_SOURCES_PER_PROJECT` | int | `100` | Knowledge sources per project. |
| `KNOWLEDGE_DOCS_BUCKET` | str | `knowledge-docs` | Bucket for uploaded knowledge documents. |
| `KNOWLEDGE_STALE_THRESHOLD_JIRA_HOURS` | int | `24` | Age after which a Jira source is re-synced. |
| `KNOWLEDGE_STALE_THRESHOLD_URL_HOURS` | int | `168` | Age after which a URL source is re-synced. |
| `KNOWLEDGE_CHUNK_TARGET_TOKENS` | int | `400` | Target chunk size. |
| `KNOWLEDGE_CHUNK_MAX_TOKENS` | int | `800` | Largest chunk. |
| `KNOWLEDGE_CHUNK_OVERLAP_TOKENS` | int | `50` | Overlap between chunks. |
| `KNOWLEDGE_RESYNC_BATCH_CAP` | int | `50` | Sources re-synced per run of the four-hourly resync task. |
| `KNOWLEDGE_SYNC_STUCK_MINUTES` | int | `45` | A source left in `SYNCING` this long is failed by the reaper. Keep it above the Celery hard limit (31 min). |
| `CONFLUENCE_ENABLED` | bool | `false` | **Not read.** Confluence sources are available whenever credentials resolve. |
| `CONFLUENCE_DOMAIN` | str (optional) | unset | Confluence host (for example `example.atlassian.net`). Falls back to `JIRA_DOMAIN`. |
| `CONFLUENCE_EMAIL` | str (optional) | unset | Confluence account email. Falls back to `JIRA_EMAIL`. |
| `CONFLUENCE_API_TOKEN` | str (optional) | unset | Confluence API token. Falls back to `JIRA_API_TOKEN`. **Secret.** |
| `SEARCH_INDEX_BATCH_SIZE` | int | `200` | Documents per ChromaDB upsert when indexing for search. |
| `SEARCH_INDEX_INCREMENTAL_LIMIT` | int | `5000` | Documents per incremental re-index (hourly beat task). |
| `SEARCH_QUERY_TIMEOUT_MS` | int | `5000` | Reported by the performance settings page; not enforced by the search code today. |
| `SEARCH_MAX_RESULTS` | int | `200` | Reported by the performance settings page; not enforced by the search code today. |

## Notifications and integrations

Setup steps for each integration are in [Integrations](integrations.md). Values below are environment defaults; `/settings/integrations` and `/settings/notifications` override most of them.

| Setting | Type | Default | Description |
|---|---|---|---|
| `JIRA_ENABLED` | bool | `false` | Enables Jira issue creation. |
| `JIRA_DOMAIN` | str (optional) | unset | Jira Cloud host, for example `example.atlassian.net` (no scheme). |
| `JIRA_EMAIL` | str (optional) | unset | Account email for Jira basic auth. |
| `JIRA_API_TOKEN` | str (optional) | unset | Jira API token for that account. **Secret.** |
| `JIRA_DEFAULT_PROJECT_KEY` | str | `QA` | Jira project that issues are filed into when the request names none. |
| `JIRA_WEBHOOK_SECRET` | str (optional) | unset | Secret configured on the Jira resolution webhook; Jira signs each delivery with it (`X-Hub-Signature: sha256=<hex>`). While unset or a placeholder, `POST /api/v1/feedback/jira-webhook` answers 403 to every delivery. Environment only. **Secret.** Production warning when Jira is on and this is unset. |
| `SPLUNK_ENABLED` | bool | `false` | Enables the Splunk log-correlation tools and look-ups of the AI analysis. A saved Enabled switch at `/settings/integrations` wins. |
| `SPLUNK_BASE_URL` | str (optional) | unset | Splunk management URL, for example `https://splunk.example.com:8089`. A saved Base URL wins. A loopback, link-local or unspecified host is refused at use (and at save in the UI). |
| `SPLUNK_API_TOKEN` | str (optional) | unset | Splunk token. A saved token wins. **Secret.** |
| `SPLUNK_INDEX` | str | `main` | Index searched when the project has no Splunk Index of its own (Edit Project). Checked at use: letters, digits, `_` and `-`, 1-80 characters; another value refuses the search. |
| `OCP_ENABLED` | bool | `false` | Enables OpenShift/Kubernetes event correlation (pod events around a failure). A saved Enabled switch wins. |
| `OCP_API_URL` | str (optional) | unset | Cluster API URL, for example `https://api.cluster.example.com:6443`. A saved API URL wins; the egress check applies as for Splunk. |
| `OCP_SA_TOKEN` | str (optional) | unset | Service-account bearer token with read access to events and pods. A saved token wins. **Secret.** |
| `OCP_DEFAULT_NAMESPACE` | str | `qa-testing` | Namespace queried when a run names none, after the project's OCP Namespace and the saved Default Namespace. |
| `SLACK_ENABLED` | bool | `false` | Enables the deployment-wide Slack channel. |
| `SLACK_WEBHOOK_URL` | str (optional) | unset | Slack incoming-webhook URL (the delivery path). **Secret.** |
| `SLACK_BOT_TOKEN` | str (optional) | unset | Slack bot token. Only the integration health probe uses it (`auth.test`); messages are sent through the webhook. **Secret.** |
| `SLACK_DEFAULT_CHANNEL` | str | `#qa-alerts` | Accepted for compatibility, not used: an incoming webhook posts to the channel it was created for (route other channels per team in Team channels). The settings read still returns it, marked deprecated. |
| `TEAMS_ENABLED` | bool | `false` | Enables the deployment-wide Microsoft Teams channel. |
| `TEAMS_WEBHOOK_URL` | str (optional) | unset | Teams incoming-webhook URL. **Secret.** |
| `SMTP_ENABLED` | bool | `false` | Enables email (notifications, digests, report attachments). |
| `SMTP_HOST` | str | `localhost` | SMTP relay host. |
| `SMTP_PORT` | int | `587` | SMTP port. Must agree with `SMTP_TLS`: `465` = implicit TLS, `587` = STARTTLS. |
| `SMTP_USER` | str (optional) | unset | SMTP username (omit for an unauthenticated relay). |
| `SMTP_PASSWORD` | str (optional) | unset | SMTP password. **Secret.** |
| `SMTP_FROM` | str | `noreply@testlookup.io` | Sender address. Set one your relay is allowed to send as. |
| `SMTP_TLS` | bool | `true` | `true` = implicit TLS (port 465); `false` = STARTTLS required (port 587). Plain unencrypted SMTP is not supported. **The defaults pair implicit TLS with port 587, which fails**: with port 587 set `SMTP_TLS=false` (as `.env.example` does), or use port 465. |
| `OFFLINE_NOTIFICATION_ALLOWED_HOSTS` | str | empty | Off-box hosts the deployment's own Slack/Teams webhooks and SMTP relay may still reach while `AI_OFFLINE_MODE` is on. Comma-separated; `.example.com` matches subdomains. Per-user and per-team webhooks never use this list. Empty: every off-box destination is refused. |
| `OFFLINE_EMAIL_ALLOWED_RECIPIENT_DOMAINS` | str | empty | Recipient domains email may reach while offline (`corp.example` exactly, `.corp.example` subdomains). Set: every recipient must match. Empty: an on-box relay may deliver; an off-box relay admitted by the host list is refused. |
| `WEBHOOK_PRIVATE_ALLOWED_HOSTS` | str | empty | Private-network hosts a Slack or Teams webhook may reach while online (exact host names, no wildcards), for example an on-premises Mattermost. Online webhooks otherwise refuse private addresses (SSRF guard). Name a chat server only: any webhook may then target it. Does not apply to [outbound webhook subscriptions](integrations.md#outbound-webhooks) (see the next row). |
| `OUTBOUND_WEBHOOK_PRIVATE_ALLOWED_HOSTS` | str | empty | Private-network or loopback hosts an [outbound webhook subscription](integrations.md#outbound-webhooks) may target (comma-separated, exact host names, case and a trailing dot aside, no wildcards or ranges). A listed host skips the public-only check at create, update and delivery and is reached through the shared client (`HTTP_CA_BUNDLE` honoured). Any QA lead of any project may then point a subscription at it: name a test receiver or a trusted internal endpoint only. Separate from `WEBHOOK_PRIVATE_ALLOWED_HOSTS` (chat servers). Offline mode still refuses every delivery. Empty: none. |

## Observability and logging

| Setting | Type | Default | Description |
|---|---|---|---|
| `OTEL_ENABLED` | bool | `true` | OpenTelemetry tracing for the API. |
| `OTEL_SERVICE_NAME` | str | `testlookup` | Service name on spans. |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | str (optional) | unset | OTLP/HTTP collector, for example `http://otel-collector:4318`. Unset: spans go to stdout in development only; elsewhere nothing is exported and one warning is logged. |
| `METRICS_ENABLED` | bool | `true` | Exposes Prometheus metrics at `/metrics` on the API. Worker metrics are separate (see `PROMETHEUS_MULTIPROC_DIR` below). |
| `LOG_LEVEL` | str | `INFO` | `DEBUG`, `INFO`, `WARNING` or `ERROR`. |
| `LOG_FORMAT` | `json` \| `text` | `json` | Structured JSON for log shippers; `text` for reading in a terminal. |

## Retention

Data retention (runs, results, artifacts) is configured per project at `/settings/retention`, with instance-wide defaults a project inherits field by field; each project is purged once a day in its purge-window hour (02:00 UTC unless set otherwise) and can be paused. It is not an environment setting. See the administration guide's "Data retention & purge" section in [user-guide/administration.md](../../user-guide/administration.md). The retention settings in the environment are `AGENT_MEMORY_RETENTION_DAYS` ([Agent pipeline](#agent-pipeline-and-investigations)) and the two limits on a deletion archive:

| Variable | Type | Default | Purpose |
|---|---|---|---|
| `RETENTION_EXPORT_MAX_BYTES` | int | `536870912` | Byte cap of a deletion archive (512 MiB; ≥ 1 MiB): checked at preview, at execute and while writing. |
| `RETENTION_EXPORT_COLLECT_TTL_HOURS` | int | `72` | Hours an administrator has to download and confirm a "collect" deletion archive (1-720); after that the job expires and nothing is deleted. |

## Workers and reports

| Setting | Type | Default | Description |
|---|---|---|---|
| `REPORT_EXPORT_SYNC_MAX_TESTS` | int | `200000` | A Summary Report export whose window holds more tests than this renders in the background instead of in the request. |
| `CELERY_WORKER_CONCURRENCY` | int | `4` | See [Valkey and Celery](#valkey-redis-protocol-and-celery). |
| `LIVE_INGEST_SHARD_COUNT` | int | `8` | See [Ingestion](#ingestion-and-live-streaming). |

## Environment variables read outside `Settings`

These are read with `os.getenv` / `os.environ` and do not appear in the generated table.

| Variable | Read by | Default | Description |
|---|---|---|---|
| `MIGRATION_LOCK_WAIT_SECONDS` | `backend/app/db/migration_lock.py` | `1800` | How long `alembic upgrade head` waits for the migration advisory lock held by another migrator before failing. |
| `KUBERNETES_NAMESPACE` | `backend/app/services/model_status_service.py` | `testlookup` | Namespace used in the `kubectl -n <ns> exec deploy/testlookup-ollama -- ollama pull <model>` hint the AI settings page shows when running in Kubernetes. Cosmetic. |
| `OPENAI_API_BASE`, `OPENAI_BASE_URL` | `backend/app/services/llm_factory.py` | unset | OpenAI-compatible base URL for `LLM_PROVIDER=openai` when `/settings/ai` sets no base URL (the OpenAI client library also reads them). |
| `SDK_PATH` | `backend/app/routers/sdk.py` | `/app/client_sdks` | Directory served by the public SDK download endpoints `GET /api/v1/sdk` and `/api/v1/sdk/{lang}`. Compose mounts `./client` there. |
| `PROMETHEUS_MULTIPROC_DIR` | `prometheus_client` (API under gunicorn), `backend/app/worker/celery_app.py` | `/tmp/prometheus-multiproc` (set in the production image) | prometheus_client multiprocess directory; the image's start command clears stale files from it. When set on a Celery worker, the worker parent serves aggregated metrics on `WORKER_METRICS_PORT`. Must be a writable, per-container directory. |
| `WORKER_METRICS_PORT` | `backend/app/worker/celery_app.py` | `9100` | Port of the worker metrics endpoint. |
| `WORKER_METRICS_MAX_FILES` | same | `256` | When the multiprocess directory holds more metric files than this, the worker waits for a final scrape and then exits gracefully so the orchestrator restarts it. |
| `WORKER_METRICS_FILE_CHECK_SECONDS` | same | `60` | How often that file count is checked. |
| `WORKER_METRICS_FINAL_SCRAPE_GRACE_SECONDS` | same | `65` | Wait before that graceful exit, so two scrapes at the shipped 30 s interval still see the counters. |
| `KNOWLEDGE_RAG_ENABLED`, `AIQ_CONTRACT_VALIDATION_ENABLED`, `AIQ_CHANGE_OWNERSHIP_ENABLED`, `AIQ_ASYNC_DECISION_REPORT_SUPERSESSION_ENABLED` | `backend/app/services/feature_flags.py` | unset | Also read directly from the process environment as feature-flag fallbacks (only `1`, `true`, `yes`, `on` count as on). A value that exists only in a `.env` file is not in the process environment unless something (Compose `env_file`, Kubernetes `envFrom`) exported it. |
| `ANONYMIZED_TELEMETRY` | set by `backend/app/core/config.py` | `False` | Set by the application (if absent) to keep ChromaDB's client telemetry off. Not an operator setting. |
| `RAGAS_DO_NOT_TRACK` | set by `backend/app/services/rag_faithfulness_service.py` | `true` | Set to `true` (always, overriding any value) before the Ragas faithfulness evaluator imports `ragas`, so Ragas sends no usage telemetry to its vendor. Not an operator setting; only an image built with `INSTALL_RAGAS=1` has Ragas. |
| `DATABASE_URL`, `POSTGRES_*` | `backend/migrations/env.py` | see [PostgreSQL](#postgresql) | Alembic uses `DATABASE_URL`, else builds a URL from the `POSTGRES_*` variables. |
| `ADMIN_EMAIL`, `ADMIN_USERNAME`, `ADMIN_FULL_NAME`, `ADMIN_PASSWORD`, `ADMIN_PASSWORD_GENERATED` | `backend/scripts/create_admin.py` | `admin@example.com`, `admin`, `TestLookup Admin`, unset | Inputs to the first-admin bootstrap script (idempotent: it changes nothing if the user exists). Without `ADMIN_PASSWORD` it generates a random password, prints it once and forces a change at first sign-in. Pass a password through the environment, never on a command line. |
| `MFA_BREAKGLASS_OPERATOR` | `backend/scripts/mfa_breakglass.py` | empty | Default for the script's operator-name option, recorded in the breakglass identity event. |

The MCP server (`TESTLOOKUP_API_URL`, `TESTLOOKUP_USERNAME`, `TESTLOOKUP_PASSWORD`, ...), the CLI (`TESTLOOKUP_URL`, `TESTLOOKUP_API_KEY`, `TESTLOOKUP_PROFILE`) and the reporters (`TESTLOOKUP_ENDPOINT`, `TESTLOOKUP_API_KEY`, ...) have their own variables: see [mcp/README.md](../../mcp/README.md), [cli/README.md](../../cli/README.md) and [client/README.md](../../client/README.md).

## Frontend and Docker Compose

### Frontend

| Variable | When | Default | Description |
|---|---|---|---|
| `VITE_API_BASE_URL` | build time (`frontend/Dockerfile` build argument) | empty | API origin baked into the SPA. Empty uses same-origin relative URLs, which is right whenever the frontend's nginx (or your ingress) proxies `/api`, `/ws` and `/webhooks` to the backend. |
| `VITE_API_URL` | build time | unset | Legacy alias of `VITE_API_BASE_URL`, read only by the error reporter. |
| `VITE_WS_URL` | build time | unset | WebSocket origin for the live view; defaults to `VITE_API_BASE_URL` with a `ws`/`wss` scheme, else the page origin. |
| `VITE_PROXY_TARGET` | Vite dev server only | `http://localhost:8000` | Backend the dev server proxies to. |
| `BACKEND_UPSTREAM` | container start (nginx template) | `backend:8000` | `host:port` the frontend container's nginx proxies `/api/`, `/ws/` and `/webhooks/` to. Set `testlookup-backend:8000` (or your service name) in Kubernetes. |
| `INSTALL_EXTRA_CA` | build time (backend and frontend Dockerfiles) | `0` | `1` trusts the `.crt` files placed in `backend/certs/` or `frontend/certs/` during the image build (for builds behind a TLS-intercepting proxy). Does not affect the running app; use `HTTP_CA_BUNDLE` for that. |

### Keys in `.env.example` that only Docker Compose reads

The application ignores these; Compose interpolates them into service definitions.

| Key | Used by | Purpose |
|---|---|---|
| `MONGO_USER`, `MONGO_PASSWORD` | `docker-compose.yml`, `docker-compose.release.yml` | MongoDB root user, and the credentials Compose embeds in the containers' `MONGO_URI`. `MONGO_PASSWORD` is required. **Secret.** |
| `FLOWER_USER`, `FLOWER_PASSWORD` | `docker-compose.yml` (`flower`) | Basic auth for the Celery Flower UI. `FLOWER_PASSWORD` is required. **Secret.** |
| `GF_SECURITY_ADMIN_PASSWORD` | `docker-compose.monitoring.yml` | Grafana admin password for the monitoring overlay. **Secret.** |
| `PG_API_POOL_SIZE`, `PG_API_MAX_OVERFLOW` | Compose files | Become `PG_POOL_SIZE` / `PG_MAX_OVERFLOW` for the backend container and feed the budget gate. |
| `PG_WORKER_POOL_SIZE`, `PG_WORKER_MAX_OVERFLOW` | Compose files | The same for the worker containers. |
| `INITIAL_ADMIN_PASSWORD` | `docker-compose.release.yml` (`db-migrate` service, passed to `create_admin.py` as `ADMIN_PASSWORD`) | Password of the first `admin` account, used once when the release stack migrates a new database; `install.sh` / `gen-dev-env.sh` write a random value. Empty: a random password is generated and printed once in the `db-migrate` logs. The account must change it at first sign-in; an existing admin is never modified. **Secret.** |
| `E2E_ADMIN_PASSWORD` | Playwright end-to-end tests (`frontend/tests/e2e`) | Admin password the tests sign in with when dev login is disabled. Not used by any service. **Secret.** |

Compose also reads these variables, which `.env.example` does not list:

| Variable | Default | Purpose |
|---|---|---|
| `CELERY_CONCURRENCY` | `4` | `--concurrency` of the main worker (and the budget gate's arithmetic). |
| `CELERY_CHILDREN_CONCURRENCY` | `1` | Concurrency of the agent-children worker in `docker-compose.release.yml`. |
| `PG_COMPOSE_REQUIRED_CONNECTIONS` | `14` (`docker-compose.yml`), `23` (`docker-compose.release.yml`) | The `PG_FLEET_REQUIRED_CONNECTIONS` value Compose gives its services; must equal what the budget gate computes. |
| `BACKEND_PUBLISH_ADDR` | `127.0.0.1` | Host address the release Compose publishes the API port on. |
| `TESTLOOKUP_IMAGE`, `TESTLOOKUP_VERSION` | see `docker-compose.release.yml` | Image prefix and tag for the no-build release stack. |
| `REGISTRY`, `TESTLOOKUP_REGISTRY` | see `docker-compose.airgap.yml` | Registry the air-gapped override pulls from. |

The PostgreSQL, Valkey and S3 server containers take their credentials from `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_DB`, `REDIS_PASSWORD` and `MINIO_ACCESS_KEY` / `MINIO_SECRET_KEY` in the same `.env`.

## Common recipes

- **Generate the secrets** for a new deployment: `openssl rand -hex 32` for `JWT_SECRET_KEY`, `APP_SECRET_KEY` and `WEBHOOK_SECRET`; `openssl rand -hex 24` for `REDIS_PASSWORD`; strong random values for `POSTGRES_PASSWORD`, `MONGO_PASSWORD`, `MINIO_SECRET_KEY` and `FLOWER_PASSWORD`. `make dev` / `make quickstart` generate a development `.env` for you (`scripts/gen-dev-env.sh`).
- **Run fully offline**: keep `AI_OFFLINE_MODE=true`, `LLM_PROVIDER=ollama`, pull the model into Ollama, set `CHROMA_ONNX_MODEL_DIR` to a pre-loaded embedder, and list any internal chat/SMTP hosts in `OFFLINE_NOTIFICATION_ALLOWED_HOSTS`. See [user-guide/air-gapped-install.md](../../user-guide/air-gapped-install.md) and [user-guide/offline-model-pack.md](../../user-guide/offline-model-pack.md).
- **Use a hosted model**: set `AI_OFFLINE_MODE=false`, `LLM_PROVIDER` and the matching API key, optionally lock it down with `AI_LLM_PROVIDER_ALLOWLIST`, and set `LLM_PRICE_OVERRIDES` if your contract prices differ from list prices.
- **Behind a reverse proxy**: set `PUBLIC_BASE_URL`, `CORS_ORIGINS` and `TRUSTED_PROXY_IPS`; with SAML also `SAML_BASE_URL` (the ACS URL the IdP posts to). `PUBLIC_BASE_URL` is where the browser lands after SAML sign-in.
- **Behind a TLS-intercepting proxy**: mount the corporate CA bundle and set `HTTP_CA_BUNDLE` to its path.
